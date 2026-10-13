"""Validation rules for uploaded resume files.

The checks here confirm that a file's bytes are consistent with the container
format implied by its filename. They deliberately do **not** claim that a
document is complete, renders correctly, carries no active content such as
embedded JavaScript, or is free of malware. Content analysis of that kind is
out of scope for this feature.

The client-supplied ``Content-Type`` header and the filename extension are both
attacker-controlled, so the extension is only used to decide *which* container
format to verify. The file's own leading bytes decide whether it passed.
"""

import zipfile
from pathlib import Path, PurePosixPath

PDF_SIGNATURE = b"%PDF-"
ZIP_SIGNATURE = b"PK\x03\x04"
DOCX_DOCUMENT_ENTRY = "word/document.xml"

PDF_MEDIA_TYPE = "application/pdf"
DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

# Extension -> media type, also used to pick the stored extension.
ALLOWED_EXTENSIONS: dict[str, str] = {
    ".pdf": PDF_MEDIA_TYPE,
    ".docx": DOCX_MEDIA_TYPE,
}

MAX_DISPLAY_FILENAME_LENGTH = 255


class ResumeFileError(Exception):
    """An expected upload failure that is safe to report to a client.

    Messages must stay generic: they are returned verbatim in API responses and
    must never contain filesystem paths or other internal detail.
    """

    status_code: int = 400

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


class FileTooLargeError(ResumeFileError):
    """The upload exceeded the configured maximum size."""

    status_code: int = 413

    def __init__(self, max_upload_bytes: int) -> None:
        super().__init__(
            "Uploaded file exceeds the maximum allowed size of "
            f"{_describe_size(max_upload_bytes)}."
        )


def _describe_size(max_upload_bytes: int) -> str:
    if max_upload_bytes >= 1024 * 1024:
        return f"{max_upload_bytes / (1024 * 1024):.0f} MiB"
    return f"{max_upload_bytes} bytes"


def safe_display_filename(raw_filename: str | None) -> str:
    """Reduce a client-supplied filename to a safe, display-only basename.

    Browsers may submit a full client-side path. Both separator styles are
    normalised before taking the basename, so traversal segments such as
    ``../../evil.pdf`` or ``..\\..\\evil.pdf`` reduce to ``evil.pdf``. The result
    is only ever displayed or stored as metadata; it is never used to build a
    filesystem path.
    """
    if raw_filename is None:
        raise ResumeFileError("A filename is required.")

    basename = PurePosixPath(raw_filename.replace("\\", "/")).name.strip()
    if not basename or basename in {".", ".."}:
        raise ResumeFileError("A filename is required.")

    return basename[:MAX_DISPLAY_FILENAME_LENGTH]


def validate_extension(filename: str) -> str:
    """Return the lower-cased extension if it is allowed for resumes."""
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ResumeFileError(
            "Unsupported file type. Allowed extensions: .pdf, .docx."
        )
    return suffix


def validate_not_empty(size_bytes: int) -> None:
    """Reject zero-byte uploads before any content inspection."""
    if size_bytes <= 0:
        raise ResumeFileError("Uploaded file is empty.")


def detect_media_type(path: Path, extension: str) -> str:
    """Verify that ``path`` holds the container format implied by ``extension``."""
    if extension == ".pdf":
        return _detect_pdf(path)
    if extension == ".docx":
        return _detect_docx(path)
    raise ResumeFileError("Unsupported file type. Allowed extensions: .pdf, .docx.")


def _detect_pdf(path: Path) -> str:
    with path.open("rb") as handle:
        header = handle.read(len(PDF_SIGNATURE))
    if header != PDF_SIGNATURE:
        raise ResumeFileError(
            "File content is not a valid PDF. A %PDF- signature is required."
        )
    return PDF_MEDIA_TYPE


def _detect_docx(path: Path) -> str:
    with path.open("rb") as handle:
        header = handle.read(len(ZIP_SIGNATURE))
    if header != ZIP_SIGNATURE:
        raise ResumeFileError(
            "File content is not a valid DOCX. A ZIP container is required."
        )

    try:
        with zipfile.ZipFile(path) as archive:
            # namelist() reads the central directory only. Nothing is extracted,
            # so an archive bomb is never expanded by this check.
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        raise ResumeFileError(
            "File content is not a readable DOCX archive."
        ) from None

    if DOCX_DOCUMENT_ENTRY not in names:
        raise ResumeFileError(
            "File content is not a valid DOCX. "
            f"The archive is missing {DOCX_DOCUMENT_ENTRY}."
        )
    return DOCX_MEDIA_TYPE