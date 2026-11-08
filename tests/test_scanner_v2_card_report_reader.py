"""Artificial retained evidence only; no admission, market coverage or edge evidence.

Each tamper case is re-hashed so the SHA-256 gate passes and only the
consistency gates can reject it: a digest is integrity, not authenticity.
"""
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import json

import pytest

from app.scanner_v2_card_report_reader import (
    MAX_BYTES, RetainedCardReportError, read_retained_egx_card_report)
from tests.test_scanner_v2_card_report import VIEW, project
from tests.test_scanner_v2_egx_rank import NEXT_CLOSE, record, report
from tests.test_scanner_v2_retained_binding import OPEN


def encode(doc):
    return json.dumps(doc, sort_keys=True, allow_nan=False).encode()


def read(payload):
    return read_retained_egx_card_report(payload, expected_sha256=sha256(payload).hexdigest())


@pytest.mark.parametrize('observed_at', [VIEW, OPEN, NEXT_CLOSE])
def test_producer_output_round_trips_at_every_card_state(observed_at):
    payload = encode(project(observed_at=observed_at))
    assert read(payload) == json.loads(payload)


def test_report_without_cards_round_trips():
    artifact = report([record('AAAA', 'WATCHLIST'), record('BBBB', 'NO_TRADE')])
    payload = encode(project(artifact=artifact))
    out = read(payload)
    assert out['cards'] == [] and out['coverage']['candidate_count'] == 0


def test_hash_mismatch_and_unbounded_input_rejected():
    payload = encode(project())
    with pytest.raises(RetainedCardReportError, match='hash mismatch'):
        read_retained_egx_card_report(payload, expected_sha256='0' * 64)
    with pytest.raises(RetainedCardReportError, match='expected_sha256'):
        read_retained_egx_card_report(payload, expected_sha256=sha256(payload).hexdigest().upper())
    for bad in (bytearray(payload), payload.decode(), b'', b' ' * (MAX_BYTES + 1)):
        with pytest.raises(RetainedCardReportError, match='bounded bytes'):
            read_retained_egx_card_report(bad, expected_sha256=sha256(payload).hexdigest())


@pytest.mark.parametrize('payload,reason', [
    (b'{"a":1,"a":2}', 'duplicate JSON key'),
    (b'{"a":NaN}', 'non-finite'),
    (b'{"a":Infinity}', 'non-finite'),
    (b'{"a":', 'strict JSON'),
    (b'\xff\xfe', 'strict JSON'),
    (b'[]', 'report fields drifted'),
])
def test_non_strict_json_rejected(payload, reason):
    with pytest.raises(RetainedCardReportError, match=reason):
        read(payload)


def put(path, value):
    def mutate(doc):
        target = doc
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return mutate


def drop(path):
    def mutate(doc):
        target = doc
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]
    return mutate


def bump(path):
    def mutate(doc):
        target = doc
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] += 1
    return mutate


def relabel_watchlist_exclusion(doc):
    cov = doc['coverage']
    row = cov['symbols'][2]
    assert (row['classification'], row['reason']) == ('WATCHLIST', 'SETUP_NOT_CONFIRMED')
    row['reason'] = 'NO_TRADE'
    cov['exclusion_counts']['SETUP_NOT_CONFIRMED'] -= 1
    cov['exclusion_counts']['NO_TRADE'] += 1


def swap_cards(doc):
    doc['cards'].reverse()


def drop_cards(doc):
    doc['cards'] = []


def edit_stop_only(doc):
    doc['cards'][0]['strategy_inference']['plan']['stop'] = '0.01'


def drop_ranking_evidence(doc):
    refs = doc['cards'][0]['observed_fact']['provenance']['evidence_ids']
    doc['cards'][0]['observed_fact']['provenance']['evidence_ids'] = [
        r for r in refs if not r.startswith('egx-ranking-sha256:')]


def extend_card_freshness(doc):
    doc['cards'][0]['observed_fact']['provenance']['fresh_until'] = '2099-01-01T00:00:00Z'


def future_evidence(doc):
    doc['provenance']['evidence_available_at'] = '2099-01-01T00:00:00+00:00'


def naive_observation(doc):
    doc['observed_at'] = doc['observed_at'].replace('+00:00', '')


def mismatched_card_symbol(doc):
    doc['cards'][0]['observed_fact']['symbol'] = 'ZZZZ'


