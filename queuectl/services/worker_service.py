from __future__ import annotations

import multiprocessing
import os
import signal
import socket
import time
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy.orm import Session, sessionmaker

from queuectl.core.executor import execute_command
from queuectl.core.models import WorkerStatus
from queuectl.storage.database import create_engine_for_url, create_session_factory, init_db
from queuectl.storage.repositories import ConfigRepository, JobRepository, WorkerRepository

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class WorkerStartResult:
    worker_ids: list[str]
    process_ids: list[int]


class WorkerRuntime:
    def __init__(self, session_factory: sessionmaker[Session], worker_id: str | None = None) -> None:
        self.session_factory = session_factory
        self.worker_id = worker_id or self._new_worker_id()
        self.jobs = JobRepository(session_factory)
        self.workers = WorkerRepository(session_factory)
        self.config = ConfigRepository(session_factory)
        self._registered = False
        self._shutdown_requested = False

    @staticmethod
    def _new_worker_id() -> str:
        host = socket.gethostname().split(".")[0]
        return f"{host}-{os.getpid()}-{uuid.uuid4().hex[:8]}"

    def register(self) -> None:
        if self._registered:
            return
        self.config.ensure_defaults()
        self.workers.register(self.worker_id, pid=os.getpid(), hostname=socket.gethostname())
        self._registered = True
        logger.info("worker_registered", worker_id=self.worker_id)

    def request_shutdown(self, *_args: object) -> None:
        self._shutdown_requested = True
        logger.info("worker_shutdown_requested", worker_id=self.worker_id)

    def run_once(self) -> bool:
        self.register()
        runtime = self.config.runtime()

        if self._shutdown_requested or self.workers.should_stop(self.worker_id):
            self.workers.heartbeat(self.worker_id, WorkerStatus.STOPPING)
            return False

        self.workers.heartbeat(self.worker_id, WorkerStatus.IDLE)
        job = self.jobs.claim_next(self.worker_id)
        if job is None:
            return False

        self.workers.heartbeat(self.worker_id, WorkerStatus.BUSY, current_job_id=job.id)
        logger.info("job_started", worker_id=self.worker_id, job_id=job.id, command=job.command)
        result = execute_command(job.command, timeout=job.timeout)

        if result.exit_code == 0:
            self.jobs.complete(job.id, result)
            logger.info("job_completed", worker_id=self.worker_id, job_id=job.id)
        else:
            failed_job = self.jobs.fail(job.id, result, backoff_base=runtime.backoff_base)
            logger.warning(
                "job_failed",
                worker_id=self.worker_id,
                job_id=job.id,
                state=failed_job.state,
                exit_code=result.exit_code,
            )

        self.workers.heartbeat(self.worker_id, WorkerStatus.IDLE)
        return True

    def drain(self, max_jobs: int | None = None) -> int:
        completed = 0
        while max_jobs is None or completed < max_jobs:
            if not self.run_once():
                break
            completed += 1
        return completed

    def run_forever(self) -> None:
        self.register()
        signal.signal(signal.SIGTERM, self.request_shutdown)
        if hasattr(signal, "SIGINT"):
            signal.signal(signal.SIGINT, self.request_shutdown)

        try:
            while not self._shutdown_requested and not self.workers.should_stop(self.worker_id):
                runtime = self.config.runtime()
                did_work = self.run_once()
                if not did_work:
                    self.workers.heartbeat(self.worker_id, WorkerStatus.IDLE)
                    time.sleep(runtime.poll_interval)
        finally:
            self.workers.mark_stopped(self.worker_id)
            logger.info("worker_stopped", worker_id=self.worker_id)


def run_worker_process(database_url: str, worker_id: str | None = None) -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),
    )
    engine = create_engine_for_url(database_url)
    init_db(engine)
    session_factory = create_session_factory(engine)
    WorkerRuntime(session_factory, worker_id=worker_id).run_forever()


class WorkerService:
    def __init__(self, session_factory: sessionmaker[Session], database_url: str) -> None:
        self.session_factory = session_factory
        self.database_url = database_url
        self.workers = WorkerRepository(session_factory)
        self.config = ConfigRepository(session_factory)
        self.config.ensure_defaults()

    def start(self, count: int) -> WorkerStartResult:
        multiprocessing.freeze_support()
        ctx = multiprocessing.get_context("spawn")
        processes: list[multiprocessing.Process] = []
        worker_ids: list[str] = []
        for _ in range(count):
            worker_id = WorkerRuntime._new_worker_id()
            process = ctx.Process(target=run_worker_process, args=(self.database_url, worker_id), daemon=False)
            process.start()
            processes.append(process)
            worker_ids.append(worker_id)

        try:
            while any(process.is_alive() for process in processes):
                time.sleep(0.5)
        except KeyboardInterrupt:
            self.stop_all()
        finally:
            for process in processes:
                process.join()

        return WorkerStartResult(
            worker_ids=worker_ids,
            process_ids=[process.pid for process in processes if process.pid is not None],
        )

    def stop_all(self) -> int:
        return self.workers.request_stop_all()
