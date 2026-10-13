"""HTTP endpoints for resume uploads."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.services.file_validation import ResumeFileError
from app.services.storage import store_resume_file

router = APIRouter(prefix="/api/v1", tags=["resumes"])


class ResumeUploadResponse(BaseModel):
    """Metadata describing a stored resume file.

    The on-disk location is deliberately absent: clients only ever receive the
    server-generated ``file_id``. A successful upload means the bytes matched a
    known container format; it is not a statement that the document is
    complete, renders correctly, or is free of malware.
    """

    file_id: str = Field(
        description="Server-generated 32-character hexadecimal identifier.",
        examples=["9f2b1c4d8e7a4f0b8c3d5e6f7a8b9c0d"],
    )
    filename: str = Field(
        description="Original filename, reduced to a safe basename for display.",
        examples=["resume.pdf"],
    )
    content_type: str = Field(
        description="Media type verified from the file's own bytes.",
        examples=["application/pdf"],
    )
    size_bytes: int = Field(
        description="Number of bytes actually written to storage.",
        examples=[248193],
    )
    status: Literal["stored"] = Field(
        description="Outcome of the upload.",
        examples=["stored"],
    )


@router.post(
    "/resumes",
    response_model=ResumeUploadResponse,
    status_code=201,
    summary="Upload a resume",
)
async def upload_resume(
    file: Annotated[
        UploadFile,
        File(description="Resume file. Allowed types: .pdf and .docx."),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ResumeUploadResponse:
    """Store one uploaded resume and return its metadata.

    Raises 400 for unsupported or inconsistent content and 413 when the file
    exceeds the configured maximum size. A missing ``file`` field keeps
    FastAPI's default 422 validation response.
    """
    try:
        stored = await store_resume_file(upload=file, settings=settings)
    except ResumeFileError as exc:
        # Messages defined by the validation layer are safe to expose; the
        # "from None" keeps internal tracebacks out of the error report.
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None

    return ResumeUploadResponse(
        file_id=stored.file_id,
        filename=stored.filename,
        content_type=stored.content_type,
        size_bytes=stored.size_bytes,
        status="stored",
    )