@pytest.mark.parametrize('mutate', [
    put(('live_money',), True),
    put(('execution_allowed',), True),
    put(('runtime_activation',), True),
    put(('mode',), 'LIVE'),
    put(('promotion',), 'AUTHORIZED'),
    put(('risk_status',), 'APPROVED'),
    put(('feature_status',), 'ACTIVATED'),
    put(('market',), 'US'),
    put(('extra',), 1),
    drop(('limitations',)),
    put(('limitations',), []),
    put(('lifecycle_evidence', 'sample_size'), 10),
    put(('lifecycle_evidence', 'status'), 'VERIFIED'),
    put(('coverage', 'authoritative_universe_size'), 224),
    bump(('coverage', 'requested')),
    bump(('coverage', 'candidate_count')),
    bump(('coverage', 'excluded')),
    bump(('coverage', 'classification_counts', 'NO_TRADE')),
    bump(('coverage', 'exclusion_counts', 'NO_TRADE')),
    relabel_watchlist_exclusion,
    put(('provenance', 'calendar_admission'), 'ADMITTED'),
    put(('provenance', 'identity_admission'), 'ADMITTED'),
    put(('provenance', 'freshness_policy'), 'ALWAYS_FRESH'),
    put(('provenance', 'retained_evidence', 'live_money'), True),
    put(('provenance', 'retained_evidence', 'calendar_admission'), 'ADMITTED'),
    put(('provenance', 'retained_evidence', 'evidence_available_at'), '2026-01-01T00:00:00+00:00'),
    put(('provenance', 'retained_evidence', 'binding', 'fresh_until'), '2099-01-01T00:00:00Z'),
    put(('provenance', 'retained_evidence', 'binding', 'session'), '1999-01-01'),
    put(('provenance', 'session'), '1999-01-01'),
    future_evidence,
    naive_observation,
    swap_cards,
    drop_cards,
    put(('cards', 0, 'live_money'), True),
    put(('cards', 0, 'execution_allowed'), True),
    put(('cards', 0, 'expected_return'), '0.10'),
    put(('cards', 0, 'risk_decision', 'status'), 'APPROVED'),
    put(('cards', 0, 'risk_decision', 'risk_per_share'), '0.01'),
    put(('cards', 0, 'research_evidence', 'confidence'), 'HIGH'),
    edit_stop_only,
    drop_ranking_evidence,
    extend_card_freshness,
    mismatched_card_symbol,
    bump(('card_state_counts', 'RESEARCH_CANDIDATE')),
])
def test_tampered_retained_report_rejected(mutate):
    doc = deepcopy(project())
    mutate(doc)
    with pytest.raises(RetainedCardReportError):
        read(encode(doc))


def test_stale_card_relabeled_fresh_is_rejected():
    doc = deepcopy(project(observed_at=OPEN))
    assert doc['card_state_counts']['DATA_STALE'] == 2
    doc['cards'][0]['invalidation_reason'] = None
    doc['cards'][0]['status'] = 'RESEARCH_CANDIDATE'
    doc['card_state_counts'] = {'RESEARCH_CANDIDATE': 1, 'DATA_STALE': 1, 'PLAN_EXPIRED': 0}
    with pytest.raises(RetainedCardReportError, match='card state'):
        read(encode(doc))


def test_expired_card_relabeled_stale_is_rejected():
    doc = deepcopy(project(observed_at=NEXT_CLOSE))
    doc['cards'][0]['invalidation_reason'] = 'DATA_STALE'
    doc['card_state_counts'] = {'RESEARCH_CANDIDATE': 0, 'DATA_STALE': 1, 'PLAN_EXPIRED': 1}
    with pytest.raises(RetainedCardReportError, match='card state'):
        read(encode(doc))


def consistent_stop_edit(doc):
    # Keeps geometry and recomputed risk per share consistent: only the
    # rebuilt decision digest can detect it.
    card = doc['cards'][0]
    plan = card['strategy_inference']['plan']
    stop = Decimal(plan['stop']) - Decimal('0.01')
    assert stop > 0
    plan['stop'] = str(stop)
    card['risk_decision']['risk_per_share'] = str(Decimal(plan['entry_reference']) - stop)


def edit_signal_evidence(doc):
    research = doc['cards'][0]['research_evidence']
    research['signal_evidence_json'] = research['signal_evidence_json'] + ' '


def forge_decision_id_everywhere(doc):
    forged = 'a' * 64
    doc['cards'][0]['decision_id'] = forged
    row = next(r for r in doc['coverage']['symbols'] if r.get('decision_id')
               and r['symbol'] == doc['cards'][0]['observed_fact']['symbol'])
    row['decision_id'] = forged


def edit_setup_version(doc):
    doc['cards'][0]['strategy_inference']['setup_version'] = '2'


def noncanonical_instrument_id(doc):
    source = doc['cards'][0]['observed_fact']['provenance']
    source['instrument_id'] = '{' + source['instrument_id'] + '}'


@pytest.mark.parametrize('mutate,reason', [
    (consistent_stop_edit, 'decision_id does not match'),
    (edit_signal_evidence, 'decision_id does not match'),
    (forge_decision_id_everywhere, 'decision_id does not match'),
    (edit_setup_version, 'decision_id does not match'),
    (put(('cards', 0, 'research_evidence', 'candidate_sha256'), '0' * 64),
     'candidate_sha256 does not match'),
    (noncanonical_instrument_id, 'projection of rebuilt decision'),
    (put(('cards', 0, 'observed_fact', 'provenance', 'instrument_id'), 7), 'instrument_id'),
    (put(('cards', 0, 'observed_fact', 'provenance', 'instrument_id'), 'not-a-uuid'),
     'cannot be rebuilt'),
    (put(('cards', 0, 'strategy_inference', 'setup'), 1), 'cannot be rebuilt'),
    (put(('cards', 0, 'observed_fact', 'provenance', 'evidence_ids'), [{}]),
     'provenance not bound'),
])
def test_card_must_equal_projection_of_rebuilt_decision(mutate, reason):
    doc = deepcopy(project())
    mutate(doc)
    with pytest.raises(RetainedCardReportError, match=reason):
        read(encode(doc))


@pytest.mark.parametrize('observed_at', [VIEW, OPEN, NEXT_CLOSE])
def test_rebuilt_digests_match_producer_cards(observed_at):
    doc = project(observed_at=observed_at)
    assert doc['cards']
    out = read(encode(doc))
    assert [c['decision_id'] for c in out['cards']] == [
        r['decision_id'] for r in out['coverage']['symbols'] if r['status'] == 'RESEARCH_CANDIDATE']


def test_rejection_is_value_error_for_existing_fail_closed_callers():
    assert issubclass(RetainedCardReportError, ValueError)
