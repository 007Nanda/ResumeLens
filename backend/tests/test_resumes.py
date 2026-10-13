"""Tests for the resume upload endpoint."""

import re
from pathlib import Path
from typing import Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import (
    DEFAULT_MAX_UPLOAD_BYTES,
    ENV_MAX_UPLOAD_BYTES,
    ENV_UPLOAD_DIR,
    ConfigurationError,
    Settings,
    get_settings,
)

UPLOAD_URL = "/api/v1/resumes"

PDF_MEDIA_TYPE = "application/pdf"
DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def post_upload(
    client: TestClient,
    filename: str,
    content: bytes,
    content_type: str = "application/octet-stream",
) -> httpx.Response:
    """POST one multipart file field named ``file``."""
    return client.post(
        UPLOAD_URL, files={"file": (filename, content, content_type)}
    )


# --- successful uploads ----------------------------------------------------


def test_valid_pdf_upload_returns_201(
    client: TestClient,
    make_pdf_bytes: Callable[..., bytes],
    stored_files: Callable[[], list[Path]],
) -> None:
    content = make_pdf_bytes()

    response = post_upload(client, "resume.pdf", content, PDF_MEDIA_TYPE)

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {
        "file_id",
        "filename",
        "content_type",
        "size_bytes",
        "status",
    }
    assert re.fullmatch(r"[0-9a-f]{32}", body["file_id"])
    assert body["filename"] == "resume.pdf"
    assert body["content_type"] == PDF_MEDIA_TYPE
    assert body["size_bytes"] == len(content)
    assert body["status"] == "stored"

    files = stored_files()
    assert [path.name for path in files] == [f"{body['file_id']}.pdf"]
    assert files[0].read_bytes() == content


def test_valid_docx_upload_returns_201(
    client: TestClient,
    make_docx_bytes: Callable[..., bytes],
    stored_files: Callable[[], list[Path]],
) -> None:
    content = make_docx_bytes()

    response = post_upload(client, "resume.docx", content)

    assert response.status_code == 201
    body = response.json()
    assert re.fullmatch(r"[0-9a-f]{32}", body["file_id"])
    assert body["filename"] == "resume.docx"
    assert body["content_type"] == DOCX_MEDIA_TYPE
    assert body["size_bytes"] == len(content)
    assert body["status"] == "stored"

    files = stored_files()
    assert [path.name for path in files] == [f"{body['file_id']}.docx"]


def test_client_content_type_is_not_trusted(
    client: TestClient,
    make_pdf_bytes: Callable[..., bytes],
) -> None:
    """A DOCX declared as PDF is rejected because the bytes decide."""
    response = post_upload(client, "resume.pdf", make_pdf_bytes(), DOCX_MEDIA_TYPE)

    assert response.status_code == 201
    assert response.json()["content_type"] == PDF_MEDIA_TYPE


# --- unsupported formats ---------------------------------------------------


@pytest.mark.parametrize("filename", ["resume.txt", "resume.exe"])
def test_unsupported_extension_is_rejected(
    client: TestClient,
    stored_files: Callable[[], list[Path]],
    filename: str,
) -> None:
    response = post_upload(client, filename, b"definitely not a resume")

    assert response.status_code == 400
    assert "detail" in response.json()
    assert stored_files() == []


@pytest.mark.parametrize("filename", ["resume", "resume.", "resume.pdf.exe"])
def test_filename_without_allowed_extension_is_rejected(
    client: TestClient,
    make_pdf_bytes: Callable[..., bytes],
    stored_files: Callable[[], list[Path]],
    filename: str,
) -> None:
    response = post_upload(client, filename, make_pdf_bytes())

    assert response.status_code == 400
    assert stored_files() == []


def test_zip_without_document_xml_is_rejected(
    client: TestClient,
    make_docx_bytes: Callable[..., bytes],
    stored_files: Callable[[], list[Path]],
) -> None:
    """A plain ZIP renamed to .docx has the right signature but is not a DOCX."""
    response = post_upload(client, "resume.docx", make_docx_bytes(include_document=False))

    assert response.status_code == 400
    assert stored_files() == []


@pytest.mark.parametrize(
    ("filename", "use_pdf"),
    [("resume.docx", True), ("resume.pdf", False)],
)
def test_extension_content_mismatch_is_rejected(
    client: TestClient,
    make_pdf_bytes: Callable[..., bytes],
    make_docx_bytes: Callable[..., bytes],
    stored_files: Callable[[], list[Path]],
    filename: str,
    use_pdf: bool,
) -> None:
    content = make_pdf_bytes() if use_pdf else make_docx_bytes()

    response = post_upload(client, filename, content)

    assert response.status_code == 400
    assert stored_files() == []


