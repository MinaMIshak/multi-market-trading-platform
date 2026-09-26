"""Opt-in local verification using the scheduler's durable job ledger.

Completion means scope classified, never that all symbols are ready. Failed jobs
are not retried every poll; the separate fallback window permits one retry.
"""
from app.egx_scan_config import load_scan_configuration
from app.egx_scan import scan_egx_scope


def dispatch_scan(*, evaluation, repository, database, data_root,
                  config_path, history_path):
    if evaluation.calendar_truth != 'VERIFIED_TRADING_DAY':
        return None
    for window in evaluation.due:
        checkpoint = window.name
        if checkpoint.value not in ('EGX_SCAN_PRIMARY', 'EGX_SCAN_FALLBACK'):
            continue
        if checkpoint.value == 'EGX_SCAN_FALLBACK' and any(
                c.value == 'EGX_SCAN_PRIMARY'
                for c in repository.successful_checkpoints(evaluation.market_date)):
            continue
        key = dict(market_date=evaluation.market_date, checkpoint_name=checkpoint)
        row = repository.get_job(**key)
        if row is None or row['status'] != 'PENDING':
            continue
        if not repository.claim_job(**key):
            continue
        try:
            # Reload each attempt: corrected evidence must not require restart.
            config = load_scan_configuration(config_path)
            report = scan_egx_scope(
                symbols=config.symbols, sources=config.sources,
                scope_reference=config.scope_reference, database=database,
                data_root=data_root, history_path=history_path)
        except Exception as exc:
            error = 'EGX_SCAN_FAILED:' + type(exc).__name__
            if not repository.mark_failed(**key, error=error):
                raise RuntimeError('failed to persist scan failure') from None
            return dict(checkpoint=checkpoint.value, succeeded=False, error=error)
        if not repository.mark_succeeded(**key):
            raise RuntimeError('failed to persist scan completion')
        return dict(checkpoint=checkpoint.value, succeeded=True,
                    requested=report['requested'], scanned=report['scanned'])
    return None
