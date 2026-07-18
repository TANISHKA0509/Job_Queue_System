from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass


MAX_CAPTURED_OUTPUT = 1_000_000


@dataclass(frozen=True)
class ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    execution_time: float
    timed_out: bool = False


def _truncate_output(value: str) -> str:
    if len(value) <= MAX_CAPTURED_OUTPUT:
        return value
    return value[:MAX_CAPTURED_OUTPUT] + "\n[queuectl: output truncated]\n"


def execute_command(command: str, timeout: int | None = None) -> ExecutionResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        elapsed = time.monotonic() - started
        return ExecutionResult(
            exit_code=completed.returncode,
            stdout=_truncate_output(completed.stdout or ""),
            stderr=_truncate_output(completed.stderr or ""),
            execution_time=elapsed,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - started
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(errors="replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode(errors="replace")
        return ExecutionResult(
            exit_code=124,
            stdout=_truncate_output(stdout),
            stderr=_truncate_output(stderr + f"\nCommand timed out after {timeout} seconds."),
            execution_time=elapsed,
            timed_out=True,
        )
    except OSError as exc:
        elapsed = time.monotonic() - started
        return ExecutionResult(
            exit_code=127,
            stdout="",
            stderr=str(exc),
            execution_time=elapsed,
        )

