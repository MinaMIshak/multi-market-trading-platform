"""decision-outcome-metrics-report-reader-v1: fail-closed gate for retained metrics bytes."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from app.decision_observation import canonical_bytes
from app.decision_outcome_metrics_job import MetricsJobError, _failure
from app.decision_outcome_metrics_reader import (
    CONTRACT_VERSION, RetainedMetricsReportError, read_retained_egx_metrics_report)
from tests.test_decision_outcome_action_attestation import checked  # noqa: F401
from tests.test_decision_outcome_metrics_job import (  # noqa: F401
    _private_tmp, attest, run, stores)

HEX = 'a' * 64


def encode(document):
    payload = canonical_bytes(document)
    return payload, sha256(payload).hexdigest()


def read(document):
    payload, digest = encode(document)
    return read_retained_egx_metrics_report(payload, expected_sha256=digest)


def plain(document):
    """The JSON form a retained copy has (tuples in the job result become lists)."""
    return json.loads(canonical_bytes(document))


@pytest.fixture
def result(stores):
    attest(stores)
    return plain(run(stores, by_event_state=True))


def test_contract_version():
    assert CONTRACT_VERSION == 'decision-outcome-metrics-report-reader-v1'


@pytest.mark.parametrize('by_event_state', [False, True])
def test_accepts_exact_canonical_job_result(stores, by_event_state):
    attest(stores)
    out = plain(run(stores, by_event_state=by_event_state))
    assert (out['event_state_report'] == 'NOT_REQUESTED') is (not by_event_state)
    payload, digest = encode(out)
    assert read_retained_egx_metrics_report(payload, expected_sha256=digest) == out
    # Deterministic: same bytes, same document.
    assert read_retained_egx_metrics_report(payload, expected_sha256=digest) == out


def test_accepts_unattested_result_without_upgrading_it(stores):
    out = plain(run(stores))
    assert out['report']['outcomes_counted'] == 0
    doc = read(out)
    assert doc['report']['attestation_counts']['UNATTESTED'] == 1
    assert doc['report']['metrics']['not_evaluated_count'] == 1


def test_refuses_digest_mismatch_and_unbounded_input(result):
    payload, digest = encode(result)
    with pytest.raises(RetainedMetricsReportError, match='hash mismatch'):
        read_retained_egx_metrics_report(payload, expected_sha256=HEX)
    with pytest.raises(RetainedMetricsReportError, match='expected_sha256'):
        read_retained_egx_metrics_report(payload, expected_sha256=digest.upper())
    for bad in (b'', payload.decode('ascii'), bytearray(payload)):
        with pytest.raises(RetainedMetricsReportError, match='bounded bytes'):
            read_retained_egx_metrics_report(bad, expected_sha256=digest)


def test_refuses_non_canonical_bytes_even_with_matching_digest(result):
    payload = json.dumps(result, sort_keys=True, indent=1).encode('ascii')
    with pytest.raises(RetainedMetricsReportError, match='not canonical'):
        read_retained_egx_metrics_report(payload, expected_sha256=sha256(payload).hexdigest())


@pytest.mark.parametrize('payload', [
    b'{"a":1,"a":2}', b'{"a":NaN}', b'[1]', b'not json', '{"a":"é"}'.encode('utf-8')])
def test_refuses_non_strict_json(payload):
    with pytest.raises(RetainedMetricsReportError):
        read_retained_egx_metrics_report(payload, expected_sha256=sha256(payload).hexdigest())


def test_refuses_failed_job_result():
    failed = _failure('METRICS', MetricsJobError('METRICS_REJECTED', 'x', stage='METRICS'))
    with pytest.raises(RetainedMetricsReportError, match='fields drifted'):
        read(failed)


def _set(path, value):
    def mutate(doc):
        target = doc
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return mutate


def _delete(key):
    def mutate(doc):
        del doc[key]
    return mutate


def _bump_counted(doc):
    doc['report']['outcomes_counted'] += 1
    doc['report']['outcomes_not_counted'] -= 1


def _shift_counts(doc):
    counts = doc['report']['attestation_counts']
    counts['ATTESTED_CLEAR_AT_OR_BEFORE_AS_OF'] -= 1
    counts['UNATTESTED'] += 1


def _event_state_differs(doc):
    doc['event_state_report']['attestation_counts']['UNATTESTED'] = True


MUTATIONS = {
    'live money on': _set(('live_money',), True),
    'live money as integer': _set(('live_money',), 0),
    'execution allowed': _set(('execution_allowed',), True),
    'promotion': _set(('promotion',), 'AUTHORIZED'),
    'performance claimed': _set(('performance_claim',), 'EDGE'),
    'writes': _set(('writes',), 'STORE'),
    'schema version': _set(('schema_version',), 'decision-outcome-metrics-job-v1'),
    'status': _set(('status',), 'FAILED'),
    'extra top-level key': _set(('headline',), '90% hit rate'),
    'missing top-level key': _delete('selection'),
    'as_of after job clock': _set(('as_of',), '2026-11-09T15:00:01Z'),
    'naive as_of': _set(('as_of',), '2026-11-09T15:00:00'),
    'offset as_of': _set(('job_clock',), '2026-11-09T17:00:00+02:00'),
    'selection rule': _set(('selection', 'rule'), 'ALL'),
    'selection counts': _set(('selection', 'observations_loaded'), 2),
    'selection identities': _set(('selection', 'outcomes_evaluated_after_as_of'), ['x']),
    'report version': _set(('report', 'contract_version'), 'other'),
    'report as_of': _set(('report', 'as_of'), '2026-11-08T15:00:00Z'),
    'report market': _set(('report', 'market'), 'US'),
    'report unrestricted metrics': _set(('report', 'unrestricted_metrics'), {}),
    'report absence is proof': _set(('report', 'absence_is_proof'), True),
    'report live money': _set(('report', 'live_money'), True),
    'report extra key': _set(('report', 'hit_rate'), '1'),
    'report counted inflated': _bump_counted,
    'report counts shifted': _shift_counts,
    'report attestations supplied': _set(('report', 'attestations_supplied'), 5),
    'report attested after': _set(('report', 'attested_after_as_of'), [HEX]),
    'metrics version': _set(('report', 'metrics', 'contract_version'), 'other'),
    'metrics population': _set(('report', 'metrics', 'population_count'), 7),
    'metrics headline': _set(('report', 'metrics', 'cross_segment_headline'), 'COMPUTED'),
    'metrics live money': _set(('report', 'metrics', 'live_money'), True),
    'event-state other string': _set(('event_state_report',), 'SKIPPED'),
    'event-state version': _set(('event_state_report', 'contract_version'), 'other'),
    'event-state risk interpreted': _set(('event_state_report', 'risk_interpretation'), 'LOW'),
    'event-state trading effect': _set(('event_state_report', 'trading_effect'), 'BUY'),
    'event-state as_of': _set(('event_state_report', 'as_of'), '2026-11-08T15:00:00Z'),
    'event-state selection differs': _event_state_differs,
}


@pytest.mark.parametrize('name', sorted(MUTATIONS))
def test_refuses_drift(result, name):
    doc = deepcopy(result)
    MUTATIONS[name](doc)
    with pytest.raises(RetainedMetricsReportError):
        read(doc)
    # The unmutated result is still accepted (the fixture itself is valid).
    assert read(result) == result


def test_does_not_mutate_or_recompute(result):
    before = deepcopy(result)
    payload, digest = encode(result)
    doc = read_retained_egx_metrics_report(payload, expected_sha256=digest)
    assert result == before
    # Returned values are the retained ones; nothing is recomputed or relabelled.
    assert doc['report']['metrics'] == result['report']['metrics']
    assert doc['live_money'] is False and doc['performance_claim'] == 'NONE'