# --- request errors --------------------------------------------------------


def test_missing_file_field_preserves_fastapi_422(
    client: TestClient,
    stored_files: Callable[[], list[Path]],
) -> None:
    response = client.post(UPLOAD_URL)

    assert response.status_code == 422
    assert "detail" in response.json()
    assert stored_files() == []


def test_empty_file_is_rejected(
    client: TestClient,
    stored_files: Callable[[], list[Path]],
) -> None:
    response = post_upload(client, "resume.pdf", b"")

    assert response.status_code == 400
    assert stored_files() == []


def test_oversized_upload_returns_413_and_leaves_nothing(
    make_client: Callable[..., TestClient],
    make_pdf_bytes: Callable[..., bytes],
    stored_files: Callable[[], list[Path]],
) -> None:
    content = make_pdf_bytes(padding=8192)

    with make_client(max_upload_bytes=1024) as client:
        response = post_upload(client, "resume.pdf", content)

    assert response.status_code == 413
    assert stored_files() == []


def test_upload_just_under_the_limit_is_accepted(
    make_client: Callable[..., TestClient],
    make_pdf_bytes: Callable[..., bytes],
) -> None:
    content = make_pdf_bytes()

    with make_client(max_upload_bytes=len(content)) as client:
        response = post_upload(client, "resume.pdf", content)

    assert response.status_code == 201


# --- path safety -----------------------------------------------------------


@pytest.mark.parametrize(
    "filename", ["../../etc/evil.pdf", "..\\..\\evil.pdf", "/etc/evil.pdf"]
)
def test_traversal_filename_cannot_escape_storage(
    client: TestClient,
    upload_dir: Path,
    make_pdf_bytes: Callable[..., bytes],
    stored_files: Callable[[], list[Path]],
    filename: str,
) -> None:
    response = post_upload(client, filename, make_pdf_bytes())

    assert response.status_code == 201
    file_id = response.json()["file_id"]

    files = stored_files()
    assert [path.name for path in files] == [f"{file_id}.pdf"]
    assert files[0].resolve().parent == upload_dir.resolve()
    # Nothing was created beside the upload directory either.
    assert [path.name for path in upload_dir.parent.iterdir()] == [upload_dir.name]


# --- cleanup ---------------------------------------------------------------


def test_rejected_uploads_leave_no_partial_files(
    client: TestClient,
    make_pdf_bytes: Callable[..., bytes],
    make_docx_bytes: Callable[..., bytes],
    stored_files: Callable[[], list[Path]],
) -> None:
    rejections = [
        ("resume.txt", b"plain text"),
        ("resume.docx", make_docx_bytes(include_document=False)),
        ("resume.docx", make_pdf_bytes()),
        ("resume.pdf", b""),
        ("noextension", make_pdf_bytes()),
    ]

    for filename, content in rejections:
        response = post_upload(client, filename, content)
        assert response.status_code == 400, filename
        assert stored_files() == [], filename


def test_successful_upload_leaves_no_part_file(
    client: TestClient,
    make_pdf_bytes: Callable[..., bytes],
    stored_files: Callable[[], list[Path]],
) -> None:
    assert post_upload(client, "resume.pdf", make_pdf_bytes()).status_code == 201

    files = stored_files()
    assert len(files) == 1
    assert not any(path.name.endswith(".part") for path in files)


# --- configuration ---------------------------------------------------------


@pytest.mark.parametrize("max_upload_bytes", [0, -1])
def test_non_positive_max_upload_bytes_is_rejected(
    upload_dir: Path, max_upload_bytes: int
) -> None:
    with pytest.raises(ConfigurationError):
        Settings(upload_dir=upload_dir, max_upload_bytes=max_upload_bytes)


def test_non_integer_max_upload_bytes_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv(ENV_MAX_UPLOAD_BYTES, "not-a-number")
    get_settings.cache_clear()
    try:
        with pytest.raises(ConfigurationError):
            get_settings()
    finally:
        get_settings.cache_clear()


def test_settings_read_configured_environment(
    monkeypatch, tmp_path: Path
) -> None:
    target = tmp_path / "custom-uploads"
    monkeypatch.setenv(ENV_UPLOAD_DIR, str(target))
    monkeypatch.setenv(ENV_MAX_UPLOAD_BYTES, "2048")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.upload_dir == target.resolve()
        assert settings.max_upload_bytes == 2048
    finally:
        get_settings.cache_clear()


def test_default_max_upload_bytes_is_ten_mebibytes() -> None:
    assert DEFAULT_MAX_UPLOAD_BYTES == 10_485_760