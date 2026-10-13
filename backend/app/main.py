"""ResumeLens backend application entry point."""

from fastapi import FastAPI

from app.api.resumes import router as resumes_router

app = FastAPI(
    title="ResumeLens API",
    description="Backend API for analyzing resumes for ATS readiness.",
    version="0.1.0",
)

app.include_router(resumes_router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Report that the service is running.

    This endpoint does not touch the database or any external service, so it
    stays a fast and reliable check that the application itself is up.
    """
    return {"status": "ok"}
