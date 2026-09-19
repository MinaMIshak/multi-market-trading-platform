"""Read-only experimental surface; no scheduler or execution startup hooks."""
from pathlib import Path
import re

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.ui.today import load_today_state, render_today_dashboard
from app.ui.performance import render_performance_dashboard


def create_experimental_app(*, state_directory: Path, build_commit: str) -> FastAPI:
    """Build identity must be supplied by the committed-snapshot launcher.

    This factory validates its shape, not Git provenance. It never reads the
    ordinary application database environment variable or initializes a database.
    """
    if re.fullmatch(r"[0-9a-f]{40}", build_commit) is None:
        raise ValueError("a full commit identity is required")
    if not state_directory.is_absolute() or not state_directory.is_dir():
        raise ValueError("an existing absolute experimental state directory is required")
    state_directory = state_directory.resolve()
    database = state_directory / "platform.db"
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    identity = {
        "mode": "EXPERIMENTAL / PAPER ONLY",
        "build_commit": build_commit,
        "empirical_validation": "NOT YET VALIDATED",
    }

    def snapshot() -> dict:
        # Recheck on every request so a swapped symlink cannot redirect reads.
        if database.is_symlink() or database.resolve().parent != state_directory:
            return dict(available=False, symbols=[], counts={}, error="UnsafeStatePath")
        return load_today_state(database)

    def page(content: str) -> HTMLResponse:
        banner = (
            '<aside role="note">EXPERIMENTAL / PAPER ONLY · '
            f'Build <code>{build_commit}</code> · NOT YET VALIDATED</aside>'
        )
        return HTMLResponse(content.replace("<body>", "<body>" + banner, 1))

    @app.get("/", response_class=HTMLResponse)
    def root():
        return page(render_today_dashboard(snapshot()))

    @app.get("/api/today")
    def today():
        return {**identity, **snapshot()}

    @app.get("/performance", response_class=HTMLResponse)
    def performance():
        return page(render_performance_dashboard())

    @app.get("/health")
    def health():
        return {**identity, "status": "healthy", "data_available": snapshot()["available"]}

    return app
