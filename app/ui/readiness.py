"""Per-component EGX readiness; data availability never implies scan readiness.

Each input is reported separately: canonical identities and daily observations
may exist while operational scanning remains blocked. The summary never
reports READY; the strongest state is PREREQUISITES_MET, which still requires a
completed verified scan receipt before any candidate exists.
"""

UNKNOWN = 'UNKNOWN'


def _security_master(summary):
    if not isinstance(summary, dict):
        return {'status': 'UNAVAILABLE', 'instruments': None, 'equities': None}
    by_type = summary.get('by_type') or {}
    total = summary.get('total_instruments')
    return {'status': 'AVAILABLE' if total else 'EMPTY',
            'instruments': total, 'equities': by_type.get('EQUITY')}


def _daily_observations(rows):
    if rows is None:
        return {'status': 'UNAVAILABLE', 'artifacts': None, 'symbols': None,
                'freshness': None, 'admitted_source_artifacts': None,
                'admitted_current_artifacts': None}
    freshness = {}
    for row in rows:
        key = row.get('freshness') or UNKNOWN
        freshness[key] = freshness.get(key, 0) + 1
    return {'status': 'AVAILABLE' if rows else 'NONE_RECORDED',
            'artifacts': len(rows),
            'symbols': len({row.get('canonical_symbol') for row in rows}),
            'freshness': freshness,
            'admitted_source_artifacts': sum(
                row.get('source_status') == 'ADMITTED' for row in rows),
            # Session-derived freshness only (app/ui/today.py); UNKNOWN is not CURRENT.
            'admitted_current_artifacts': sum(
                row.get('source_status') == 'ADMITTED'
                and row.get('freshness') == 'CURRENT' for row in rows)}


def egx_readiness(*, operational, security_master, daily_observations,
                  heartbeat, scan_history):
    """daily_observations must already carry registry source_status."""
    identities = _security_master(security_master)
    daily = _daily_observations(daily_observations)
    admitted = daily['admitted_source_artifacts']
    source = ('UNKNOWN' if admitted is None else
              'ADMITTED_SOURCE_PRESENT' if admitted else 'NO_ADMITTED_SOURCE')
    heartbeat_status = (heartbeat or {}).get('status') or UNKNOWN
    history_status = (scan_history or {}).get('status') or UNKNOWN
    receipts = {'configured': bool(operational.get('configured')),
                'available': bool(operational.get('available')),
                'status': operational.get('status') or UNKNOWN}

    blockers = []
    if identities['status'] != 'AVAILABLE':
        blockers.append('SECURITY_MASTER_UNAVAILABLE')
    if daily['status'] != 'AVAILABLE':
        blockers.append('DAILY_OBSERVATIONS_UNAVAILABLE')
    if source != 'ADMITTED_SOURCE_PRESENT':
        blockers.append('NO_ADMITTED_DAILY_SOURCE')
    elif not daily['admitted_current_artifacts']:
        blockers.append('ADMITTED_DATA_NOT_CURRENT')
    if heartbeat_status != 'RECENT_POLL':
        blockers.append('SCHEDULER_HEARTBEAT_' + ('STALE' if heartbeat_status == 'STALE'
                                                   else 'UNAVAILABLE'))
    if history_status != 'HISTORICAL_RUN':
        blockers.append('SCAN_HISTORY_UNAVAILABLE')
    if not receipts['configured']:
        blockers.append('OPERATIONAL_RECEIPTS_NOT_CONFIGURED')
    elif not receipts['available']:
        blockers.append('OPERATIONAL_RECEIPTS_UNAVAILABLE')

    if 'NO_ADMITTED_DAILY_SOURCE' in blockers:
        scan = 'EVIDENCE_BLOCKED'
    elif blockers:
        scan = 'NOT_READY'
    else:
        scan = 'PREREQUISITES_MET'
    canonical = ('AVAILABLE' if identities['status'] == 'AVAILABLE'
                 and daily['status'] == 'AVAILABLE' else
                 'PARTIAL' if 'AVAILABLE' in (identities['status'], daily['status'])
                 else 'UNAVAILABLE')
    return {'canonical_data': canonical, 'security_master': identities,
            'daily_observations': daily, 'source_admission': source,
            'scheduler_heartbeat': heartbeat_status, 'scan_history': history_status,
            'operational_receipts': receipts, 'scan_readiness': scan,
            'blockers': blockers,
            'dimensions': _dimensions(identities, daily, heartbeat_status,
                                      history_status, receipts, scan)}


