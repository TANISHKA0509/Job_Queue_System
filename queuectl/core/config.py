from __future__ import annotations

from dataclasses import dataclass


DEFAULT_CONFIG: dict[str, str] = {
    "max-retries": "3",
    "backoff-base": "2",
    "poll-interval": "1.0",
    "job-timeout": "",
}

INTEGER_KEYS = {"max-retries", "backoff-base", "job-timeout"}
FLOAT_KEYS = {"poll-interval"}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimeConfig:
    max_retries: int
    backoff_base: int
    poll_interval: float
    job_timeout: int | None


def validate_config_value(key: str, value: str) -> str:
    if key not in DEFAULT_CONFIG:
        allowed = ", ".join(sorted(DEFAULT_CONFIG))
        raise ConfigError(f"unknown config key '{key}'. Allowed keys: {allowed}")

    cleaned = value.strip()

    if key in INTEGER_KEYS:
        if key == "job-timeout" and cleaned == "":
            return cleaned
        try:
            parsed = int(cleaned)
        except ValueError as exc:
            raise ConfigError(f"{key} must be an integer") from exc
        if parsed < 0:
            raise ConfigError(f"{key} must be greater than or equal to 0")
        if key == "job-timeout" and parsed == 0:
            raise ConfigError("job-timeout must be blank or greater than 0")
        return str(parsed)

    if key in FLOAT_KEYS:
        try:
            parsed_float = float(cleaned)
        except ValueError as exc:
            raise ConfigError(f"{key} must be a number") from exc
        if parsed_float <= 0:
            raise ConfigError(f"{key} must be greater than 0")
        return str(parsed_float)

    return cleaned

