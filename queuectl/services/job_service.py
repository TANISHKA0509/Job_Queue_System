from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from queuectl.core.config import ConfigError
from queuectl.core.models import JobCreate, JobRead, JobState, MetricsSnapshot, StatusSnapshot
from queuectl.storage.repositories import (
    ConfigRepository,
    InvalidStateTransitionError,
    JobAlreadyExistsError,
    JobNotFoundError,
    JobRepository,
    WorkerRepository,
)


class JobService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.jobs = JobRepository(session_factory)
        self.workers = WorkerRepository(session_factory)
        self.config = ConfigRepository(session_factory)
        self.config.ensure_defaults()

    def enqueue(self, payload: JobCreate) -> JobRead:
        runtime = self.config.runtime()
        max_retries = payload.max_retries if payload.max_retries is not None else runtime.max_retries
        record = self.jobs.add(payload, max_retries=max_retries, timeout=runtime.job_timeout)
        return JobRead.model_validate(record)

    def get(self, job_id: str) -> JobRead:
        return JobRead.model_validate(self.jobs.get(job_id))

    def list_jobs(self, state: JobState | None = None, limit: int = 100) -> list[JobRead]:
        return [JobRead.model_validate(record) for record in self.jobs.list(state=state, limit=limit)]

    def list_dlq(self, limit: int = 100) -> list[JobRead]:
        return [JobRead.model_validate(record) for record in self.jobs.list_dead(limit=limit)]

    def retry_dead(self, job_id: str) -> JobRead:
        return JobRead.model_validate(self.jobs.retry_dead(job_id))

    def status(self) -> StatusSnapshot:
        counts = self.jobs.counts_by_state()
        return StatusSnapshot(
            active_workers=self.workers.active_count(),
            pending_jobs=counts[JobState.PENDING.value],
            processing_jobs=counts[JobState.PROCESSING.value],
            completed_jobs=counts[JobState.COMPLETED.value],
            failed_jobs=counts[JobState.FAILED.value],
            dead_jobs=counts[JobState.DEAD.value],
        )

    def metrics(self) -> MetricsSnapshot:
        metrics = self.jobs.metrics(active_workers=self.workers.active_count())
        return MetricsSnapshot.model_validate(metrics)

    def set_config(self, key: str, value: str) -> tuple[str, str]:
        record = self.config.set(key, value)
        return record.key, record.value

    def get_config(self) -> dict[str, str]:
        return self.config.all()


__all__ = [
    "ConfigError",
    "InvalidStateTransitionError",
    "JobAlreadyExistsError",
    "JobNotFoundError",
    "JobService",
]

