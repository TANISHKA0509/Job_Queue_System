from __future__ import annotations

MAX_BACKOFF_SECONDS = 3600


def calculate_backoff_seconds(backoff_base: int, attempts: int) -> int:
    if attempts <= 0:
        return 0
    if backoff_base <= 0:
        return 0
    return min(backoff_base**attempts, MAX_BACKOFF_SECONDS)

