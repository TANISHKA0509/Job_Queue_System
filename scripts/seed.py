from __future__ import annotations

from queuectl.core.models import JobCreate, JobPriority
from queuectl.services.job_service import JobService
from queuectl.storage.database import create_engine_for_url, create_session_factory, get_database_url, init_db


def main() -> None:
    engine = create_engine_for_url(get_database_url())
    init_db(engine)
    service = JobService(create_session_factory(engine))

    jobs = [
        JobCreate(id="seed-hello", command="echo hello from queuectl", priority=JobPriority.HIGH),
        JobCreate(id="seed-slow", command="sleep 2", timeout=10, priority=JobPriority.MEDIUM),
        JobCreate(id="seed-failure", command="python -c \"import sys; sys.exit(1)\"", max_retries=2),
    ]

    for job in jobs:
        try:
            created = service.enqueue(job)
            print(f"created {created.id}")
        except Exception as exc:
            print(f"skipped {job.id}: {exc}")


if __name__ == "__main__":
    main()

