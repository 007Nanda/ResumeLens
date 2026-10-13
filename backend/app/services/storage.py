"""Filesystem storage for uploaded resume files.

Storage filenames are generated entirely on the server. No client-supplied
string is ever used as a path component, which is what makes path traversal
structurally impossible rather than merely filtered.

Note on request size: FastAPI parses the multipart body before this module is
reached, so chunked copying bounds the size that is written to disk but does
not on its own prevent the parser from buffering a large body. Server-level or
reverse-proxy body limits remain the defence for that.
"""

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from app.config import Settings
from app.services.file_validation import (
    FileTooLargeError,
    ResumeFileError,
    detect_media_type,
    safe_display_filename,
    validate_extension,
    validate_not_empty,
)

CHUNK_SIZE = 64 * 1024
PART_SUFFIX = ".part"


@dataclass(frozen=True)
class StoredUpload:
    """Metadata about a stored file, safe to hand back to the API layer."""

    file_id: str
    filename: str
    content_type: str
    size_bytes: int


async def store_resume_file(
    *, upload: UploadFile, settings: Settings
) -> StoredUpload:
    """Validate and persist one uploaded resume.

    The upload is streamed to a ``.part`` file first and only promoted to its
    final name once validation has passed, so a failed upload never leaves a
    file that looks like a completed one.
    """
    display_name = safe_display_filename(upload.filename)
    extension = validate_extension(display_name)

    file_id = uuid.uuid4().hex
    root = _resolve_root(settings.upload_dir)
    part_path = _resolve_inside(root, f"{file_id}{extension}{PART_SUFFIX}")
    final_path = _resolve_inside(root, f"{file_id}{extension}")

    try:
        size_bytes = await _write_stream(upload, part_path, settings.max_upload_bytes)
        validate_not_empty(size_bytes)
        media_type = detect_media_type(part_path, extension)
        _promote(part_path, final_path)
    except ResumeFileError:
        _discard(part_path)
        raise
    except OSError:
        _discard(part_path)
        raise ResumeFileError(
            "The uploaded file could not be stored.", status_code=500
        ) from None

    return StoredUpload(
        file_id=file_id,
        filename=display_name,
        content_type=media_type,
        size_bytes=size_bytes,
    )


def _resolve_root(upload_dir: Path) -> Path:
    """Create and return the storage root directory."""
    root = Path(upload_dir).expanduser().resolve()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise ResumeFileError(
            "The upload directory is unavailable.", status_code=500
        ) from None
    return root


def _resolve_inside(root: Path, name: str) -> Path:
    """Join ``name`` onto ``root`` and assert the result stays inside it.

    ``name`` is always server-generated, so this check should be unreachable.
    It is kept as a cheap invariant that fails loudly if a future change ever
    lets caller-influenced text reach this function.
    """
    resolved = (root / name).resolve()
    if resolved.parent != root:
        raise ResumeFileError(
            "Refusing to store the file outside the upload directory.",
            status_code=500,
        )
    return resolved


async def _write_stream(
    upload: UploadFile, part_path: Path, max_upload_bytes: int
) -> int:
    """Copy the upload to ``part_path`` in bounded chunks, aborting past the cap."""
    total = 0
    with part_path.open("xb") as handle:
        while True:
            chunk = await upload.read(CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_upload_bytes:
                raise FileTooLargeError(max_upload_bytes)
            handle.write(chunk)
    return total


def _promote(part_path: Path, final_path: Path) -> None:
    """Atomically move the validated temporary file into place."""
    if final_path.exists():
        # uuid4 gives 122 bits of randomness, so this is not a practical race;
        # the check simply guarantees an existing stored file is never replaced.
        raise ResumeFileError(
            "Could not reserve a unique storage name.", status_code=500
        )
    os.replace(part_path, final_path)


def _discard(part_path: Path) -> None:
    """Remove a partial file, ignoring failures during cleanup."""
    try:
        part_path.unlink(missing_ok=True)
    except OSError:
        pass