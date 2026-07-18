from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from queuectl.core.time import coerce_utc_naive


class JobState(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD = "dead"


class JobPriority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class WorkerStatus(str, Enum):
    STARTING = "starting"
    IDLE = "idle"
    BUSY = "busy"
    STOPPING = "stopping"
    STOPPED = "stopped"


class JobCreate(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    command: str = Field(min_length=1)
    max_retries: int | None = Field(default=None, ge=0)
    timeout: int | None = Field(default=None, gt=0)
    priority: JobPriority = JobPriority.MEDIUM
    run_at: datetime | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("job id cannot be blank")
        if any(ch.isspace() for ch in cleaned):
            raise ValueError("job id cannot contain whitespace")
        return cleaned

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("command cannot be blank")
        return cleaned

    @field_validator("run_at")
    @classmethod
    def validate_run_at(cls, value: datetime | None) -> datetime | None:
        return coerce_utc_naive(value)


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    command: str
    state: JobState
    attempts: int
    max_retries: int
    timeout: int | None
    priority: JobPriority
    run_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime
    updated_at: datetime
    locked_by: str | None
    last_worker_id: str | None
    stdout: str | None
    stderr: str | None
    exit_code: int | None
    execution_time: float | None
    last_error: str | None


class StatusSnapshot(BaseModel):
    active_workers: int
    pending_jobs: int
    processing_jobs: int
    completed_jobs: int
    failed_jobs: int
    dead_jobs: int


class MetricsSnapshot(BaseModel):
    total_jobs: int
    completed_jobs: int
    failed_jobs: int
    dead_jobs: int
    success_rate: float
    average_execution_time: float | None
    active_workers: int

