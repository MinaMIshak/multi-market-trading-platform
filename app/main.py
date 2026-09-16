from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.core.config import settings
from app.ui.performance import render_performance_dashboard
from app.ui.portfolio import render_portfolio_dashboard
from app.ui.system import render_system_dashboard
from app.ui.today import (
    load_today_state,
    render_today_dashboard,
)


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    docs_url=None,
    redoc_url=None,
)


@app.get("/", response_class=HTMLResponse)
def root() -> HTMLResponse:
    state = load_today_state()
    return HTMLResponse(
        render_today_dashboard(state)
    )


@app.get("/api/today")
def today() -> dict:
    return load_today_state()


@app.get("/performance", response_class=HTMLResponse)
def performance() -> HTMLResponse:
    return HTMLResponse(
        render_performance_dashboard()
    )


@app.get("/portfolio", response_class=HTMLResponse)
def portfolio() -> HTMLResponse:
    return HTMLResponse(
        render_portfolio_dashboard()
    )


@app.get("/system", response_class=HTMLResponse)
def system() -> HTMLResponse:
    return HTMLResponse(
        render_system_dashboard()
    )


@app.get("/health")
def health() -> dict:
    now = datetime.now(
        ZoneInfo(settings.timezone)
    )

    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.version,
        "environment": settings.environment,
        "timezone": settings.timezone,
        "server_time": now.isoformat(),
    }
