from __future__ import annotations

import socket
from collections.abc import Iterable
from datetime import timedelta

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from queuectl.core.config import DEFAULT_CONFIG, RuntimeConfig, validate_config_value
from queuectl.core.executor import ExecutionResult
from queuectl.core.models import JobCreate, JobPriority, JobState, WorkerStatus
from queuectl.core.retry import calculate_backoff_seconds
from queuectl.core.time import utc_now
from queuectl.storage.orm import ConfigRecord, JobRecord, WorkerRecord


class RepositoryError(RuntimeError):
    pass


class JobAlreadyExistsError(RepositoryError):
    pass


class JobNotFoundError(RepositoryError):
    pass


class InvalidStateTransitionError(RepositoryError):
    pass


class JobRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def add(self, payload: JobCreate, max_retries: int, timeout: int | None) -> JobRecord:
        now = utc_now()
        record = JobRecord(
            id=payload.id,
            command=payload.command,
            state=JobState.PENDING.value,
            attempts=0,
            max_retries=max_retries,
            timeout=payload.timeout if payload.timeout is not None else timeout,
            priority=payload.priority.value,
            run_at=payload.run_at,
            next_run_at=payload.run_at,
            created_at=now,
            updated_at=now,
        )
        with self.session_factory() as session:
            try:
                session.add(record)
                session.commit()
                session.refresh(record)
                return record
            except IntegrityError as exc:
                session.rollback()
                raise JobAlreadyExistsError(f"job '{payload.id}' already exists") from exc

    def get(self, job_id: str) -> JobRecord:
        with self.session_factory() as session:
            record = session.get(JobRecord, job_id)
            if record is None:
                raise JobNotFoundError(f"job '{job_id}' was not found")
            return record

    def list(self, state: JobState | None = None, limit: int = 100) -> list[JobRecord]:
        with self.session_factory() as session:
            stmt = select(JobRecord).order_by(JobRecord.created_at.desc()).limit(limit)
            if state is not None:
                stmt = stmt.where(JobRecord.state == state.value)
            return list(session.execute(stmt).scalars())

    def list_dead(self, limit: int = 100) -> list[JobRecord]:
        return self.list(JobState.DEAD, limit=limit)

    def counts_by_state(self) -> dict[str, int]:
        with self.session_factory() as session:
            rows = session.execute(
                select(JobRecord.state, func.count()).group_by(JobRecord.state)
            ).all()
        counts = {state.value: 0 for state in JobState}
        counts.update({state: count for state, count in rows})
        return counts

    def claim_next(self, worker_id: str) -> JobRecord | None:
        now = utc_now()
        priority_order = case(
            (JobRecord.priority == JobPriority.HIGH.value, 0),
            (JobRecord.priority == JobPriority.MEDIUM.value, 1),
            (JobRecord.priority == JobPriority.LOW.value, 2),
            else_=3,
        )
        scheduled_order = case((JobRecord.run_at.is_(None), 1), else_=0)
        due_filter = (
            or_(JobRecord.run_at.is_(None), JobRecord.run_at <= now),
            or_(JobRecord.next_run_at.is_(None), JobRecord.next_run_at <= now),
        )
        claimable_states = [JobState.PENDING.value, JobState.FAILED.value]

        with self.session_factory() as session:
            candidate_ids = list(
                session.execute(
                    select(JobRecord.id)
                    .where(JobRecord.state.in_(claimable_states), *due_filter)
                    .order_by(priority_order, scheduled_order, JobRecord.run_at, JobRecord.created_at)
                    .limit(10)
                ).scalars()
            )

            for job_id in candidate_ids:
                result = session.execute(
                    update(JobRecord)
                    .where(JobRecord.id == job_id, JobRecord.state.in_(claimable_states))
                    .values(
                        state=JobState.PROCESSING.value,
                        locked_by=worker_id,
                        last_worker_id=worker_id,
                        locked_at=now,
                        updated_at=now,
                    )
                )
                if result.rowcount == 1:
                    session.commit()
                    claimed = session.get(JobRecord, job_id)
                    return claimed
                session.rollback()

        return None

    def complete(self, job_id: str, result: ExecutionResult) -> JobRecord:
        now = utc_now()
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise JobNotFoundError(f"job '{job_id}' was not found")
            job.state = JobState.COMPLETED.value
            job.stdout = result.stdout
            job.stderr = result.stderr
            job.exit_code = result.exit_code
            job.execution_time = result.execution_time
            job.last_error = None
            job.locked_by = None
            job.locked_at = None
            job.next_run_at = None
            job.updated_at = now
            session.commit()
            session.refresh(job)
            return job

    def fail(self, job_id: str, result: ExecutionResult, backoff_base: int) -> JobRecord:
        now = utc_now()
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise JobNotFoundError(f"job '{job_id}' was not found")

            attempts = job.attempts + 1
            exhausted = attempts >= job.max_retries
            delay = calculate_backoff_seconds(backoff_base, attempts)

            job.attempts = attempts
            job.state = JobState.DEAD.value if exhausted else JobState.FAILED.value
            job.stdout = result.stdout
            job.stderr = result.stderr
            job.exit_code = result.exit_code
            job.execution_time = result.execution_time
            job.last_error = result.stderr.strip() or f"command exited with {result.exit_code}"
            job.locked_by = None
            job.locked_at = None
            job.next_run_at = None if exhausted else now + timedelta(seconds=delay)
            job.updated_at = now
            session.commit()
            session.refresh(job)
            return job

    def retry_dead(self, job_id: str) -> JobRecord:
        now = utc_now()
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise JobNotFoundError(f"job '{job_id}' was not found")
            if job.state != JobState.DEAD.value:
                raise InvalidStateTransitionError(f"job '{job_id}' is not in the DLQ")

            job.state = JobState.PENDING.value
            job.attempts = 0
            job.next_run_at = now
            job.locked_by = None
            job.locked_at = None
            job.updated_at = now
            session.commit()
            session.refresh(job)
            return job

    def delete_all(self) -> None:
        with self.session_factory() as session:
            session.execute(delete(JobRecord))
            session.commit()

    def metrics(self, active_workers: int) -> dict[str, float | int | None]:
        with self.session_factory() as session:
            total = session.scalar(select(func.count()).select_from(JobRecord)) or 0
            completed = (
                session.scalar(
                    select(func.count()).select_from(JobRecord).where(JobRecord.state == JobState.COMPLETED.value)
                )
                or 0
            )
            failed = (
                session.scalar(
                    select(func.count()).select_from(JobRecord).where(JobRecord.state == JobState.FAILED.value)
                )
                or 0
            )
            dead = (
                session.scalar(
                    select(func.count()).select_from(JobRecord).where(JobRecord.state == JobState.DEAD.value)
                )
                or 0
            )
            avg_execution_time = session.scalar(
                select(func.avg(JobRecord.execution_time)).where(JobRecord.execution_time.is_not(None))
            )

        return {
            "total_jobs": total,
            "completed_jobs": completed,
            "failed_jobs": failed,
            "dead_jobs": dead,
            "success_rate": (completed / total * 100) if total else 0.0,
            "average_execution_time": avg_execution_time,
            "active_workers": active_workers,
        }


class WorkerRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def register(self, worker_id: str, pid: int, hostname: str | None = None) -> WorkerRecord:
        now = utc_now()
        record = WorkerRecord(
            id=worker_id,
            pid=pid,
            hostname=hostname or socket.gethostname(),
            status=WorkerStatus.IDLE.value,
            started_at=now,
            last_heartbeat=now,
            stopped_at=None,
            current_job_id=None,
            stop_requested=False,
        )
        with self.session_factory() as session:
            session.merge(record)
            session.commit()
            saved = session.get(WorkerRecord, worker_id)
            assert saved is not None
            return saved

    def heartbeat(
        self,
        worker_id: str,
        status: WorkerStatus,
        current_job_id: str | None = None,
    ) -> None:
        now = utc_now()
        with self.session_factory() as session:
            session.execute(
                update(WorkerRecord)
                .where(WorkerRecord.id == worker_id)
                .values(
                    status=status.value,
                    current_job_id=current_job_id,
                    last_heartbeat=now,
                )
            )
            session.commit()

    def mark_stopped(self, worker_id: str) -> None:
        now = utc_now()
        with self.session_factory() as session:
            session.execute(
                update(WorkerRecord)
                .where(WorkerRecord.id == worker_id)
                .values(
                    status=WorkerStatus.STOPPED.value,
                    current_job_id=None,
                    stopped_at=now,
                    last_heartbeat=now,
                )
            )
            session.commit()

    def request_stop_all(self) -> int:
        with self.session_factory() as session:
            result = session.execute(
                update(WorkerRecord)
                .where(WorkerRecord.status != WorkerStatus.STOPPED.value)
                .values(stop_requested=True, status=WorkerStatus.STOPPING.value)
            )
            session.commit()
            return result.rowcount or 0

    def should_stop(self, worker_id: str) -> bool:
        with self.session_factory() as session:
            return bool(
                session.scalar(
                    select(WorkerRecord.stop_requested).where(WorkerRecord.id == worker_id)
                )
            )

    def active_count(self, heartbeat_ttl_seconds: int = 30) -> int:
        since = utc_now() - timedelta(seconds=heartbeat_ttl_seconds)
        with self.session_factory() as session:
            return (
                session.scalar(
                    select(func.count())
                    .select_from(WorkerRecord)
                    .where(
                        WorkerRecord.status != WorkerStatus.STOPPED.value,
                        WorkerRecord.last_heartbeat >= since,
                    )
                )
                or 0
            )

    def list_active(self, heartbeat_ttl_seconds: int = 30) -> list[WorkerRecord]:
        since = utc_now() - timedelta(seconds=heartbeat_ttl_seconds)
        with self.session_factory() as session:
            return list(
                session.execute(
                    select(WorkerRecord)
                    .where(
                        WorkerRecord.status != WorkerStatus.STOPPED.value,
                        WorkerRecord.last_heartbeat >= since,
                    )
                    .order_by(WorkerRecord.started_at.desc())
                ).scalars()
            )


class ConfigRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def ensure_defaults(self) -> None:
        with self.session_factory() as session:
            try:
                existing = set(session.execute(select(ConfigRecord.key)).scalars())
                for key, value in DEFAULT_CONFIG.items():
                    if key not in existing:
                        session.add(ConfigRecord(key=key, value=value))
                session.commit()
            except IntegrityError:
                session.rollback()

    def set(self, key: str, value: str) -> ConfigRecord:
        cleaned = validate_config_value(key, value)
        now = utc_now()
        with self.session_factory() as session:
            record = session.get(ConfigRecord, key)
            if record is None:
                record = ConfigRecord(key=key, value=cleaned, updated_at=now)
                session.add(record)
            else:
                record.value = cleaned
                record.updated_at = now
            session.commit()
            session.refresh(record)
            return record

    def get(self, key: str) -> str:
        self.ensure_defaults()
        with self.session_factory() as session:
            value = session.scalar(select(ConfigRecord.value).where(ConfigRecord.key == key))
        if value is None:
            return DEFAULT_CONFIG[key]
        return value

    def all(self) -> dict[str, str]:
        self.ensure_defaults()
        with self.session_factory() as session:
            rows: Iterable[tuple[str, str]] = session.execute(
                select(ConfigRecord.key, ConfigRecord.value).order_by(ConfigRecord.key)
            )
            return {key: value for key, value in rows}

    def runtime(self) -> RuntimeConfig:
        values = self.all()
        timeout = values["job-timeout"]
        return RuntimeConfig(
            max_retries=int(values["max-retries"]),
            backoff_base=int(values["backoff-base"]),
            poll_interval=float(values["poll-interval"]),
            job_timeout=int(timeout) if timeout else None,
        )
