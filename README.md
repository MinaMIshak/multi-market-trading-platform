# EGX + US Paper/Shadow Trading Platform

PAPER/SHADOW ONLY. LIVE_MONEY=DISABLED. No component places broker or exchange
orders. Unsupported or unevidenced state is shown as UNKNOWN/null.

Project constitution: `AGENTS.md`. Current checkpoint: `PROGRESS.json`.

## Run locally

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/ . With no environment configured, the UI starts and
reports unavailable evidence as UNKNOWN; it never fabricates data.

Optional read-only evidence inputs (all absent by default):
`EGX_DB_PATH`, `EGX_SCHEDULER_HEARTBEAT_PATH`, `EGX_SCAN_HISTORY_PATH`,
`EGX_SCAN_LEDGER_PATH`, `EGX_SHADOW_DIRECTORY`, `EGX_BUILD_REVISION`.

Container build (API + OBSERVE-mode scheduler, bound to 127.0.0.1:8000):
`docker compose up --build`. See `EGX_SCAN_SCHEDULER.md` and
`SCHEDULER_HEARTBEAT.md` for scheduler configuration.

## Routes

| Route | Purpose |
| --- | --- |
| `/?section=<S>&market=<M>` | Product shell. S: TODAY, LIVE, PRE-SURGE, SWING, PERFORMANCE, RESEARCH, SYSTEM. M: EGX, US, ALL |
| `/api/product` | JSON for the product shell (same parameters) |
| `/system`, `/api/system` | Operator progress and evidence view |
| `/shadow`, `/api/shadow` | Audited Paper/Shadow collection |
| `/performance` | Performance evidence (requires authentic lifecycle evidence) |
| `/api/today`, `/api/paper-operational` | Compatibility endpoints |
| `/health` | Liveness only |

## Tests

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

The full suite takes about 11 minutes. Some modules are stdlib-only and also run
with `python3 -B -m unittest tests.<module>`.

## Research

The research layer is intelligence only. `app/research/intelligence.py` defines
sourced research notes (fact / derived / interpretation / UNKNOWN with
provenance and timestamps). The anthropics/financial-services evaluation is in
`docs/FINANCIAL_SERVICES_EVALUATION.md`: methodology reference only, with
every connector fail-closed.
