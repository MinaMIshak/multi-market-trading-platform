"""Read-side gate for retained bytes of one REPORTED checked-metrics job result.

decision-outcome-metrics-report-reader-v1. First step toward a protected
read-only SYSTEM view of a host-published checked metrics report (same pattern
as ``app.scanner_v2_card_report_reader``: a pure gate first, then a publisher,
a protected loader and only then a route). Pure validation of exact retained
bytes: no file, network or database IO, no publisher, loader, route, page or
runtime caller, and nothing runs it.

A future publisher must retain the job's result as ``canonical_bytes`` (sorted
keys, compact, ASCII, no NaN) and record its SHA-256. The gate fails closed
(``RetainedMetricsReportError``) unless:

1. the payload is bounded bytes whose SHA-256 equals the caller's expected
   digest, is strict JSON (no duplicate keys, no NaN/Infinity) and re-encodes to
   exactly the same canonical bytes;
2. it is a REPORTED decision-outcome-metrics-job-v2 result with exactly the
   job's fields and fixed safety labels (writes NONE, performance_claim NONE,
   SHADOW, LIVE_MONEY=false, NOT_AUTHORIZED; booleans must be real booleans);
3. ``as_of`` and ``job_clock`` are normalised aware UTC instants with
   ``as_of`` at or before ``job_clock``;
4. the selection uses the job's rule labels, its counts are non-negative
   integers that add up (in population + recorded after = loaded; supplied +
   evaluated after = loaded) and the excluded identities are sorted, unique
   SHA-256 hex digests;
5. the embedded decision-outcome-checked-metrics-v1 report has exactly its
   fields and fixed labels, the same ``as_of``, counts consistent with the
   selection (outcomes supplied, attestations supplied = loaded, attestation
   counts summing to outcomes supplied, counted = attested CLEAR by ``as_of``,
   not counted = the rest, attested-after identities matching their count) and
   embedded metrics of the expected version, market, ``as_of``, population size
   and counted outcomes;
6. ``event_state_report`` is either NOT_REQUESTED or a
   decision-outcome-checked-event-state-metrics-v1 report with exactly its
   fields and fixed labels whose selection (counts, attested-after identities
   and partition) is identical to the checked report's, as the job computes both
   from the same inputs.

The gate checks integrity and internal consistency only. It does NOT recompute
any metric, partition, attestation or outcome, does not read the retained
stores, cannot prove population or attestation completeness (still
CALLER_ASSERTED_UNVERIFIED) and does not assess sample size. A digest is an
integrity check, not a signature. Outcomes are hypothetical results from session
bars: not fills, not performance. SYSTEM gaps are unchanged. PAPER/SHADOW only;
LIVE_MONEY=false.
"""
from datetime import datetime
from hashlib import sha256
import json
import re

from app.decision_observation import _utc, canonical_bytes
from app.decision_outcome_checked_event_states import (
    CONTRACT_VERSION as CHECKED_EVENT_STATES_VERSION)
from app.decision_outcome_checked_metrics import (
    ATTESTATION_TIME_RULE, CONTRACT_VERSION as CHECKED_VERSION, COUNT_KEYS)
from app.decision_outcome_event_states import CONTRACT_VERSION as EVENT_STATES_VERSION
from app.decision_outcome_metrics import CONTRACT_VERSION as METRICS_VERSION
from app.decision_outcome_metrics_job import (
    ATTESTATION_SELECTION, CONTRACT_VERSION as JOB_VERSION, EVENT_STATE_NOT_REQUESTED, SAFETY,
    SELECTION_RULE, SELECTION_TIME_BASIS)

CONTRACT_VERSION = 'decision-outcome-metrics-report-reader-v1'
MAX_BYTES = 16 * 1024 * 1024
_HEX64 = re.compile(r'[0-9a-f]{64}')

_TOP_KEYS = frozenset(('schema_version', 'status', 'as_of', 'job_clock', 'selection', 'report',
                       'event_state_report', *SAFETY))
_SELECTION_KEYS = frozenset((
    'rule', 'time_basis', 'observations_loaded', 'observations_in_population',
    'observations_recorded_after_as_of', 'outcomes_loaded', 'outcomes_supplied',
    'outcomes_evaluated_after_as_of', 'attestations_loaded', 'attestation_selection'))
# Selection fields shared by the checked report and the event-state report.
_SHARED_SELECTION_KEYS = ('outcome_basis', 'attestation_time_rule', 'outcomes_supplied',
                          'attestations_supplied', 'attestation_counts', 'outcomes_counted',
                          'outcomes_not_counted', 'attested_after_as_of', 'partition')
