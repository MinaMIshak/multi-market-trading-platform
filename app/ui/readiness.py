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
                'freshness': None, 'admitted_source_artifacts': None}
    freshness = {}
    for row in rows:
        key = row.get('freshness') or UNKNOWN
        freshness[key] = freshness.get(key, 0) + 1
    return {'status': 'AVAILABLE' if rows else 'NONE_RECORDED',
            'artifacts': len(rows),
            'symbols': len({row.get('canonical_symbol') for row in rows}),
            'freshness': freshness,
            'admitted_source_artifacts': sum(
                row.get('source_status') == 'ADMITTED' for row in rows)}


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
            'blockers': blockers}


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
                    + '</tbody></table><p>Blockers: '
                    + (escape(', '.join(value['blockers'])) or 'none recorded') + '</p></section>')
    return content
