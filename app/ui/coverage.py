"""Coverage breakdown: each level states its own evidence; none implies the next.

Restates existing readers only (security master, validated daily artifacts
annotated by the source registry, recorded scan history). It never promotes
identities to exchange membership, stored bars to admitted or current data,
or data availability to scan or candidate coverage. A level without evidence
is None (UNKNOWN), never zero.
"""
from html import escape

LEVELS = (
    ('identities', 'Canonical identities (security master)'),
    ('equities', 'Known equities (security master)'),
    ('authoritative_universe', 'Authoritative current exchange universe'),
    ('observed_symbols', 'Symbols with any validated daily observations'),
    ('admitted_symbols', 'Symbols backed by an admitted source'),
    ('current_symbols', 'Symbols with session-current data (any source)'),
    ('admitted_current_symbols', 'Symbols with admitted and session-current data'),
    ('scanned_symbols', 'Scanned symbols'),
    ('candidates', 'Candidates'),
)


def _symbols(rows, predicate):
    return len({row['canonical_symbol'] for row in rows if predicate(row)})


def coverage_breakdown(*, security_master, daily_observations, scan_runs):
    """EGX coverage levels with a basis per level; inputs are existing reader outputs."""
    levels = {}
    if security_master is None:
        levels['identities'] = (None, 'security master unavailable')
        levels['equities'] = (None, 'security master unavailable')
    else:
        providers = ', '.join(security_master.get('source_providers') or []) or 'unknown source'
        levels['identities'] = (security_master.get('total_instruments'),
                                f'identity/reference records from {providers}; not membership')
        levels['equities'] = ((security_master.get('by_type') or {}).get('EQUITY'),
                              'instrument_type EQUITY; not dated exchange membership')
    levels['authoritative_universe'] = (
        None, 'no dated authoritative membership source is connected')
    if daily_observations is None:
        for key in ('observed_symbols', 'admitted_symbols', 'current_symbols',
                    'admitted_current_symbols'):
            levels[key] = (None, 'daily observations unavailable')
    else:
        rows = daily_observations
        admitted = lambda row: row.get('source_status') == 'ADMITTED'
        current = lambda row: row.get('freshness') == 'CURRENT'
        levels['observed_symbols'] = (_symbols(rows, lambda row: True),
                                      'distinct symbols with VALIDATED daily artifacts')
        levels['admitted_symbols'] = (_symbols(rows, admitted),
                                      'source status ADMITTED in the daily source registry')
        levels['current_symbols'] = (_symbols(rows, current),
                                     'freshness CURRENT from verified sessions')
        levels['admitted_current_symbols'] = (
            _symbols(rows, lambda row: admitted(row) and current(row)),
            'the only data eligible for new candidates')
    run = (scan_runs or {}).get('run') if (scan_runs or {}).get('status') == 'HISTORICAL_RUN' else None
    if run is None:
        levels['scanned_symbols'] = (None, 'no scan history recorded')
        levels['candidates'] = (None, 'no scan history recorded')
    else:
        completed = run.get('completed_at')
        levels['scanned_symbols'] = (run.get('scanned'),
                                     f'historical run completed {completed}; not current coverage')
        levels['candidates'] = ((run.get('status_counts') or {}).get('WATCH'),
                                f'WATCH in historical run completed {completed}; a candidate is not a fill')
    return [{'level': key, 'label': label, 'count': levels[key][0], 'basis': levels[key][1]}
            for key, label in LEVELS]


def unknown_breakdown(reason):
    return [{'level': key, 'label': label, 'count': None, 'basis': reason}
            for key, label in LEVELS]


def render_coverage(coverage):
    html = ''
    for market, rows in coverage.items():
        body = ''.join(
            f'<tr><th scope="row">{escape(row["label"])}</th>'
            f'<td>{"UNKNOWN" if row["count"] is None else escape(str(row["count"]))}</td>'
            f'<td>{escape(row["basis"])}</td></tr>' for row in rows)
        html += (f'<section aria-label="{market} coverage breakdown">'
                 f'<h2>{market} coverage breakdown</h2>'
                 '<p>Each row is separate evidence and does not imply the next row. '
                 'Identities are not the exchange universe, stored bars are not admitted '
                 'or current data, and data is not a scan or a candidate. '
                 'UNKNOWN means no evidence; it is not zero.</p>'
                 f'<table aria-label="{market} coverage breakdown"><thead><tr>'
                 '<th scope="col">Level</th><th scope="col">Count</th>'
                 '<th scope="col">Basis</th></tr></thead>'
                 f'<tbody>{body}</tbody></table></section>')
    return html
