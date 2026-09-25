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
    unavailable = {"available": False, "collection": None, "missed": None,
                   "security_types": None, "execution": None,
                   "portfolio": None, "series": None,
                   "status": "UNAVAILABLE / NO AUDITED COLLECTION"}
    try:
        # Isolated operator-owned state only; reject links before ledger reads.
        # This does not defend against concurrent malicious filesystem changes.
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("unsafe or missing shadow directory")
        if any(path.is_symlink() for path in directory.rglob("*")):
            raise ValueError("linked shadow state")
    except (OSError, ValueError):
        return unavailable
    try:
        if source is not None and type(source) is not ShadowWatchlistInput:
            raise ValueError("canonical shadow input required")
        if source is None:
            from app.ui.shadow_input import read_shadow_input
            source = read_shadow_input(directory)
        collection = watchlist_collection_view(
            directory, source.watchlist, source.evidence_packages,
        )
    except (OSError, ValueError, TypeError, KeyError, InvalidOperation, RecursionError):
        # Never return exception details, partial candidates or stale cached views.
        collection = None
    try:
        from app.ui.shadow_input import read_shadow_missed
        missed = read_shadow_missed(directory)
    except (OSError, ValueError, TypeError, KeyError, InvalidOperation, RecursionError):
        missed = None
    try:
        from app.ui.shadow_input import read_shadow_security_types
        if collection is None or source is None:
            raise ValueError("security type requires an audited collection")
        security_types = read_shadow_security_types(directory, source)
    except (OSError, ValueError, TypeError, KeyError, InvalidOperation, RecursionError):
        security_types = None
    try:
        from app.ui.shadow_input import read_shadow_execution
        if collection is None:
            raise ValueError("execution requires an audited collection")
        execution = read_shadow_execution(directory, source)
    except (OSError, ValueError, TypeError, KeyError, InvalidOperation, RecursionError):
        execution = None
    try:
        from app.ui.shadow_input import read_shadow_portfolio
        portfolio = read_shadow_portfolio(directory)
    except (OSError, ValueError, TypeError, KeyError, InvalidOperation, RecursionError):
        portfolio = None
    try:
        from app.ui.shadow_input import read_shadow_series
        series = read_shadow_series(directory)
    except (OSError, ValueError, TypeError, KeyError, InvalidOperation, RecursionError):
        series = None
    return {"series": series, "portfolio": portfolio, "missed": missed,
            "security_types": security_types,
            "available": collection is not None,
            "collection": collection, "execution": execution,
            "status": "AUDITED FROZEN RECORD" if collection is not None else unavailable['status']}


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
        security_types = state.get("security_types")
        bindings = (
            security_types.get("bindings", {})
            if isinstance(security_types, dict)
            else {}
        )
        for candidate in view["candidates"]:
            body += f'<h2>{e(candidate["ticker"])} · {e(candidate["decision_status"])}</h2><dl>'
            binding = bindings.get(candidate.get("candidate_id"))
            if binding is None:
                body += (
                    '<dt>Security type</dt>'
                    '<dd>UNAVAILABLE / NOT AUTHENTICATED</dd>'
                )
            else:
                body += (
                    f'<dt>Security type</dt><dd>{e(binding["security_type"])}</dd>'
                    f'<dt>Security type status</dt><dd>{e(binding["status"])}</dd>'
                    f'<dt>Security type evidence</dt><dd>{e(binding["evidence_package_id"])}</dd>'
                )
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
    def details(value):
        if isinstance(value, dict):
            return '<dl>' + ''.join(
                f'<dt>{e(key.replace("_", " "))}</dt><dd>{details(item)}</dd>'
                for key, item in value.items()
            ) + '</dl>'
        if isinstance(value, (list, tuple)):
            return '; '.join(details(item) for item in value) or 'UNKNOWN'
        return e('UNKNOWN' if value is None else value)

    missed = state.get('missed')
    if missed is not None:
        body += '<h2>Missed paper collection</h2>'
        body += '<p>MISSED / NOT SCORED. No candidates reconstructed; not a zero-candidate decision.</p>'
        for key in ('record_id', 'market', 'market_date', 'decision_cutoff',
                    'recorded_at', 'reason', 'audit_references'):
            body += f'<h3>{e(key.replace("_", " "))}</h3>{details(missed[key])}'

    body += '<h2>Paper execution observation</h2>'
    execution = state.get('execution')
    if execution is None:
        body += '<p>UNAVAILABLE / NO AUDITED EXECUTION</p>'
    else:
        body += ('<p>EXPERIMENTAL / PAPER ONLY. One audited execution observation; '
                 'current position status UNKNOWN. Portfolio NAV NOT EVALUATED.</p>')

        for key in ('collection_status', 'execution_status', 'current_position_status',
                    'recorded_at', 'trigger_evaluation', 'fill', 'position',
                    'position_status', 'entry_session_exit_evaluation',
                    'continuation_evaluations', 'exit_evaluation', 'open_paper_positions',
                    'closed_paper_trades', 'capital_reservation', 'portfolio_policy',
                    'capital_settlement', 'paper_economics', 'audit_references'):
            if key not in execution:
                continue
            body += f'<h3>{e(key.replace("_", " "))}</h3>{details(execution[key])}'
    body += '<h2>Historical native paper portfolio snapshot</h2>'
    portfolio = state.get('portfolio')
    if portfolio is None:
        body += '<p>UNAVAILABLE / NO AUDITED PORTFOLIO SNAPSHOT</p>'
    else:
        body += ('<p>EXPERIMENTAL / PAPER ONLY. Historical gross valuation; '
                 'not current NAV or liquidation value. FX aggregation and validated '
                 'performance NOT EVALUATED. Freshness NOT ESTABLISHED.</p>')
        valuation = portfolio['valuation']
        body += '<h3>Observed cash and invested capital</h3><dl>'
        for label, key in (
            ('Currency', 'currency'), ('Accounting cash', 'cash'),
            ('Active capital reserved', 'active_capital_reserved'),
            ('Observed gross open market value', 'open_market_value'),
            ('Historical gross marked NAV', 'gross_marked_nav'),
            ('Open position count', 'open_position_count'),
        ):
            body += f'<dt>{label}</dt><dd>{details(valuation[key])}</dd>'
        body += ('</dl><p>Stock / ETF allocation comparison UNAVAILABLE: this snapshot '
                 'does not establish security-type classification or comparable ETF evidence. '
                 'Cash is accounting cash, not independently verified broker buying power.</p>')
        for key in ('snapshot_date_utc', 'recorded_at', 'currency', 'scoring',
                    'valuation_status', 'performance_status', 'valuation',
                    'mark_provenance', 'snapshot_id', 'policy_id'):
            body += f'<h3>{e(key.replace("_", " "))}</h3>{details(portfolio[key])}'
    body += '<h2>Historical native paper valuation series</h2>'
    series = state.get('series')
    if series is None:
        body += '<p>UNAVAILABLE / NO AUDITED VALUATION SERIES</p>'
    else:
        body += ('<p>EXPERIMENTAL / PAPER ONLY. Selected historical snapshots only; '
                 'gaps are not filled. Gross valuation changes are not net trading returns. '
                 'Current NAV, FX aggregation and empirical validation NOT ESTABLISHED.</p>')
        body += ('<h3>Observed valuation intervals</h3>'
                 '<p>Actual elapsed reporting days; not validated strategy holding horizons. '
                 'Unavailable horizons are not interpolated or annualized.</p>'
                 '<table><thead><tr><th>Start UTC date</th><th>End UTC date</th>'
                 '<th>Elapsed days</th><th>Gross marked change (fraction)</th>'
                 '</tr></thead><tbody>')
        for interval in series['intervals']:
            body += '<tr>' + ''.join(
                f'<td>{details(interval[key])}</td>' for key in
                ('start_date_utc', 'end_date_utc', 'elapsed_days', 'gross_marked_return')
            ) + '</tr>'
        body += '</tbody></table>'
        for key in ('currency', 'scoring', 'performance_status', 'summary',
                    'observations', 'intervals', 'series_id', 'policy_id'):
            body += f'<h3>{e(key.replace("_", " "))}</h3>{details(series[key])}'
    return '<!doctype html><html><head><title>Frozen paper candidates</title></head><body>' + body + '</body></html>'