_COMMON_FIXED = {
    'market': 'EGX',
    'outcome_basis': 'ATTESTED_CLEAR_ONLY',
    'attestation_time_rule': ATTESTATION_TIME_RULE,
    'not_counted_treatment': 'NOT_EVALUATED_IN_SAME_POPULATION_NEVER_DROPPED',
    'unrestricted_metrics': 'NOT_REPORTED',
    'unattested_meaning': 'NOT_CHECKED_NOT_CLEAR',
    'absence_is_proof': False,
    'population_completeness': 'CALLER_ASSERTED_UNVERIFIED',
    'attestation_completeness': 'CALLER_ASSERTED_UNVERIFIED',
    'attestation_retention_time': 'NOT_RECORDED_NOT_CHECKED',
    'check_recomputation': 'NOT_PERFORMED',
    'review_verification': 'CALLER_ASSERTED_UNVERIFIED',
    'price_adjustment': 'NOT_APPLIED',
    'sample_assessment': 'NOT_ASSESSED',
    'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS',
    'execution_evidence': 'NONE',
    'performance_claim': 'NONE',
    'mode': 'SHADOW',
    'live_money': False,
    'execution_allowed': False,
    'runtime_activation': False,
    'promotion': 'NOT_AUTHORIZED',
}
_REPORT_FIXED = {**_COMMON_FIXED, 'contract_version': CHECKED_VERSION,
                 'cross_segment_headline': 'NOT_COMPUTED'}
_REPORT_KEYS = frozenset((*_REPORT_FIXED, *_SHARED_SELECTION_KEYS, 'as_of', 'metrics'))
_EVENT_STATE_FIXED = {
    **_COMMON_FIXED, 'contract_version': CHECKED_EVENT_STATES_VERSION,
    'checked_selection_version': CHECKED_VERSION,
    'event_state_metrics_version': EVENT_STATES_VERSION,
    'per_state_attestation_counts': 'NOT_REPORTED', 'evidence_layer': 'NEWS_EVENT_INFERENCE',
    'risk_interpretation': 'NOT_EVALUATED', 'trading_effect': 'NONE',
    'cross_state_headline': 'NOT_COMPUTED'}
_EVENT_STATE_KEYS = frozenset((*_EVENT_STATE_FIXED, *_SHARED_SELECTION_KEYS, 'as_of',
                               'event_state_metrics'))
_METRICS_FIXED = {
    'contract_version': METRICS_VERSION, 'market': 'EGX',
    'cross_segment_headline': 'NOT_COMPUTED', 'sample_assessment': 'NOT_ASSESSED',
    'performance_claim': 'NONE', 'mode': 'SHADOW', 'live_money': False,
    'execution_allowed': False, 'promotion': 'NOT_AUTHORIZED'}


class RetainedMetricsReportError(ValueError):
    """Retained metrics report failed closed; nothing from it may be displayed."""


def _fail(reason):
    raise RetainedMetricsReportError(reason)


def _require(condition, reason):
    if not condition:
        _fail(reason)


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            _fail('duplicate JSON key')
        out[key] = value
    return out


def _constant(name):
    _fail('non-finite JSON number')


def _obj(value, keys, what):
    _require(type(value) is dict and value.keys() == keys, f'{what} fields drifted')
    return value


def _fixed(doc, expected, what):
    # Exact type as well as value: JSON 0 must not pass for False.
    for key, value in expected.items():
        got = doc.get(key)
        _require(type(got) is type(value) and got == value, f'{what} {key} drifted')


def _count(value, what):
    _require(type(value) is int and value >= 0, f'invalid {what}')
    return value


def _identities(value, what):
    _require(type(value) is list and all(
        type(v) is str and _HEX64.fullmatch(v) is not None for v in value)
        and value == sorted(set(value)), f'invalid {what}')
    return value


def _instant(value, what):
    _require(type(value) is str and value.endswith('Z'), f'invalid {what}')
    try:
        parsed = datetime.fromisoformat(value[:-1] + '+00:00')
    except ValueError:
        _fail(f'invalid {what}')
    _require(parsed.tzinfo is not None and parsed.utcoffset() is not None
             and _utc(parsed) == value, f'{what} not a normalised UTC instant')
    return parsed


def _selection(selection):
    _obj(selection, _SELECTION_KEYS, 'selection')
    _fixed(selection, {'rule': SELECTION_RULE, 'time_basis': SELECTION_TIME_BASIS,
                       'attestation_selection': ATTESTATION_SELECTION}, 'selection')
    loaded = _count(selection['observations_loaded'], 'observations_loaded')
    population = _count(selection['observations_in_population'], 'observations_in_population')
    after = _identities(selection['observations_recorded_after_as_of'],
                        'observations_recorded_after_as_of')
    _require(population + len(after) == loaded, 'observation selection counts inconsistent')
    loaded = _count(selection['outcomes_loaded'], 'outcomes_loaded')
    supplied = _count(selection['outcomes_supplied'], 'outcomes_supplied')
    after = _identities(selection['outcomes_evaluated_after_as_of'],
                        'outcomes_evaluated_after_as_of')
    _require(supplied + len(after) == loaded, 'outcome selection counts inconsistent')
    _count(selection['attestations_loaded'], 'attestations_loaded')
    return selection


