"""Read-only frozen candidate surface over the existing shadow audit boundary."""
from dataclasses import dataclass
from decimal import InvalidOperation
from html import escape
from pathlib import Path

from app.paper.shadow_records import ShadowWatchlist
from app.paper.shadow_report import watchlist_collection_view
from app.research.historical_evidence import HistoricalEvidencePackage


@dataclass(frozen=True)
class ShadowWatchlistInput:
    watchlist: ShadowWatchlist
    evidence_packages: tuple[HistoricalEvidencePackage, ...]

    def __post_init__(self):
        if type(self.watchlist) is not ShadowWatchlist or (
            type(self.evidence_packages) is not tuple
            or any(type(p) is not HistoricalEvidencePackage for p in self.evidence_packages)
        ):
            raise ValueError("canonical watchlist and evidence packages required")


def load_shadow_watchlist(directory: Path, source: ShadowWatchlistInput | None) -> dict:
    unavailable = {"available": False, "collection": None, "execution": None,
                   "status": "UNAVAILABLE / NO AUDITED COLLECTION"}
    try:
        if source is not None and type(source) is not ShadowWatchlistInput:
            raise ValueError("canonical shadow input required")
        # Isolated operator-owned state only; reject links before ledger reads.
        # This does not defend against concurrent malicious filesystem changes.
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("unsafe or missing shadow directory")
        if any(path.is_symlink() for path in directory.rglob("*")):
            raise ValueError("linked shadow state")
        if source is None:
            from app.ui.shadow_input import read_shadow_input
            source = read_shadow_input(directory)
        collection = watchlist_collection_view(
            directory, source.watchlist, source.evidence_packages,
        )
    except (OSError, ValueError, TypeError, KeyError, InvalidOperation, RecursionError):
        # Never return exception details, partial candidates or stale cached views.
        return unavailable
    try:
        from app.ui.shadow_input import read_shadow_execution
        execution = read_shadow_execution(directory, source)
    except (OSError, ValueError, TypeError, KeyError, InvalidOperation, RecursionError):
        execution = None
    return {"available": True, "collection": collection, "execution": execution,
            "status": "AUDITED FROZEN RECORD"}


def render_shadow_watchlist(state: dict) -> str:
    def e(value):
        return escape(str(value))

    body = '<h1>Frozen paper candidates</h1><p>EXPERIMENTAL / PAPER ONLY</p>'
    body += '<p>Freshness NOT ESTABLISHED. Frozen record only; not a current trading signal.</p>'
    body += '<p><a href="/">Today</a> · <a href="/performance">M7 performance</a></p>'
    if not state["available"]:
        body += '<p>UNAVAILABLE / NO AUDITED COLLECTION</p>'
    else:
        view = state["collection"]
        body += (
            f'<p>{e(view["market"])} · {e(view["market_date"])} · Record {e(view["record_id"])}</p>'
            f'<p>{e(view["scope"])} · {e(view["scoring"])}</p>'
            f'<p>Information cutoff: {e(view["information_cutoff"])} · '
            f'Decision cutoff: {e(view["decision_cutoff"])}</p>'
            '<p>Frozen declarations; fills and current positions NOT EVALUATED. '
            'NAV and empirical performance NOT EVALUATED.</p>'
        )
        if not view["candidates"]:
            body += '<p>Audited frozen watchlist contains zero candidates.</p>'
        for candidate in view["candidates"]:
            body += f'<h2>{e(candidate["ticker"])} · {e(candidate["decision_status"])}</h2><dl>'
            for label, key in (
                ("Thesis", "thesis"), ("Context", "context"),
                ("Technical setup", "technical_setup"), ("Entry condition", "entry_condition"),
                ("Entry low", "entry_low"), ("Entry high", "entry_high"),
                ("Stop", "stop"), ("Targets", "targets"),
                ("Holding window", "expected_holding_window"), ("Confidence", "confidence"),
                ("Probability", "probability"), ("Liquidity declaration", "liquidity_status"),
                ("Liquidity reason", "liquidity_reason"), ("Paper quantity declaration", "paper_quantity"),
                ("Paper risk declaration", "paper_risk_status"), ("Evidence IDs", "evidence_ids"),
            ):
                value = candidate[key]
                if isinstance(value, (list, tuple)):
                    value = "; ".join(map(str, value)) or "UNKNOWN"
                body += f'<dt>{label}</dt><dd>{e("UNKNOWN" if value is None else value)}</dd>'
            body += '</dl>'
    body += '<h2>Paper execution observation</h2>'
    execution = state.get('execution')
    if execution is None:
        body += '<p>UNAVAILABLE / NO AUDITED EXECUTION</p>'
    else:
        body += ('<p>EXPERIMENTAL / PAPER ONLY. One same-session observation; '
                 'current position status UNKNOWN. Portfolio NAV NOT EVALUATED.</p>')

        def details(value):
            if isinstance(value, dict):
                return '<dl>' + ''.join(
                    f'<dt>{e(key.replace("_", " "))}</dt><dd>{details(item)}</dd>'
                    for key, item in value.items()
                ) + '</dl>'
            if isinstance(value, (list, tuple)):
                return '; '.join(details(item) for item in value) or 'UNKNOWN'
            return e('UNKNOWN' if value is None else value)

        for key in ('position_status', 'exit_evaluation', 'open_paper_positions',
                    'closed_paper_trades', 'audit_references'):
            body += f'<h3>{e(key.replace("_", " "))}</h3>{details(execution[key])}'
    return '<!doctype html><html><head><title>Frozen paper candidates</title></head><body>' + body + '</body></html>'
