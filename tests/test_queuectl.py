from __future__ import annotations

import concurrent.futures
import sys

from queuectl.core.models import JobCreate, JobState
from queuectl.services.job_service import JobService
from queuectl.services.worker_service import WorkerRuntime
from queuectl.storage.database import create_engine_for_url, create_session_factory, init_db

from tests.conftest import shell_command


def run_worker_until_idle(session_factory, worker_id: str) -> int:
    worker = WorkerRuntime(session_factory, worker_id=worker_id)
    try:
        return worker.drain()
    finally:
        worker.workers.mark_stopped(worker.worker_id)


def test_successful_job_execution(session_factory) -> None:
    service = JobService(session_factory)
    service.enqueue(
        JobCreate(
            id="job-success",
            command=shell_command(sys.executable, "-c", "print('hello from queuectl')"),
        )
    )

    processed = run_worker_until_idle(session_factory, "worker-success")

    job = service.get("job-success")
    assert processed == 1
    assert job.state == JobState.COMPLETED
    assert job.exit_code == 0
    assert "hello from queuectl" in (job.stdout or "")


def test_failure_retries_and_moves_to_dlq(session_factory) -> None:
    service = JobService(session_factory)
    service.set_config("backoff-base", "0")
    service.enqueue(
        JobCreate(
            id="job-fail",
            command=shell_command(sys.executable, "-c", "import sys; sys.exit(7)"),
            max_retries=2,
        )
    )

    worker = WorkerRuntime(session_factory, worker_id="worker-fail")
    try:
        assert worker.run_once() is True
        first_failure = service.get("job-fail")
        assert first_failure.state == JobState.FAILED
        assert first_failure.attempts == 1

        assert worker.run_once() is True
        dead_job = service.get("job-fail")
        assert dead_job.state == JobState.DEAD
        assert dead_job.attempts == 2
        assert dead_job.exit_code == 7
    finally:
        worker.workers.mark_stopped(worker.worker_id)


def test_multiple_workers_do_not_overlap(session_factory, tmp_path) -> None:
    recorder = tmp_path / "record.py"
    recorder.write_text(
        "import pathlib, sys, time\n"
        "pathlib.Path(sys.argv[1]).open('x', encoding='utf-8').write(sys.argv[2])\n"
        "time.sleep(0.03)\n",
        encoding="utf-8",
    )

    service = JobService(session_factory)
    expected_ids = [f"job-{index}" for index in range(12)]
    marker_paths = {}
    for job_id in expected_ids:
        marker_path = tmp_path / f"{job_id}.txt"
        marker_paths[job_id] = marker_path
        service.enqueue(
            JobCreate(
                id=job_id,
                command=shell_command(sys.executable, str(recorder), str(marker_path), job_id),
            )
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        processed_counts = list(
            executor.map(
                lambda worker_id: run_worker_until_idle(session_factory, worker_id),
                [f"parallel-worker-{index}" for index in range(4)],
            )
        )

    assert sum(processed_counts) == len(expected_ids)
    completed = service.list_jobs(state=JobState.COMPLETED, limit=100)
    assert len(completed) == len(expected_ids)
    for job_id, marker_path in marker_paths.items():
        assert marker_path.read_text(encoding="utf-8") == job_id


def test_invalid_command_fails_gracefully(session_factory) -> None:
    service = JobService(session_factory)
    service.enqueue(
        JobCreate(
            id="job-invalid",
            command="queuectl-command-that-does-not-exist-zz",
            max_retries=1,
        )
    )

    run_worker_until_idle(session_factory, "worker-invalid")

    job = service.get("job-invalid")
    assert job.state == JobState.DEAD
    assert job.exit_code != 0
    assert job.last_error


def test_persistence_across_restart(db_url) -> None:
    engine = create_engine_for_url(db_url)
    init_db(engine)
    first_factory = create_session_factory(engine)
    JobService(first_factory).enqueue(
        JobCreate(
            id="job-persisted",
            command=shell_command(sys.executable, "-c", "print('persisted')"),
        )
    )
    engine.dispose()

    restarted_engine = create_engine_for_url(db_url)
    init_db(restarted_engine)
    restarted_factory = create_session_factory(restarted_engine)
    restarted_service = JobService(restarted_factory)

    job = restarted_service.get("job-persisted")
    assert job.id == "job-persisted"
    assert job.state == JobState.PENDING
    restarted_engine.dispose()
