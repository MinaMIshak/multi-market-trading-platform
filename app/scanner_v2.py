"""Bounded Scanner v2 projection of the existing EGX verification coordinator.

No strategy activation or parallel admission path. Legacy SWING only produces
WATCH/READY_NO_SIGNAL, so it cannot emit a confirmed trade-decision-v1 record.
Verification may append its existing audit event; this wrapper publishes nothing.
"""
from collections import Counter

from app.egx_scan import scan_egx_scope
from app.egx_scope import SCAN_STATUSES, valid_scope_symbol


MAX_SCOPE = 1000
EXCLUSIONS = {
    'WATCH': 'SETUP_NOT_CONFIRMED',
    'READY_NO_SIGNAL': 'NO_CONFIRMED_SETUP',
    'EVIDENCE_BLOCKED': 'EVIDENCE_BLOCKED',
    'DATA_STALE': 'DATA_STALE',
    'NOT_READY': 'DATA_INSUFFICIENT',
}


def scan_egx(*, symbols, sources, database, data_root, scope_reference,
             source_errors=None):
    """Classify one explicit EGX selection, not authoritative market membership.

    Counts describe the verifier invocation, not present-time provider health.
    The selection size is explicit; authoritative universe size remains unknown.
    Deterministic ordering is lexical and never ranks invalid setups.
    """
    if (type(symbols) not in (list, tuple) or not 0 < len(symbols) <= MAX_SCOPE
            or any(not valid_scope_symbol(s) for s in symbols)
            or len(set(symbols)) != len(symbols)):
        raise ValueError('bounded unique explicit EGX scope required')
    if type(scope_reference) is not str or not scope_reference.strip():
        raise ValueError('explicit scope reference required')
    if (type(sources) is not dict or not set(sources) <= set(symbols)
            or (source_errors is not None and
                (type(source_errors) is not dict or not set(source_errors) <= set(symbols)))):
        raise ValueError('sources/errors must belong to requested scope')
    scope = tuple(sorted(symbols))
    report = scan_egx_scope(symbols=scope, sources=sources, database=database,
                            data_root=data_root, scope_reference=scope_reference,
                            source_errors=source_errors)
    # Fail closed on coordinator schema drift; never project partial coverage.
    if type(report) is not dict:
        raise ValueError('invalid EGX coverage report')
    rows = report.get('symbols')
    if (report.get('market') != 'EGX' or report.get('live_money') is not False
            or report.get('scope_kind') != 'EXPLICIT_SELECTION_NOT_AUTHORITATIVE_UNIVERSE'
            or report.get('scope_reference') != scope_reference
            or type(report.get('requested')) is not int
            or report.get('requested') != len(scope) or type(rows) is not list
            or len(rows) != len(scope)):
        raise ValueError('invalid EGX coverage report')
    outcomes = []
    for symbol, row in zip(scope, rows):
        if (type(row) is not dict or row.get('symbol') != symbol
                or row.get('market') != 'EGX' or type(row.get('status')) is not str
                or row.get('status') not in SCAN_STATUSES
                or type(row.get('scanned')) is not bool
                or row['scanned'] != (row['status'] in ('WATCH', 'READY_NO_SIGNAL'))):
            raise ValueError('invalid EGX coverage row')
        outcomes.append({'symbol': symbol, 'market': 'EGX',
                         'verification_status': row['status'], 'scanned': row['scanned'],
                         'status': 'EXCLUDED', 'reason': EXCLUSIONS[row['status']]})
    counts = Counter(row['verification_status'] for row in outcomes)
    scanned = sum(row['scanned'] for row in outcomes)
    totals = report.get('status_counts')
    if (type(report.get('scanned')) is not int or report.get('scanned') != scanned
            or type(totals) is not dict or any(type(v) is not int for v in totals.values())
            or report.get('status_counts') != {s: counts[s] for s in SCAN_STATUSES}):
        raise ValueError('inconsistent EGX coverage totals')
    return {
        'schema_version': 'scanner-v2-egx-coverage-v1', 'market': 'EGX',
        'adapter_version': 'legacy-swing-coverage-v1',
        'scope_reference': scope_reference, 'scope_kind': report['scope_kind'],
        'selection_size': len(scope), 'authoritative_universe_size': None,
        'requested': len(scope), 'usable_data': scanned, 'scanned': scanned,
        'usable_data_semantics': 'existing verification passed at its decision timestamp',
        'status_counts': {s: counts[s] for s in SCAN_STATUSES},
        'excluded': len(scope),
        'exclusion_counts': {reason: sum(r['reason'] == reason for r in outcomes)
                             for reason in EXCLUSIONS.values()},
        'symbols': outcomes, 'candidates': [], 'candidate_count': 0,
        'capability': 'COVERAGE_ONLY_NO_CONFIRMED_SETUP_ADAPTER',
        'mode': 'SHADOW', 'live_money': False, 'execution_allowed': False,
        'promotion': 'NOT_AUTHORIZED',
    }