def _dimensions(identities, daily, heartbeat_status, history_status, receipts, scan):
    """Tri-state readiness dimensions: True, False, or None when not evidenced.

    Later stages never follow from earlier availability: each requires its own
    evidence and every preceding stage.
    """
    admitted = daily['admitted_source_artifacts']
    current = daily['admitted_current_artifacts']
    source_admitted = None if admitted is None else admitted > 0
    source_fresh = (None if current is None else
                    False if not source_admitted else current > 0)
    scan_ready = scan == 'PREREQUISITES_MET'
    candidate_ready = scan_ready and source_fresh is True
    paper_ready = candidate_ready and receipts['available']
    dims = {
        'security_master_available': identities['status'] == 'AVAILABLE',
        # No dated authoritative membership reader exists; identities are not a universe.
        'authoritative_universe_available': False,
        'daily_observations_available': daily['status'] == 'AVAILABLE',
        'source_admitted': source_admitted,
        'source_fresh': source_fresh,
        'scheduler_heartbeat_available': heartbeat_status in ('RECENT_POLL', 'STALE'),
        # Poll liveness only; never job success or scan coverage.
        'scheduler_healthy': heartbeat_status == 'RECENT_POLL',
        'scan_history_available': history_status == 'HISTORICAL_RUN',
        'scan_ready': scan_ready,
        'candidate_pipeline_ready': candidate_ready,
        'paper_shadow_ready': paper_ready,
    }
    dims['overall_operational_ready'] = all(value is True for value in dims.values())
    return dims


def render_readiness(readiness):
    from html import escape
    content = ''
    for key, value in readiness.items():
        if value is None:
            content += f'<p>{key} readiness: UNKNOWN (no reader connected).</p>'
            continue
        identities, daily = value['security_master'], value['daily_observations']
        receipts = value['operational_receipts']
        receipt_text = ('NOT CONFIGURED' if not receipts['configured']
                        else receipts['status'] if receipts['available'] else 'UNAVAILABLE')

        def known(item):
            return 'UNKNOWN' if item is None else str(item)
        rows = (
            ('Canonical data', value['canonical_data']),
            ('Security master', f'{identities["status"]} · {known(identities["instruments"])} '
                                'instruments'),
            ('Daily observations', f'{daily["status"]} · {known(daily["artifacts"])} artifacts, '
                                   f'{known(daily["symbols"])} symbols'),
            ('Daily source admission', value['source_admission']),
            ('Scheduler heartbeat', value['scheduler_heartbeat']),
            ('Scan history', value['scan_history']),
            ('Operational receipts', receipt_text),
            ('Scan readiness', value['scan_readiness']),
        )
        content += (f'<section aria-label="{key} readiness"><h2>{key} readiness</h2>'
                    '<p>Components are reported separately. Identities or daily bars do not '
                    'make scanning ready; PREREQUISITES_MET is never a candidate or signal.</p>'
                    f'<table aria-label="{key} readiness components"><tbody>'
                    + ''.join(f'<tr><th scope="row">{label}</th><td>{escape(str(text))}</td></tr>'
                              for label, text in rows)
                    + '</tbody></table>'
                    f'<table aria-label="{key} readiness dimensions"><caption>Readiness '
                    'dimensions (UNKNOWN = not evidenced)</caption><tbody>'
                    + ''.join(f'<tr><th scope="row">{escape(name)}</th><td>'
                              + ('UNKNOWN' if flag is None else 'YES' if flag else 'NO')
                              + '</td></tr>' for name, flag in value['dimensions'].items())
                    + '</tbody></table><p>Blockers: '
                    + (escape(', '.join(value['blockers'])) or 'none recorded') + '</p></section>')
    return content
