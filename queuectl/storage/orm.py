from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from queuectl.core.models import JobPriority, JobState, WorkerStatus
from queuectl.core.time import utc_now
from queuectl.storage.database import Base


class JobRecord(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_claimable", "state", "next_run_at", "run_at", "priority"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default=JobState.PENDING.value)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    timeout: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default=JobPriority.MEDIUM.value)
    run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
    locked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    last_worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    stdout: Mapped[str | None] = mapped_column(Text, nullable=True)
    stderr: Mapped[str | None] = mapped_column(Text, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    execution_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class WorkerRecord(Base):
    __tablename__ = "workers"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=WorkerStatus.STARTING.value)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False, default=utc_now)
    last_heartbeat: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False, default=utc_now)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    current_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    stop_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ConfigRecord(Base):
    __tablename__ = "config"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
