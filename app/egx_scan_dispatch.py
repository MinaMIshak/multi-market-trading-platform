"""Opt-in local verification using the scheduler's durable job ledger.

Completion means scope classified, never that all symbols are ready. Failed jobs
are not retried every poll; the separate fallback window permits one retry.
"""
from app.egx_scan_config import load_scan_configuration
from app.egx_scan import scan_egx_scope
from app.egx_scan_history import write_scan_history, valid_scheduler_attempt


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
        previous_count = row['attempt_count']
        if not repository.claim_job(**key):
            continue
        row = repository.get_job(**key)
        attempt = dict(market_date=evaluation.market_date.isoformat(),
                       checkpoint=checkpoint.value, attempt_count=row['attempt_count'],
                       started_at=row['started_at'], status=row['status'],
                       finished_at=row['finished_at'])
        if (not valid_scheduler_attempt(attempt) or attempt['status'] != 'RUNNING'
                or type(previous_count) is not int
                or attempt['attempt_count'] != previous_count + 1):
            raise RuntimeError('invalid claimed scan attempt')
        guarded_key = key | {'expected_attempt': (attempt['attempt_count'], attempt['started_at'])}
        try:
            # Reload each attempt: corrected evidence must not require restart.
            config = load_scan_configuration(config_path)
            report = scan_egx_scope(
                symbols=config.symbols, sources=config.sources,
                source_errors=config.source_errors,
                scope_reference=config.scope_reference, database=database,
                data_root=data_root)
            # Validate completion before persisting success. A malformed report
            # must leave the fallback available, not a successful ledger entry.
            if (not isinstance(report, dict)
                    or type(report.get('requested')) is not int
                    or report['requested'] != len(config.symbols)
                    or type(report.get('scanned')) is not int
                    or not 0 <= report['scanned'] <= report['requested']):
                raise ValueError('invalid scan completion counts')
            write_scan_history(report, history_path, scheduler_attempt=attempt)
        except Exception as exc:
            error = 'EGX_SCAN_FAILED:' + type(exc).__name__
            if not repository.mark_failed(**guarded_key, error=error):
                raise RuntimeError('failed to persist scan failure') from None
            return dict(checkpoint=checkpoint.value, succeeded=False, error=error)
        if not repository.mark_succeeded(**guarded_key):
            raise RuntimeError('failed to persist scan completion')
        completed = repository.get_job(**key)
        if (completed['status'] != 'SUCCEEDED'
                or (completed['attempt_count'], completed['started_at']) != guarded_key['expected_attempt']):
            raise RuntimeError('scan completion identity mismatch')
        attempt.update(status='SUCCEEDED', finished_at=completed['finished_at'])
        # A failed final replace leaves completion unknown in the earlier
        # snapshot. Never reverse ledger success or enable fallback here.
        write_scan_history(report, history_path, scheduler_attempt=attempt)
        return dict(checkpoint=checkpoint.value, succeeded=True,
                    requested=report['requested'], scanned=report['scanned'])
    return None
