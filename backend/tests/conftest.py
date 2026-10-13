"""Shared fixtures for the backend test suite.

Every fixture that touches the filesystem is rooted in pytest's ``tmp_path``,
so the suite never writes into the real ``backend/uploads`` directory and is
safe to run on a developer's machine.
"""

import io
import zipfile
from pathlib import Path
from typing import Callable

import pytest
from fastapi.testclient import TestClient

from app.config import DEFAULT_MAX_UPLOAD_BYTES, Settings, get_settings
from app.main import app

# Minimal byte sequences used to build upload fixtures in memory. Generating
# them here keeps binary blobs out of the repository.
_PDF_BODY = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"
_CONTENT_TYPES_XML = (
    '<?xml version="1.0"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
)
_DOCUMENT_XML = (
    '<?xml version="1.0"?>'
    '<w:document '
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>'
)


@pytest.fixture
def upload_dir(tmp_path: Path) -> Path:
    """A per-test directory standing in for ``backend/uploads``."""
    return tmp_path / "uploads"


@pytest.fixture
def make_client(
    upload_dir: Path,
) -> Callable[..., TestClient]:
    """Build a TestClient whose settings are overridden, never read from env."""
    clients: list[TestClient] = []

    def _make_client(
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    ) -> TestClient:
        settings = Settings(
            upload_dir=upload_dir, max_upload_bytes=max_upload_bytes
        )
        app.dependency_overrides[get_settings] = lambda: settings
        client = TestClient(app)
        clients.append(client)
        return client

    try:
        yield _make_client
    finally:
        for client in clients:
            client.close()
        app.dependency_overrides.clear()


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    """A TestClient using the default settings against a temporary directory."""
    with make_client() as test_client:
        yield test_client


@pytest.fixture
def make_pdf_bytes() -> Callable[..., bytes]:
    """Build bytes that start with the required PDF signature."""

    def _make(padding: int = 0) -> bytes:
        return _PDF_BODY + (b"%" * padding)

    return _make


@pytest.fixture
def make_docx_bytes() -> Callable[..., bytes]:
    """Build a ZIP-based DOCX, optionally omitting ``word/document.xml``."""

    def _make(*, include_document: bool = True) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
            if include_document:
                archive.writestr("word/document.xml", _DOCUMENT_XML)
        return buffer.getvalue()

    return _make


@pytest.fixture
def stored_files(upload_dir: Path) -> Callable[[], list[Path]]:
    """List everything currently in the temporary upload directory.

    Includes any leftover ``.part`` file, so tests can assert that rejected
    uploads leave nothing behind at all.
    """

    def _stored() -> list[Path]:
        if not upload_dir.exists():
            return []
        return sorted(path for path in upload_dir.iterdir() if path.is_file())

    return _stored