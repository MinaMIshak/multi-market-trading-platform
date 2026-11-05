"""Read-only, bounded delivery journal projection; never a live health probe.

The host must publish a copy readable by the API via EGX_DELIVERY_RECEIPT.
Never point the API at the private delivery state directory or expose raw journals.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat

SHA = re.compile(r'[0-9a-f]{40}')
PHASES = {'STARTED', 'PREPARING', 'CUTOVER_PENDING', 'RECOVERING',
          'RECOVERY_REQUIRED', 'SUCCEEDED', 'FAILED', 'ROLLED_BACK'}
SOCKET_TESTS = {
    'tests/test_experimental_runtime.py::test_failed_candidate_preserves_previous',
    'tests/test_experimental_runtime.py::test_success_records_identity_without_stopping_child',
}


def sha(value):
    return value if isinstance(value, str) and SHA.fullmatch(value) else None


def accepted(value, commit, scheduler=False, sig=True):
    return (isinstance(value, dict) and commit is not None
            and value.get('commit') == commit and value.get('live_money') is False
            and all(value.get(k) is True for k in
                    ('http', 'api', 'runtime_compatible') + (('sig_v1',) if sig else ()))
            and (not scheduler or (value.get('scheduler_target') == commit
                                   and value.get('scheduler_verified') is True)))


# Fixed host evidence contracts; freshness never authorizes a release.
OBSERVATIONS = {
    'latest_codex_cycle': ({'SUCCEEDED', 'FAILED', 'INTERRUPTED'}, 86400),
    'latest_claude_review': ({'COMPLETED'}, 604800),
    'egx_chain_status': ({'COMPLETED', 'FAILED'}, 345600),
    'us_chain_status': ({'COMPLETED', 'FAILED'}, 345600),
}


def observation_records(raw):
    """Sanitize host attestations without copying free text or market counts."""
    if not isinstance(raw, dict) or raw.get('schema') != 'delivery-observations-v1':
        return {}
    result = {}
    for key, (statuses, _) in OBSERVATIONS.items():
        value = raw.get(key)
        if not isinstance(value, dict):
            continue
        status = value.get('status')
        digest = value.get('evidence_sha256')
        if (not isinstance(status, str) or status not in statuses
                or not sha(value.get('source_head'))
                or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)
                or value.get('live_money') is not False):
            continue
        try:
            observed = datetime.fromisoformat(value['observed_at'])
            if observed.tzinfo is None:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        result[key] = dict(status=status, source_head=value['source_head'],
            evidence_sha256=digest, observed_at=observed.isoformat(), live_money=False)
    return result


def project_observations(raw, now=None):
    now = now if now is not None else datetime.now(timezone.utc)
    result = {}
    for key, value in observation_records(raw).items():
        age = (now - datetime.fromisoformat(value['observed_at'])).total_seconds()
        freshness = 'FUTURE' if age < 0 else ('STALE' if age > OBSERVATIONS[key][1] else 'RECENT')
        result[key] = dict(value, freshness=freshness, evidence_scope='HOST_ATTESTATION_ONLY')
    return result


def project_delivery(raw, *, now=None):
    result = dict.fromkeys(('source_head', 'origin_sync', 'deployed_release',
                           'latest_codex_cycle', 'latest_claude_review', 'tests',
                           'candidate_validation', 'deployment_result', 'scheduler_target',
                           'scheduler_health', 'egx_chain_status', 'us_chain_status',
                           'blocker', 'started_at', 'live_money'))
    result.update(status='UNAVAILABLE', evidence_scope='HISTORICAL_RECEIPT_ONLY',
                  runtime_verified=False)
    if isinstance(raw, dict) and raw.get('schema') == 'delivery-observation-publication-v1':
        result.update(project_observations(raw.get('observations'), now))
        return result
    if not isinstance(raw, dict) or raw.get('schema') != 'delivery-state-v1':
        return result
    head = sha(raw.get('source_head'))
    phase = raw.get('status')
    if not head or not isinstance(phase, str) or phase not in PHASES or raw.get('live_money') is not False:
        return result
    try:
        started = datetime.fromisoformat(raw['started_at'])
        if started.tzinfo is None:
            return result
    except (KeyError, ValueError, TypeError):
        return result
    result.update(status='AVAILABLE', source_head=head, started_at=started.isoformat(),
                  deployment_result=phase, live_money=False)
    if raw.get('origin_sync') == 'VERIFIED':
        result['origin_sync'] = 'VERIFIED_AT_DELIVERY'
    tests = raw.get('tests')
    if (isinstance(tests, dict) and tests.get('commit') == head
            and all(tests.get(k) is True for k in ('full_suite', 'reporting_only', 'runtime_rss'))
            and isinstance(tests.get('socket_tests'), dict)
            and set(tests['socket_tests']) == SOCKET_TESTS
            and all(v is True for v in tests['socket_tests'].values())):
        result['tests'] = 'PASSED_AT_DELIVERY'
    candidate_ok = accepted(raw.get('candidate_validation'), head)
    if candidate_ok:
        result['candidate_validation'] = 'PASSED_AT_DELIVERY'
    deployed = sha(raw.get('deployed_release'))
    verified = False
    if phase == 'SUCCEEDED':
        verified = (deployed == head and result['tests'] is not None and candidate_ok
                    and result['origin_sync'] is not None
                    and accepted(raw.get('production_validation'), head, scheduler=True))
    elif phase == 'ROLLED_BACK':
        verified = accepted(raw.get('rollback_validation'), deployed, scheduler=True, sig=False)
    if verified and raw.get('scheduler_target') == deployed:
        result.update(deployed_release=deployed, scheduler_target=deployed)
    elif phase in ('SUCCEEDED', 'ROLLED_BACK'):
        result['deployment_result'] = 'UNVERIFIED_TERMINAL_RECEIPT'
    if phase in ('FAILED', 'RECOVERY_REQUIRED', 'RECOVERING', 'ROLLED_BACK'):
        result['blocker'] = 'RECOVERY_REQUIRED' if phase in ('RECOVERY_REQUIRED', 'RECOVERING') else 'DELIVERY_FAILED'
    result.update(project_observations(raw.get("observations"), now))
    # Raw errors, rollback schedule, arbitrary metadata and paths never leave here.
    return result


def public_receipt(raw):
    """Reconstruct a minimal receipt without copying private strings or mappings."""
    view = project_delivery(raw)
    if view['status'] != 'AVAILABLE':
        observations = (observation_records(raw.get('observations'))
                        if isinstance(raw, dict) and raw.get('schema') ==
                        'delivery-observation-publication-v1' else {})
        return (dict(schema='delivery-observation-publication-v1',
                     observations=dict(schema='delivery-observations-v1', **observations))
                if observations else {})
    result = dict(schema='delivery-state-v1', source_head=view['source_head'],
                  started_at=view['started_at'], live_money=False, status=raw['status'])
    if view['origin_sync']:
        result['origin_sync'] = 'VERIFIED'
    if view['tests']:
        result['tests'] = dict(commit=view['source_head'], full_suite=True,
            reporting_only=True, runtime_rss=True,
            socket_tests={key: True for key in sorted(SOCKET_TESTS)})
    def gate(commit, scheduler=False):
        value = dict(commit=commit, live_money=False, http=True, api=True,
                     runtime_compatible=True, sig_v1=True)
        if scheduler:
            value.update(scheduler_target=commit, scheduler_verified=True)
        return value
    if view['candidate_validation']:
        result['candidate_validation'] = gate(view['source_head'])
    if view['deployed_release']:
        commit = view['deployed_release']
        result.update(deployed_release=commit, scheduler_target=commit)
        key = 'rollback_validation' if raw['status'] == 'ROLLED_BACK' else 'production_validation'
        result[key] = gate(commit, scheduler=True)
        if key == 'rollback_validation':
            result[key].pop('sig_v1')
    observations = observation_records(raw.get('observations'))
    if observations:
        result['observations'] = dict(schema='delivery-observations-v1', **observations)
    return result


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate receipt key')
        result[key] = value
    return result


def load_delivery_status():
    configured = os.environ.get('EGX_DELIVERY_RECEIPT')
    if not configured:
        return project_delivery(None)
    try:
        path = Path(configured)
        if not path.is_absolute():
            return project_delivery(None)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                return project_delivery(None)
            payload = source.read(131073)
        if len(payload) > 131072:
            return project_delivery(None)
        return project_delivery(json.loads(payload, object_pairs_hook=_unique))
    except (OSError, ValueError, RecursionError):
        return project_delivery(None)
