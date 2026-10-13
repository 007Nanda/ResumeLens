"""Application settings loaded from environment variables.

Settings are resolved against the backend package location rather than the
process working directory, so the application behaves identically no matter
where it is started from.
"""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# app/config.py -> backend/
BACKEND_DIR: Path = Path(__file__).resolve().parent.parent

ENV_UPLOAD_DIR = "RESUMELENS_UPLOAD_DIR"
ENV_MAX_UPLOAD_BYTES = "RESUMELENS_MAX_UPLOAD_BYTES"

DEFAULT_UPLOAD_DIR: Path = BACKEND_DIR / "uploads"
DEFAULT_MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024  # 10 MiB


class ConfigurationError(RuntimeError):
    """Raised when the environment provides unusable configuration."""


@dataclass(frozen=True)
class Settings:
    """Runtime settings used by the resume upload feature."""

    upload_dir: Path
    max_upload_bytes: int

    def __post_init__(self) -> None:
        if self.max_upload_bytes <= 0:
            raise ConfigurationError(
                f"{ENV_MAX_UPLOAD_BYTES} must be greater than zero."
            )


def _read_upload_dir(raw: str | None) -> Path:
    """Resolve the upload directory, anchoring relative paths to the backend."""
    if raw is None or not raw.strip():
        return DEFAULT_UPLOAD_DIR

    path = Path(raw.strip()).expanduser()
    if not path.is_absolute():
        path = BACKEND_DIR / path
    return path.resolve()


def _read_max_upload_bytes(raw: str | None) -> int:
    if raw is None or not raw.strip():
        return DEFAULT_MAX_UPLOAD_BYTES

    try:
        value = int(raw.strip())
    except ValueError:
        raise ConfigurationError(
            f"{ENV_MAX_UPLOAD_BYTES} must be an integer number of bytes."
        ) from None

    if value <= 0:
        raise ConfigurationError(f"{ENV_MAX_UPLOAD_BYTES} must be greater than zero.")
    return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return process-wide settings, reading the environment on first use.

    Tests override this through FastAPI's ``dependency_overrides`` instead of
    mutating the environment, which keeps the developer's machine untouched.
    """
    return Settings(
        upload_dir=_read_upload_dir(os.environ.get(ENV_UPLOAD_DIR)),
        max_upload_bytes=_read_max_upload_bytes(
            os.environ.get(ENV_MAX_UPLOAD_BYTES)
        ),
    )