def _checked_selection(report, selection, what):
    supplied = _count(report['outcomes_supplied'], f'{what} outcomes_supplied')
    attestations = _count(report['attestations_supplied'], f'{what} attestations_supplied')
    _require(supplied == selection['outcomes_supplied']
             and attestations == selection['attestations_loaded'],
             f'{what} does not match the selection')
    counts = report['attestation_counts']
    _require(type(counts) is dict and tuple(sorted(counts)) == tuple(sorted(COUNT_KEYS)),
             f'{what} attestation counts drifted')
    _require(sum(_count(counts[key], f'{what} attestation count') for key in COUNT_KEYS)
             == supplied, f'{what} attestation counts do not cover every outcome')
    counted = _count(report['outcomes_counted'], f'{what} outcomes_counted')
    _require(counted == counts['ATTESTED_CLEAR_AT_OR_BEFORE_AS_OF']
             and report['outcomes_not_counted'] == supplied - counted
             and type(report['outcomes_not_counted']) is int,
             f'{what} counted outcomes inconsistent')
    after = _identities(report['attested_after_as_of'], f'{what} attested_after_as_of')
    _require(len(after) == counts['ATTESTED_CLEAR_AFTER_AS_OF'],
             f'{what} attested-after identities inconsistent')
    partition = report['partition']
    _require(type(partition) is dict
             and type(partition.get('outcomes_supplied')) is int
             and partition['outcomes_supplied'] == supplied
             and type(partition.get('attestations_supplied')) is int
             and partition['attestations_supplied'] == attestations,
             f'{what} partition does not match its counts')


def _report(report, selection, as_of_text):
    _obj(report, _REPORT_KEYS, 'checked report')
    _fixed(report, _REPORT_FIXED, 'checked report')
    _require(report['as_of'] == as_of_text, 'checked report as_of drifted')
    _checked_selection(report, selection, 'checked report')
    metrics = report['metrics']
    _require(type(metrics) is dict, 'checked report metrics drifted')
    _fixed(metrics, _METRICS_FIXED, 'checked report metrics')
    _require(metrics.get('as_of') == as_of_text, 'checked report metrics as_of drifted')
    population = _count(metrics.get('population_count'), 'metrics population_count')
    outcomes = _count(metrics.get('outcome_count'), 'metrics outcome_count')
    _require(population == selection['observations_in_population']
             and outcomes == report['outcomes_counted']
             and metrics.get('not_evaluated_count') == population - outcomes
             and type(metrics.get('not_evaluated_count')) is int,
             'checked report metrics counts inconsistent')
    return report


def _event_state_report(value, report, as_of_text):
    if type(value) is str:
        _require(value == EVENT_STATE_NOT_REQUESTED, 'event_state_report drifted')
        return value
    _obj(value, _EVENT_STATE_KEYS, 'event-state report')
    _fixed(value, _EVENT_STATE_FIXED, 'event-state report')
    _require(value['as_of'] == as_of_text, 'event-state report as_of drifted')
    _require(type(value['event_state_metrics']) is dict, 'event-state metrics drifted')
    # Same selected inputs and as_of as the checked report: identical selection
    # (compared as canonical bytes so JSON true cannot stand in for 1).
    _require(all(canonical_bytes(value[key]) == canonical_bytes(report[key])
                 for key in _SHARED_SELECTION_KEYS),
             'event-state report selection differs from the checked report')
    return value


def read_retained_egx_metrics_report(payload, *, expected_sha256):
    """Validate exact retained metrics-job result bytes; return the parsed document.

    Raises ``RetainedMetricsReportError`` on any drift. Callers must treat a
    failure as UNAVAILABLE and must not display partial content.
    """
    _require(type(payload) is bytes and 0 < len(payload) <= MAX_BYTES,
             'retained report must be bounded bytes')
    _require(type(expected_sha256) is str and _HEX64.fullmatch(expected_sha256) is not None,
             'invalid expected_sha256')
    _require(sha256(payload).hexdigest() == expected_sha256, 'retained report hash mismatch')
    try:
        doc = json.loads(payload.decode('ascii'), object_pairs_hook=_pairs,
                         parse_constant=_constant)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail('retained report is not strict JSON')
    _obj(doc, _TOP_KEYS, 'result')
    _fixed(doc, {'schema_version': JOB_VERSION, 'status': 'REPORTED', **SAFETY}, 'result')
    try:
        canonical = canonical_bytes(doc)
    except (TypeError, ValueError):
        _fail('retained report cannot be re-encoded')
    _require(canonical == payload, 'retained report is not canonical')
    as_of = _instant(doc['as_of'], 'as_of')
    _require(as_of <= _instant(doc['job_clock'], 'job_clock'), 'as_of after the job clock')
    selection = _selection(doc['selection'])
    report = _report(doc['report'], selection, doc['as_of'])
    _event_state_report(doc['event_state_report'], report, doc['as_of'])
    return doc
