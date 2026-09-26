"""Coordinate explicit EGX scope through existing verification, without publishing.

Scope is caller-supplied, not authoritative universe evidence. Missing inputs and
failed verification are classified, never counted as completed strategy scans.
"""
from collections import Counter
from pathlib import Path


class ScanBlocked(ValueError):
    """Reviewed admission failure from the operational verifier."""


def _verify(database, data_root, source):
    # Lazy import allows the coordinator's failure handling to remain available
    # even when the strategy runtime cannot be loaded.
    from app.paper.swing_launch import LaunchBlocked, run_signal

    try:
        return run_signal(database, data_root, source, Path(data_root), publish=False)
    except LaunchBlocked as exc:
        raise ScanBlocked(exc.reason) from exc


def scan_egx_scope(*, symbols, sources, database, data_root, scope_reference, history_path=None,
                   source_errors=None):
    """Verify each distinct configured symbol; preserve existing admission gates.

    sources maps symbols to SwingLaunchInput objects already decoded by the
    caller. This does not acquire data, establish membership, rank or infer fills.
    Returned counts describe this invocation only, not current market readiness.
    """
    scope = tuple(symbols)
    if (not isinstance(scope_reference, str) or not scope_reference.strip()
            or not scope or any(not isinstance(s, str) or not s or s != s.strip() for s in scope)
            or len(set(scope)) != len(scope)):
        raise ValueError('unique nonempty explicit scope and reference required')
    outcomes = []
    # Only fixed diagnostic codes cross the configuration boundary; never echo
    # decoder exceptions or caller-supplied text into operator-visible reports.
    reasons = {
        'INVALID_LAUNCH_EVIDENCE': 'launch evidence failed contract validation',
        'LAUNCH_IDENTITY_MISMATCH': 'launch identity does not match requested symbol',
    }
    source_errors = source_errors or {}
    for symbol in scope:
        item = {'symbol': symbol, 'market': 'EGX', 'status': 'EVIDENCE_BLOCKED',
                'scanned': False, 'reason': 'launch evidence unavailable'}
        source = sources.get(symbol)
        if symbol in source_errors:
            item['reason'] = reasons.get(source_errors[symbol], 'launch evidence rejected')
        elif source is not None:
            if getattr(source, 'symbol', None) != symbol:
                item['reason'] = 'launch identity does not match requested symbol'
            else:
                try:
                    result = _verify(database, data_root, source)
                    if (result.get('operation') != 'VERIFIED_SIGNAL_NOT_PUBLISHED'
                            or result.get('symbol') != symbol or result.get('market') != 'EGX'
                            or result.get('signal_status') not in ('WATCH', 'READY_NO_SIGNAL')
                            or result.get('mode') != 'SHADOW' or result.get('live') != 'DISABLED'
                            or result.get('market_data') != 'FRESH'):
                        raise ValueError('unexpected verification result')
                    item.update(status=result['signal_status'], scanned=True,
                                reason='existing SWING verification completed', verification=result)
                except ScanBlocked as exc:
                    item['reason'] = str(exc)
                except Exception as exc:
                    # Exception text may contain provider credentials or paths.
                    # Only known launch failures expose their reviewed reason.
                    from_exception = type(exc).__name__
                    item['reason'] = 'verification failed: ' + from_exception
        outcomes.append(item)
    counts = Counter(item['status'] for item in outcomes)
    report = {'market': 'EGX', 'scope_reference': scope_reference,
            'scope_kind': 'EXPLICIT_SELECTION_NOT_AUTHORITATIVE_UNIVERSE',
            'requested': len(scope), 'scanned': sum(item['scanned'] for item in outcomes),
            'status_counts': {s: counts[s] for s in ('WATCH', 'READY_NO_SIGNAL', 'EVIDENCE_BLOCKED')},
            'symbols': outcomes, 'live_money': False}

    if history_path is not None:
        from app.egx_scan_history import write_scan_history
        write_scan_history(report, history_path)
    return report
