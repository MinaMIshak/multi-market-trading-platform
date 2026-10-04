from datetime import datetime
import json
import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from app.core.config import settings
from app.ui.performance import render_performance_dashboard
from app.ui.shadow import load_shadow_watchlist, render_shadow_watchlist
from app.ui.today import (
    load_security_master_summary,
    load_today_state,
    load_validated_daily_observations,
)
from app.ui.system import load_system_state, render_system
from app.ui.operational import load_operational_state
from app.ui.product import product_state, render_product
from app.ui.context import load_context
from app.ui.macro import load_macro
from app.ui.us import load_us_ranking
from app.ui.experiment import load_experiment
from app.ui.learning import load_learning
from app.ui.ranking import load_ranking
from app.egx_scan_history import load_scan_history
from app.scheduler_heartbeat import load_heartbeat
from app.path_safety import symlinked
from app.runtime_state import startup_event


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    docs_url=None,
    redoc_url=None,
)

# One structured record per process so operators can see which state it reads.
# WARNING level: visible under Python's default last-resort handler without
# any logging configuration. It carries origins only, never paths or secrets.
logging.getLogger('egx.platform').warning(
    json.dumps(startup_event(os.getenv('EGX_BUILD_REVISION')), sort_keys=True))


@app.get("/", response_class=HTMLResponse)
def root(market: str = 'ALL', section: str = 'TODAY') -> HTMLResponse:
    return HTMLResponse(render_product(product(market, section)))


@app.get('/api/product')
def product(market: str = 'ALL', section: str = 'TODAY') -> dict:
    egx = market != "US"
    try:
        return product_state(load_operational_state(), market, section,
                             scan_history=load_scan_history() if egx else None,
                             security_master=load_security_master_summary() if egx else None,
                             daily_observations=(load_validated_daily_observations()
                                                 if egx else None),
                             heartbeat=load_heartbeat() if egx else None,
                             ranking=load_ranking() if egx else None,
                             macro=load_macro() if section == 'RESEARCH' else None,
                             context=(load_context() if section in ('TODAY', 'SWING', 'RESEARCH')
                                      else None),
                             us_ranking=load_us_ranking() if market != 'EGX' else None,
                             experiment=(load_experiment() if market != 'US' and section in (
                                 'TODAY', 'SWING', 'LIVE', 'PERFORMANCE') else None),
                             learning=(load_learning() if section in ('TODAY', 'PERFORMANCE', 'RESEARCH')
                                       else None))
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
        if directory.is_absolute() and not symlinked(directory):
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
