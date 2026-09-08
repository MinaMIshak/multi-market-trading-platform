from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI

from app.core.config import settings


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    docs_url=None,
    redoc_url=None,
)


@app.get("/")
def root() -> dict:
    return {
        "service": settings.app_name,
        "version": settings.version,
        "status": "running",
    }


@app.get("/health")
def health() -> dict:
    now = datetime.now(ZoneInfo(settings.timezone))

    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.version,
        "environment": settings.environment,
        "timezone": settings.timezone,
        "server_time": now.isoformat(),
    }
