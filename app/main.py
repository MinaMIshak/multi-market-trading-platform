from datetime import datetime
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from app.core.config import settings
from app.ui.performance import render_performance_dashboard
from app.ui.shadow import load_shadow_watchlist, render_shadow_watchlist
from app.ui.today import load_today_state
from app.ui.system import load_system_state, render_system
from app.ui.operational import load_operational_state
from app.ui.product import product_state, render_product


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    docs_url=None,
    redoc_url=None,
)


@app.get("/", response_class=HTMLResponse)
def root(market: str = 'ALL', section: str = 'TODAY') -> HTMLResponse:
    return HTMLResponse(render_product(product(market, section)))


@app.get('/api/product')
def product(market: str = 'ALL', section: str = 'TODAY') -> dict:
    try:
        return product_state(load_operational_state(), market, section)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail='Unknown product view') from exc


@app.get("/api/today")
def today() -> dict:
    operational = load_operational_state()
    if operational['configured']:
        return operational
    return load_today_state()


@app.get('/api/paper-operational')
def paper_operational() -> dict:
    return load_operational_state()


@app.get("/performance", response_class=HTMLResponse)
def performance() -> HTMLResponse:
    return HTMLResponse(
        render_performance_dashboard()
    )


@app.get("/api/shadow")
def shadow() -> dict:
    # Explicit operator configuration only; never infer state from the live DB.
    configured = os.getenv("EGX_SHADOW_DIRECTORY")
    state = {"available": False, "collection": None, "missed": None,
             "security_types": None, "execution": None,
             "portfolio": None, "series": None,
             "status": "UNAVAILABLE / NO AUDITED COLLECTION"}
    if configured:
        directory = Path(configured)
        if directory.is_absolute() and not any(p.is_symlink() for p in directory.parents):
            state = load_shadow_watchlist(directory, None)
    return {"mode": "EXPERIMENTAL / PAPER ONLY",
            "empirical_validation": "NOT YET VALIDATED",
            "freshness": "NOT ESTABLISHED / FROZEN RECORD ONLY", **state}


@app.get("/shadow", response_class=HTMLResponse)
def shadow_page() -> HTMLResponse:
    return HTMLResponse(render_shadow_watchlist(shadow()))


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


@app.get("/api/system")
def system() -> dict:
    return load_system_state()


@app.get("/system", response_class=HTMLResponse)
def system_page() -> HTMLResponse:
    return HTMLResponse(render_system(load_system_state()))
