"""decision-outcome-production-v3: retained observations + daily evidence -> retained outcomes."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from hashlib import sha256
from uuid import UUID

import pytest

from app.decision_observation import canonical_bytes
from app.decision_observation_store import (
    load_retained_decision_observations, retain_decision_observation)
from app.decision_outcome import TOUCH, OutcomeAssumptions, evaluate_decision_outcome
from app.decision_outcome_action_attestation import (
    attest_outcome_corporate_actions, verify_outcome_action_attestation)
from app.decision_outcome_action_evidence import OutcomeActionSelection
from app.decision_outcome_attestation_store import load_retained_outcome_attestations
from app.decision_outcome_attested import partition_egx_attested_outcomes
from app.decision_outcome_corporate_actions import WINDOW_RULE, check_egx_outcome_corporate_actions
from app.decision_outcome_metrics import aggregate_decision_outcomes
from app.decision_outcome_producer import (
    ACTION_PATH_RULE, ATTESTATION_STATUSES, AWAITING_REASONS, CALENDAR_RULE, CONTRACT_VERSION,
    ENTRY_STATUSES, PRODUCTION_REASONS, DecisionOutcomeProductionError, EgxDailyEvidence,
    produce_egx_decision_outcomes)
from app.decision_outcome_session_bars import bind_egx_outcome_session_bars
from app.decision_outcome_store import load_retained_decision_outcomes
from app.strategies.contracts import Candidate
from app.strategies.trade_decision import DecisionSource, DecisionTerms, TradeDecision
from tests.test_decision_outcome_corporate_actions import (
    REVIEWS, SOURCE, action, evidence as action_evidence)
from tests.test_decision_outcome_session_bars import (
    AT, CAL, INSTRUMENT, LOOKBACK, RECEIVED, ROWS, VALID_UNTIL, ZERO, row, session)

UTC = timezone.utc
NOW = RECEIVED + timedelta(hours=1)
OTHER = UUID(int=8)
SAFETY = {'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS', 'execution_evidence': 'NONE',
          'performance_claim': 'NONE', 'mode': 'SHADOW', 'live_money': False,
          'execution_allowed': False, 'runtime_activation': False,
          'promotion': 'NOT_AUTHORIZED'}


@pytest.fixture(autouse=True)
def _private_tmp(tmp_path):
    # The stores refuse group/world-writable directories; do not depend on umask.
    tmp_path.chmod(0o700)


@pytest.fixture
def stores(tmp_path):
    observations, outcomes = tmp_path / 'observations', tmp_path / 'outcomes'
    observations.mkdir(mode=0o700)
    outcomes.mkdir(mode=0o700)
    return observations, outcomes


def decision(symbol='TEST', instrument=INSTRUMENT, at=AT):
    candidate = Candidate(strategy_id='fixture-challenger', strategy_version='1',
                          config_id='fixture', symbol=symbol, decision_time=at,
                          data_cutoff=at - timedelta(minutes=1), state='READY',
                          entry_reference=None, stop_reference=None,
                          evidence_json='{"fixture":true}')
    source = DecisionSource(market='EGX', symbol=symbol, instrument_id=instrument,
                            source_id='fixture', artifact_sha256='a' * 64,
                            evidence_ids=('fixture-only',), data_as_of=candidate.data_cutoff,
                            available_at=at, fresh_until=at + timedelta(hours=1),
                            completed_bar=True)
    terms = DecisionTerms(entry_trigger=TOUCH, entry_low=D('9.5'), entry_high=D('10.5'),
                          entry_reference=D('10'), stop=D('9'), target=D('12'),
                          valid_until=VALID_UNTIL, max_holding_sessions=3, flat_by=None)
    return TradeDecision(candidate=candidate, source=source, terms=terms)


def observe(directory, *decisions):
    for item in decisions or (decision(),):
        retain_decision_observation(str(directory), item, None, now=AT + timedelta(minutes=5))
    return load_retained_decision_observations(str(directory), market='EGX')


@pytest.fixture
def sidecar(tmp_path):
    directory = tmp_path / 'attestations'
    directory.mkdir(mode=0o700)
    return directory


def produce(stores, daily=None, sessions=CAL, now=NOW, assumptions=ZERO, actions=None,
            attestations=None):
    observations, outcomes = stores
    daily = {str(INSTRUMENT): EgxDailyEvidence(ROWS, RECEIVED)} if daily is None else daily
    kwargs = {} if attestations is None else {'attestation_directory': str(attestations)}
    return produce_egx_decision_outcomes(str(observations), str(outcomes), daily=daily,
                                         sessions=sessions, assumptions=assumptions, now=now,
                                         actions=actions, **kwargs)


def selection(doc=None):
    """The exact ReferenceRepository.actions selection shape (fixture document)."""
    return OutcomeActionSelection(evidence=action_evidence() if doc is None else doc,
                                  source_ingestion_id=SOURCE, review_ingestion_ids=REVIEWS)


def snapshot(directory):
    return sorted((p.name, p.read_bytes()) for p in directory.iterdir())


def refused(reason, call, *args, **kwargs):
    assert reason in PRODUCTION_REASONS
    with pytest.raises(DecisionOutcomeProductionError) as info:
        call(*args, **kwargs)
    assert info.value.reason == reason
    return info.value


def test_terminal_outcome_is_evaluated_from_retained_evidence_and_retained(stores):
    (identity, record), = observe(stores[0])
    out = produce(stores)
    assert out['schema_version'] == CONTRACT_VERSION == 'decision-outcome-production-v3'
    assert out['market'] == 'EGX' and out['observations'] == 1
    assert out['produced_at'] == '2026-11-09T15:00:00Z'
    (entry,) = out['outcomes']
    assert entry['observation_identity'] == identity
    assert entry['decision_id'] == record['decision_id']
    assert (entry['status'], entry['outcome_status'], entry['outcome_reason']) == (
        'RETAINED', 'CLOSED', None)
    assert entry['sessions_bound'] == ['2026-11-05', '2026-11-08', '2026-11-09']
    assert entry['path_complete_through_at'] == '2026-11-09T14:00:00Z'

    # The retained outcome is exactly the unchanged binding + evaluator result.
    bound = bind_egx_outcome_session_bars(identity, record, ROWS, CAL, received_at=RECEIVED)
    expected = evaluate_decision_outcome(identity, record, bound['bars'], assumptions=ZERO,
                                         path_complete_through_at=bound['path_complete_through_at'],
                                         evaluated_at=NOW)
    held = load_retained_decision_outcomes(str(stores[1]), market='EGX', assumptions=ZERO)
    assert held == (expected,)
    assert entry['outcome_identity'] == sha256(canonical_bytes(expected)).hexdigest()
    assert expected['exit']['reason'] == 'TARGET'

    metrics = aggregate_decision_outcomes(
        load_retained_decision_observations(str(stores[0]), market='EGX'), held,
        market='EGX', assumptions=ZERO, as_of=NOW)
    assert metrics['contract_version'] == 'decision-outcome-metrics-v1'


def test_result_is_labelled_and_claims_nothing(stores):
    observe(stores[0])
    out = produce(stores)
    assert {k: out[k] for k in SAFETY} == SAFETY
    assert out['calendar_rule'] == CALENDAR_RULE == 'CALENDAR_THROUGH_RECEIPT_DATE_V1'
    # Without action selections the v1 behaviour is kept and labelled unchecked.
    assert out['corporate_actions'] == 'NOT_CHECKED'
    assert out['corporate_action_window_rule'] is None
    assert out['corporate_action_path_rule'] is None
    assert out['action_selections_without_observation'] is None
    assert out['retained_outcome_corporate_actions'] == 'NOT_RECORDED_IN_OUTCOME'
    # The v3 attestation step is opt-in and off by default.
    assert out['outcome_attestation'] == 'NOT_REQUESTED'
    assert out['attestation_counts'] is None
    assert out['held_outcome_attestation'] == 'NOT_PERFORMED'
    assert out['outcomes'][0]['attestation_status'] is None
    assert out['outcomes'][0]['attestation_identity'] is None
    assert out['price_adjustment'] == 'NOT_APPLIED'
    assert out['outcomes'][0]['corporate_action_status'] is None
    assert out['outcomes'][0]['corporate_action_check'] is None
    assert out['calendar_admission'] == 'NOT_ESTABLISHED'
    assert out['daily_receipt_clock'] == 'CALLER_ASSERTED_UNVERIFIED'
    assert out['retained_outcome_reevaluation'] == 'NOT_PERFORMED'
    assert out['produced_at_clock'] == 'PRODUCER_CLOCK_UNVERIFIED'
    assert out['assumptions'] == ZERO.model_dump(mode='json')
    assert tuple(out['status_counts']) == ENTRY_STATUSES
    assert out['status_counts']['RETAINED'] == 1 and sum(out['status_counts'].values()) == 1
    assert out['retained_outcomes_without_observation'] == 0


def test_rerun_keeps_the_first_outcome_and_does_not_reevaluate(stores):
    observe(stores[0])
    first = produce(stores)
    before = snapshot(stores[1])
    later = produce(stores, now=NOW + timedelta(days=3))
    (entry,) = later['outcomes']
    assert entry['status'] == 'ALREADY_RETAINED'
    assert entry['outcome_identity'] == first['outcomes'][0]['outcome_identity']
    assert entry['outcome_status'] == 'CLOSED'
    assert snapshot(stores[1]) == before
    # A retained outcome is not re-evaluated even if its daily evidence is gone.
    assert produce(stores, daily={})['outcomes'][0]['status'] == 'ALREADY_RETAINED'


def test_open_position_is_pending_and_never_retained(stores):
    observe(stores[0])
    received = datetime(2026, 11, 6, 7, tzinfo=UTC)   # Friday (weekend) after Thursday's session
    rows = tuple(r.model_copy(update={'source_snapshot_date': date(2026, 11, 6)})
                 for r in LOOKBACK + (row(5, '10', '10.6', '9.8', '10.4', 3),))
    # The full calendar is offered; CALENDAR_THROUGH_RECEIPT_DATE_V1 trims it to Nov 5-6.
    out = produce(stores, daily={str(INSTRUMENT): EgxDailyEvidence(rows, received)},
                  now=received + timedelta(hours=1))
    (entry,) = out['outcomes']
    assert (entry['status'], entry['outcome_status'], entry['outcome_reason']) == (
        'PENDING_PATH', 'PENDING_PATH', 'POSITION_OPEN')
    assert entry['sessions_bound'] == ['2026-11-05'] and entry['outcome_identity'] is None
    assert snapshot(stores[1]) == []


def test_missing_or_early_daily_evidence_is_visible_not_evaluated(stores):
    observe(stores[0], decision(), decision(symbol='OTHR', instrument=OTHER))
    out = produce(stores, daily={str(OTHER): EgxDailyEvidence(ROWS, AT - timedelta(hours=1))})
    statuses = {e['symbol']: (e['status'], e['reason']) for e in out['outcomes']}
    assert statuses == {'TEST': ('AWAITING_DAILY_EVIDENCE', 'NO_DAILY_EVIDENCE'),
                        'OTHR': ('AWAITING_DAILY_EVIDENCE', 'RECEIVED_BEFORE_DETECTION')}
    assert out['status_counts']['AWAITING_DAILY_EVIDENCE'] == 2
    assert snapshot(stores[1]) == []
    # Without an outcome the unchanged aggregate counts each decision NOT_EVALUATED.
    metrics = aggregate_decision_outcomes(
        load_retained_decision_observations(str(stores[0]), market='EGX'), (),
        market='EGX', assumptions=ZERO, as_of=NOW)
    assert metrics['contract_version'] == 'decision-outcome-metrics-v1'


def test_gap_in_retained_evidence_rejects_only_that_observation(stores):
    observe(stores[0], decision(), decision(symbol='OTHR', instrument=OTHER))
    other_rows = tuple(r.model_copy(update={'instrument_id': OTHER, 'canonical_symbol': 'OTHR'})
                       for r in ROWS[:3] + ROWS[4:])       # Sunday's bar missing
    out = produce(stores, daily={str(INSTRUMENT): EgxDailyEvidence(ROWS, RECEIVED),
                                 str(OTHER): EgxDailyEvidence(other_rows, RECEIVED)})
    by_symbol = {e['symbol']: e for e in out['outcomes']}
    assert by_symbol['TEST']['status'] == 'RETAINED'
    rejected = by_symbol['OTHR']
    assert rejected['status'] == 'BINDING_REJECTED' and 'missing daily bar' in rejected['detail']
    assert rejected['outcome_identity'] is None and rejected['sessions_bound'] is None
    assert len(snapshot(stores[1])) == 1


def test_unestablished_session_rejects_binding(stores):
    observe(stores[0])
    sessions = CAL[:4] + (session(8, 'UNKNOWN'),) + CAL[5:]
    (entry,) = produce(stores, sessions=sessions)['outcomes']
    assert entry['status'] == 'BINDING_REJECTED'
    assert 'session state not established' in entry['detail']
    assert snapshot(stores[1]) == []


def test_observation_recorded_after_the_clock_is_not_evaluated(stores):
    observe(stores[0])
    (entry,) = produce(stores, daily={str(INSTRUMENT): EgxDailyEvidence(ROWS, AT)},
                       now=AT + timedelta(minutes=1))['outcomes']
    assert entry['status'] == 'OBSERVATION_AFTER_CLOCK'
    assert snapshot(stores[1]) == []


def test_invalid_configuration_is_refused(stores):
    observations, outcomes = stores
    evidence = {str(INSTRUMENT): EgxDailyEvidence(ROWS, RECEIVED)}
    us = OutcomeAssumptions(assumptions_version='fixture-zero', market='US',
                            entry_slippage_bps=D('0'), exit_slippage_bps=D('0'),
                            cost_bps_per_side=D('0'))
    call = produce_egx_decision_outcomes
    kwargs = dict(daily=evidence, sessions=CAL, assumptions=ZERO, now=NOW)
    refused('CONFIG_INVALID', call, 'relative', str(outcomes), **kwargs)
    refused('CONFIG_INVALID', call, str(observations), str(observations), **kwargs)
    refused('CONFIG_INVALID', call, str(observations), str(observations) + '/', **kwargs)
    for change in (dict(assumptions=us), dict(sessions=list(CAL)),
                   dict(daily={str(INSTRUMENT): (ROWS, RECEIVED)}),
                   dict(daily={str(INSTRUMENT): EgxDailyEvidence(ROWS, RECEIVED.replace(tzinfo=None))}),
                   dict(daily={str(INSTRUMENT): EgxDailyEvidence(ROWS, NOW + timedelta(seconds=1))})):
        refused('CONFIG_INVALID', call, str(observations), str(outcomes), **{**kwargs, **change})
    with pytest.raises(TypeError):
        call(str(observations), str(outcomes), **{**kwargs, 'now': NOW.replace(tzinfo=None)})
    assert snapshot(outcomes) == []


def test_damaged_stores_fail_closed(stores):
    observations, outcomes = stores
    observe(observations)
    (outcomes / 'unexpected.txt').write_bytes(b'x')
    error = refused('OUTCOMES_UNAVAILABLE', produce, stores)
    assert error.detail == 'STORE_INVALID'
    (outcomes / 'unexpected.txt').unlink()
    (observations / 'unexpected.txt').write_bytes(b'x')
    error = refused('OBSERVATIONS_UNAVAILABLE', produce, stores)
    assert error.detail == 'STORE_INVALID'
    assert snapshot(outcomes) == []


def test_clear_action_check_is_required_before_retention(stores):
    (identity, record), = observe(stores[0])
    # Actions outside the window (before the data_as_of date, after the path) only.
    doc = action_evidence((action(3), action(12)))
    out = produce(stores, actions={identity: selection(doc)})
    assert out['schema_version'] == 'decision-outcome-production-v3'
    assert out['corporate_actions'] == 'CHECKED_BEFORE_RETENTION'
    assert out['corporate_action_window_rule'] == WINDOW_RULE
    assert out['corporate_action_path_rule'] == ACTION_PATH_RULE == 'PATH_COMPLETION_DATE_CAIRO_V1'
    assert out['action_selections_without_observation'] == 0
    assert out['price_adjustment'] == 'NOT_APPLIED'
    (entry,) = out['outcomes']
    assert (entry['status'], entry['outcome_status']) == ('RETAINED', 'CLOSED')
    assert entry['corporate_action_status'] == 'CLEAR'
    # The report is exactly the unchanged pure check at the path-completion date
    # (2026-11-09T14:00Z is 16:00 Cairo on 2026-11-09) with the producer clock.
    assert entry['corporate_action_check'] == check_egx_outcome_corporate_actions(
        identity, record, doc, source_ingestion_id=SOURCE, review_ingestion_ids=REVIEWS,
        path_through=date(2026, 11, 9), as_of=NOW)
    # The retained outcome is the same unchanged evaluator result as an unchecked run.
    bound = bind_egx_outcome_session_bars(identity, record, ROWS, CAL, received_at=RECEIVED)
    expected = evaluate_decision_outcome(identity, record, bound['bars'], assumptions=ZERO,
                                         path_complete_through_at=bound['path_complete_through_at'],
                                         evaluated_at=NOW)
    assert load_retained_decision_outcomes(str(stores[1]), market='EGX',
                                           assumptions=ZERO) == (expected,)
    # A retained outcome is not re-checked; the outcome record does not carry the check.
    (again,) = produce(stores, actions={})['outcomes']
    assert again['status'] == 'ALREADY_RETAINED' and again['corporate_action_status'] is None
    assert out['retained_outcome_corporate_actions'] == 'NOT_RECORDED_IN_OUTCOME'


def test_action_in_path_is_visible_and_never_retained(stores):
    (identity, record), = observe(stores[0])
    # A dividend effective on Sunday 2026-11-08, inside the path.
    out = produce(stores, actions={identity: selection(action_evidence((action(8),)))})
    (entry,) = out['outcomes']
    assert entry['status'] == 'ACTIONS_IN_PATH'
    assert entry['corporate_action_status'] == 'ACTIONS_IN_PATH'
    assert [a['event_id'] for a in entry['corporate_action_check']['actions_in_path']] == ['ev-8']
    assert entry['corporate_action_check']['price_adjustment'] == 'NOT_APPLIED'
    # The hypothetical outcome stays visible but is not retained or adjusted.
    assert entry['outcome_status'] == 'CLOSED' and entry['outcome_identity'] is None
    assert out['status_counts']['ACTIONS_IN_PATH'] == 1
    assert out['status_counts']['RETAINED'] == 0
    assert snapshot(stores[1]) == []
    # The unchanged aggregate keeps the decision as NOT_EVALUATED (no silent drop).
    metrics = aggregate_decision_outcomes(
        load_retained_decision_observations(str(stores[0]), market='EGX'), (),
        market='EGX', assumptions=ZERO, as_of=NOW)
    assert metrics['contract_version'] == 'decision-outcome-metrics-v1'


def test_missing_selection_awaits_action_evidence(stores):
    observed = observe(stores[0], decision(), decision(symbol='OTHR', instrument=OTHER))
    other_rows = tuple(r.model_copy(update={'instrument_id': OTHER, 'canonical_symbol': 'OTHR'})
                       for r in ROWS)
    identities = {record['symbol']: identity for identity, record in observed}
    out = produce(stores, daily={str(INSTRUMENT): EgxDailyEvidence(ROWS, RECEIVED),
                                 str(OTHER): EgxDailyEvidence(other_rows, RECEIVED)},
                  actions={identities['TEST']: selection(), 'f' * 64: selection()})
    by_symbol = {e['symbol']: e for e in out['outcomes']}
    assert by_symbol['TEST']['status'] == 'RETAINED'
    waiting = by_symbol['OTHR']
    assert (waiting['status'], waiting['reason']) == ('AWAITING_ACTION_EVIDENCE',
                                                      'NO_ACTION_EVIDENCE')
    assert 'NO_ACTION_EVIDENCE' in AWAITING_REASONS
    assert waiting['outcome_status'] == 'CLOSED' and waiting['outcome_identity'] is None
    assert waiting['corporate_action_check'] is None
    # A selection naming no stored observation is counted, never used.
    assert out['action_selections_without_observation'] == 1
    assert len(snapshot(stores[1])) == 1


@pytest.mark.parametrize('doc,why', [
    (action_evidence(end=date(2026, 11, 8)), 'coverage'),        # ends before the path date
    (action_evidence(start=date(2026, 11, 6)), 'coverage'),      # starts after data_as_of date
    (action_evidence(published=NOW + timedelta(seconds=1)), 'published after'),
    (action_evidence(instrument=OTHER), 'instrument'),
])
def test_unusable_action_evidence_rejects_only_that_observation(stores, doc, why):
    (identity, _), = observe(stores[0])
    (entry,) = produce(stores, actions={identity: selection(doc)})['outcomes']
    assert entry['status'] == 'ACTION_CHECK_REJECTED' and why in entry['detail']
    assert entry['corporate_action_status'] is None and entry['outcome_identity'] is None
    assert snapshot(stores[1]) == []


def test_pending_path_is_not_checked(stores):
    observe(stores[0])
    received = datetime(2026, 11, 6, 7, tzinfo=UTC)
    rows = tuple(r.model_copy(update={'source_snapshot_date': date(2026, 11, 6)})
                 for r in LOOKBACK + (row(5, '10', '10.6', '9.8', '10.4', 3),))
    (entry,) = produce(stores, daily={str(INSTRUMENT): EgxDailyEvidence(rows, received)},
                       now=received + timedelta(hours=1), actions={})['outcomes']
    assert entry['status'] == 'PENDING_PATH' and entry['corporate_action_status'] is None
    assert snapshot(stores[1]) == []


def test_invalid_action_selections_are_refused(stores):
    (identity, _), = observe(stores[0])
    doc = action_evidence()
    for actions in ([selection()], {identity: (doc, SOURCE, REVIEWS)}, {1: selection()}):
        refused('CONFIG_INVALID', produce, stores, actions=actions)
    assert snapshot(stores[1]) == []


def test_clear_retention_is_attested_in_the_sidecar(stores, sidecar):
    (identity, record), = observe(stores[0])
    doc = action_evidence((action(3), action(12)))
    out = produce(stores, actions={identity: selection(doc)}, attestations=sidecar)
    assert out['outcome_attestation'] == 'ATTESTED_AFTER_CLEAR_RETENTION'
    assert out['held_outcome_attestation'] == 'NOT_PERFORMED'
    assert tuple(out['attestation_counts']) == ATTESTATION_STATUSES
    assert out['attestation_counts']['RETAINED'] == 1
    assert sum(out['attestation_counts'].values()) == 1
    (entry,) = out['outcomes']
    assert (entry['status'], entry['corporate_action_status'], entry['attestation_status']) == (
        'RETAINED', 'CLEAR', 'RETAINED')
    assert entry['attestation_detail'] is None
    (held,) = load_retained_decision_outcomes(str(stores[1]), market='EGX', assumptions=ZERO)
    assert entry['outcome_identity'] == sha256(canonical_bytes(held)).hexdigest()
    (attestation,) = load_retained_outcome_attestations(str(sidecar))
    assert entry['attestation_identity'] == sha256(canonical_bytes(attestation)).hexdigest()
    # Exactly the unchanged builder over the retained outcome, its observation and
    # the entry's CLEAR report; it re-verifies against the retained records.
    assert attestation == attest_outcome_corporate_actions(
        entry['outcome_identity'], held, (identity, record), entry['corporate_action_check'])
    assert verify_outcome_action_attestation(
        attestation, entry['outcome_identity'], held, (identity, record)) == attestation
    assert attestation['check_timing'] == 'AT_EVALUATION'
    assert attestation['live_money'] is False and attestation['performance_claim'] == 'NONE'
    population = load_retained_decision_observations(str(stores[0]), market='EGX')
    partition = partition_egx_attested_outcomes(population, (held,), (attestation,))
    assert partition['counts'] == {'ATTESTED_CLEAR': 1, 'UNATTESTED': 0, 'ATTESTATION_INVALID': 0}
    # A rerun neither re-checks nor re-attests the held outcome.
    before = snapshot(sidecar)
    again = produce(stores, actions={identity: selection(doc)}, attestations=sidecar,
                    now=NOW + timedelta(days=1))
    (entry,) = again['outcomes']
    assert entry['status'] == 'ALREADY_RETAINED' and entry['attestation_status'] is None
    assert sum(again['attestation_counts'].values()) == 0
    assert snapshot(sidecar) == before


def test_attestation_is_opt_in_and_requires_the_check(stores, sidecar):
    observations, outcomes = stores
    (identity, _), = observe(observations)
    actions = {identity: selection()}
    error = refused('CONFIG_INVALID', produce, stores, attestations=sidecar)
    assert 'requires action selections' in str(error)
    for directory in (outcomes, observations):
        refused('CONFIG_INVALID', produce, stores, actions=actions, attestations=directory)
    refused('CONFIG_INVALID', produce_egx_decision_outcomes, str(observations), str(outcomes),
            daily={str(INSTRUMENT): EgxDailyEvidence(ROWS, RECEIVED)}, sessions=CAL,
            assumptions=ZERO, now=NOW, actions=actions, attestation_directory='relative')
    assert snapshot(outcomes) == [] and snapshot(sidecar) == []
    # Checked but not attested: the outcome is retained, the sidecar untouched.
    out = produce(stores, actions=actions)
    assert out['outcomes'][0]['status'] == 'RETAINED'
    assert out['outcome_attestation'] == 'NOT_REQUESTED'
    assert snapshot(sidecar) == []


def test_outcomes_without_a_clear_check_in_this_run_are_not_attested(stores, sidecar):
    (identity, _), = observe(stores[0])
    out = produce(stores, actions={identity: selection(action_evidence((action(8),)))},
                  attestations=sidecar)
    (entry,) = out['outcomes']
    assert entry['status'] == 'ACTIONS_IN_PATH' and entry['attestation_status'] is None
    assert sum(out['attestation_counts'].values()) == 0
    assert snapshot(sidecar) == [] and snapshot(stores[1]) == []
    # An outcome retained by an unchecked run is held, so a later checked run
    # attests nothing; it stays UNATTESTED (not clear) for the partition.
    produce(stores)
    later = produce(stores, actions={identity: selection()}, attestations=sidecar,
                    now=NOW + timedelta(days=1))
    (entry,) = later['outcomes']
    assert entry['status'] == 'ALREADY_RETAINED' and entry['attestation_status'] is None
    assert later['held_outcome_attestation'] == 'NOT_PERFORMED'
    assert snapshot(sidecar) == []
    held = load_retained_decision_outcomes(str(stores[1]), market='EGX', assumptions=ZERO)
    population = load_retained_decision_observations(str(stores[0]), market='EGX')
    partition = partition_egx_attested_outcomes(population, held, ())
    assert partition['counts'] == {'ATTESTED_CLEAR': 0, 'UNATTESTED': 1, 'ATTESTATION_INVALID': 0}


def test_unusable_sidecar_refuses_the_run_before_any_write(stores, sidecar):
    (identity, _), = observe(stores[0])
    actions = {identity: selection()}
    (sidecar / 'unexpected.txt').write_bytes(b'x')
    error = refused('ATTESTATIONS_UNAVAILABLE', produce, stores, actions=actions,
                    attestations=sidecar)
    assert error.detail == 'STORE_INVALID'
    refused('ATTESTATIONS_UNAVAILABLE', produce, stores, actions=actions,
            attestations=sidecar.parent / 'missing')
    assert snapshot(stores[1]) == []


def test_attestation_refusals_are_visible_and_never_hide_the_outcome(stores, sidecar, monkeypatch):
    import app.decision_outcome_producer as producer
    from app.decision_outcome_attestation_store import OutcomeAttestationStoreError
    (identity, _), = observe(stores[0])
    actions = {identity: selection()}

    def busy(*args, **kwargs):
        raise OutcomeAttestationStoreError('STORE_BUSY')

    monkeypatch.setattr(producer, 'retain_outcome_action_attestation', busy)
    error = refused('ATTESTATION_FAILED', produce, stores, actions=actions, attestations=sidecar)
    assert 'STORE_BUSY' in error.detail
    # The outcome retained before the refusal stays (write-once) and is unattested.
    assert len(snapshot(stores[1])) == 1 and snapshot(sidecar) == []
    monkeypatch.undo()

    def reject(*args, **kwargs):
        raise ValueError('fixture refusal')

    monkeypatch.setattr(producer, 'attest_outcome_corporate_actions', reject)
    observed = observe(stores[0], decision(symbol='OTHR', instrument=OTHER))
    other = next(i for i, r in observed if r['symbol'] == 'OTHR')
    other_rows = tuple(r.model_copy(update={'instrument_id': OTHER, 'canonical_symbol': 'OTHR'})
                       for r in ROWS)
    out = produce(stores, daily={str(INSTRUMENT): EgxDailyEvidence(ROWS, RECEIVED),
                                 str(OTHER): EgxDailyEvidence(other_rows, RECEIVED)},
                  actions={identity: selection(),
                           other: selection(action_evidence(instrument=OTHER))},
                  attestations=sidecar)
    by_symbol = {e['symbol']: e for e in out['outcomes']}
    # TEST was retained (unattested) by the aborted run: held, never attested.
    assert (by_symbol['TEST']['status'], by_symbol['TEST']['attestation_status']) == (
        'ALREADY_RETAINED', None)
    entry = by_symbol['OTHR']
    assert (entry['status'], entry['attestation_status']) == ('RETAINED', 'ATTESTATION_REJECTED')
    assert entry['attestation_detail'] == 'fixture refusal'
    assert entry['attestation_identity'] is None
    assert out['attestation_counts']['ATTESTATION_REJECTED'] == 1
    assert snapshot(sidecar) == []


def test_production_is_deterministic_for_a_fixed_clock(tmp_path):
    results = []
    for name in ('a', 'b'):
        base = tmp_path / name
        base.mkdir(mode=0o700)
        pair = (base / 'observations', base / 'outcomes')
        for path in pair:
            path.mkdir(mode=0o700)
        observe(pair[0])
        results.append((produce(pair), snapshot(pair[1])))
    assert results[0] == results[1]
