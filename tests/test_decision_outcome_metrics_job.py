"""decision-outcome-metrics-job-v2: read-only checked metrics over retained stores."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from app.decision_observation_store import load_retained_decision_observations
from app.decision_outcome_action_attestation import attest_outcome_corporate_actions
from app.decision_outcome_attestation_store import (
    STORE_REASONS as ATTESTATION_STORE_REASONS, retain_outcome_action_attestation)
from app.decision_outcome_checked_event_states import (
    aggregate_egx_checked_decision_outcomes_by_event_state)
from app.decision_outcome_checked_metrics import aggregate_egx_checked_decision_outcomes
from app.decision_outcome_event_states import NOT_ATTACHED
from app.decision_outcome_metrics import NOT_EVALUATED
from app.decision_outcome_metrics_job import (
    ATTESTATION_SELECTION, CONTRACT_VERSION, EVENT_STATE_NOT_REQUESTED, EXIT_CODES,
    SELECTION_RULE, SELECTION_TIME_BASIS, MetricsJobError, main, parse_as_of,
    run_egx_decision_outcome_metrics_job)
from app.decision_outcome_store import load_retained_decision_outcomes
from tests.test_decision_outcome_action_attestation import checked  # noqa: F401
from tests.test_decision_outcome_producer import NOW
from tests.test_decision_outcome_session_bars import ZERO

SAFETY = {'writes': 'NONE', 'fill_kind': 'HYPOTHETICAL_FROM_SESSION_BARS',
          'execution_evidence': 'NONE', 'performance_claim': 'NONE', 'mode': 'SHADOW',
          'live_money': False, 'execution_allowed': False, 'runtime_activation': False,
          'promotion': 'NOT_AUTHORIZED'}
CLI_ASSUMPTIONS = ['--assumptions-version', 'fixture-zero', '--entry-slippage-bps', '0',
                   '--exit-slippage-bps', '0', '--cost-bps-per-side', '0']
# The fixture observation is recorded at 2026-11-05T06:05Z; its outcome is
# evaluated (and checked) at NOW, 2026-11-09T15:00Z.
BEFORE_EVALUATION = NOW - timedelta(days=1)
BEFORE_RECORDING = datetime(2026, 11, 5, 6, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _private_tmp(tmp_path):
    # The stores refuse group/world-writable directories; do not depend on umask.
    tmp_path.chmod(0o700)


@pytest.fixture
def stores(tmp_path, checked):
    """(observation dir, outcome dir, attestation dir, checked); attestation not retained."""
    attestations = tmp_path / 'attestations'
    attestations.mkdir(mode=0o700)
    return tmp_path / 'observations', tmp_path / 'outcomes', attestations, checked


def attest(stores):
    _, _, directory, (outcome_identity, outcome, observation, clear) = stores
    attestation = attest_outcome_corporate_actions(outcome_identity, outcome, observation, clear)
    retain_outcome_action_attestation(str(directory), attestation, (outcome_identity, outcome),
                                      observation, now=NOW)
    return attestation


def run(stores, as_of=NOW, now=NOW, **overrides):
    observations, outcomes, attestations, _ = stores
    kwargs = {'observation_directory': str(observations), 'outcome_directory': str(outcomes),
              'attestation_directory': str(attestations), 'assumptions': ZERO,
              'as_of': as_of, 'now': now, **overrides}
    return run_egx_decision_outcome_metrics_job(**kwargs)


def snapshot(stores):
    return [sorted((p.name, p.read_bytes()) for p in directory.iterdir())
            for directory in stores[:3]]


def cli(stores, as_of='2026-11-09T15:00:00Z', attestation_dir=None, extra=()):
    observations, outcomes, attestations, _ = stores
    return main(['--observation-dir', str(observations), '--outcome-dir', str(outcomes),
                 '--attestation-dir', str(attestations if attestation_dir is None
                                          else attestation_dir),
                 '--as-of', as_of, *CLI_ASSUMPTIONS, *extra])


def state(event_state_report, name=NOT_ATTACHED):
    (found,) = [s for s in event_state_report['event_state_metrics']['states']
                if s['event_state'] == name]
    return found


def test_reports_unchanged_checked_metrics_and_writes_nothing(stores):
    attestation = attest(stores)
    before = snapshot(stores)
    out = run(stores)
    assert snapshot(stores) == before
    assert out['schema_version'] == CONTRACT_VERSION == 'decision-outcome-metrics-job-v2'
    # Default run: the event-state report is not requested and not computed.
    assert out['event_state_report'] == EVENT_STATE_NOT_REQUESTED == 'NOT_REQUESTED'
    assert out['status'] == 'REPORTED'
    assert out['as_of'] == out['job_clock'] == '2026-11-09T15:00:00Z'
    observation = stores[3][2]
    outcome = stores[3][1]
    # The embedded report is exactly the unchanged pure caller's output.
    assert out['report'] == aggregate_egx_checked_decision_outcomes(
        (observation,), (outcome,), (attestation,), assumptions=ZERO, as_of=NOW)
    assert out['report']['attestation_counts']['ATTESTED_CLEAR_AT_OR_BEFORE_AS_OF'] == 1
    assert out['report']['outcomes_counted'] == 1
    assert out['selection'] == {
        'rule': SELECTION_RULE, 'time_basis': SELECTION_TIME_BASIS,
        'observations_loaded': 1, 'observations_in_population': 1,
        'observations_recorded_after_as_of': [], 'outcomes_loaded': 1, 'outcomes_supplied': 1,
        'outcomes_evaluated_after_as_of': [], 'attestations_loaded': 1,
        'attestation_selection': ATTESTATION_SELECTION}
    assert {key: out[key] for key in SAFETY} == SAFETY
    assert out['report']['live_money'] is False
    json.dumps(out, allow_nan=False)
    # Deterministic for the same stores, as_of and clock.
    assert run(stores) == out


def test_unattested_outcome_stays_not_evaluated(stores):
    out = run(stores)
    assert out['report']['attestation_counts']['UNATTESTED'] == 1
    assert out['report']['outcomes_counted'] == 0
    metrics = out['report']['metrics']
    assert (metrics['population_count'], metrics['not_evaluated_count']) == (1, 1)


def test_outcome_evaluated_after_as_of_is_excluded_visibly(stores):
    attest(stores)
    outcome_identity = stores[3][0]
    out = run(stores, as_of=BEFORE_EVALUATION)
    selection = out['selection']
    assert selection['outcomes_loaded'] == 1 and selection['outcomes_supplied'] == 0
    assert selection['outcomes_evaluated_after_as_of'] == [outcome_identity]
    # Its observation stays in the population and is counted NOT_EVALUATED.
    assert selection['observations_in_population'] == 1
    metrics = out['report']['metrics']
    assert (metrics['population_count'], metrics['not_evaluated_count']) == (1, 1)
    assert out['report']['outcomes_supplied'] == 0
    # The attestation is still loaded and visibly unmatched, never counted.
    assert selection['attestations_loaded'] == 1
    (unmatched,) = out['report']['partition']['attestations_without_supplied_outcome']
    assert unmatched['outcome_identity'] == outcome_identity
    assert out['report']['outcomes_counted'] == 0


def test_observation_recorded_after_as_of_is_excluded_visibly(stores):
    attest(stores)
    out = run(stores, as_of=BEFORE_RECORDING)
    selection = out['selection']
    assert selection['observations_recorded_after_as_of'] == [stores[3][2][0]]
    assert selection['observations_in_population'] == 0
    assert selection['outcomes_supplied'] == 0
    assert out['report']['metrics']['population_count'] == 0
    assert out['report']['outcomes_counted'] == 0


def test_inputs_fail_closed_before_any_store_read(stores, tmp_path):
    def refused(**overrides):
        with pytest.raises(MetricsJobError) as info:
            run(stores, **overrides)
        assert (info.value.reason, info.value.stage) == ('CONFIG_INVALID', 'INPUT')

    refused(as_of=NOW + timedelta(seconds=1))                      # future as_of
    refused(as_of=NOW.replace(tzinfo=None))                         # naive as_of
    refused(now=NOW.replace(tzinfo=None))
    refused(observation_directory='relative/observations')
    refused(attestation_directory=str(stores[1]))                   # same as outcomes
    refused(attestation_directory=str(stores[1]) + '/')             # same after normpath
    refused(assumptions=ZERO.model_dump())
    for text in ('', '2026-11-09T15:00:00', 'not-a-time', None):
        with pytest.raises(MetricsJobError) as info:
            parse_as_of(text)
        assert info.value.reason == 'CONFIG_INVALID'
    assert parse_as_of('2026-11-09T15:00:00Z') == NOW
    assert parse_as_of('2026-11-09T17:00:00+02:00') == NOW


def test_cli_reports_and_exits_zero(stores, capsys):
    attest(stores)
    assert cli(stores) == EXIT_CODES['REPORTED'] == 0
    out = json.loads(capsys.readouterr().out)
    assert out['status'] == 'REPORTED'
    assert out['report']['outcomes_counted'] == 1
    assert out['live_money'] is False


def test_cli_store_refusals_fail_closed(stores, tmp_path, capsys):
    assert cli(stores, attestation_dir=tmp_path / 'missing') == EXIT_CODES['FAILED'] == 1
    out = json.loads(capsys.readouterr().out)
    assert (out['status'], out['stage']) == ('FAILED', 'STORES')
    assert out['reason'] in ATTESTATION_STORE_REASONS
    assert {key: out[key] for key in SAFETY} == SAFETY
    # A damaged or unexpected attestation entry refuses the run; nothing partial.
    (stores[2] / 'unexpected.txt').write_bytes(b'x')
    assert cli(stores) == 1
    out = json.loads(capsys.readouterr().out)
    assert (out['stage'], out['reason']) == ('STORES', 'STORE_INVALID')


def test_cli_input_refusals(stores, capsys):
    assert cli(stores, as_of='2026-11-09T15:00:00') == 1               # naive
    out = json.loads(capsys.readouterr().out)
    assert (out['stage'], out['reason']) == ('INPUT', 'CONFIG_INVALID')
    assert cli(stores, as_of='2999-01-01T00:00:00Z') == 1              # after the job clock
    out = json.loads(capsys.readouterr().out)
    assert (out['stage'], out['reason']) == ('INPUT', 'CONFIG_INVALID')


def test_opt_in_event_state_report_is_the_unchanged_checked_component(stores):
    attestation = attest(stores)
    before = snapshot(stores)
    out = run(stores, by_event_state=True)
    assert snapshot(stores) == before
    observation, outcome = stores[3][2], stores[3][1]
    # Exactly the unchanged pure component over the same selected inputs and as_of.
    assert out['event_state_report'] == aggregate_egx_checked_decision_outcomes_by_event_state(
        (observation,), (outcome,), (attestation,), assumptions=ZERO, as_of=NOW)
    # The main report and selection are unchanged by the flag.
    default = run(stores)
    assert {k: v for k, v in out.items() if k != 'event_state_report'} == {
        k: v for k, v in default.items() if k != 'event_state_report'}
    by_state = out['event_state_report']
    assert by_state['attestation_counts'] == out['report']['attestation_counts']
    assert by_state['outcomes_counted'] == out['report']['outcomes_counted'] == 1
    (segment,) = state(by_state)['metrics']['segments']
    assert segment['status_counts']['CLOSED'] == 1
    assert by_state['cross_state_headline'] == 'NOT_COMPUTED'
    assert by_state['absence_is_proof'] is False
    assert by_state['risk_interpretation'] == 'NOT_EVALUATED'
    assert by_state['trading_effect'] == 'NONE'
    assert by_state['live_money'] is False and out['live_money'] is False
    assert {key: out[key] for key in SAFETY} == SAFETY
    json.dumps(out, allow_nan=False)
    assert run(stores, by_event_state=True) == out


def test_event_state_report_keeps_unattested_and_late_outcomes_not_evaluated(stores):
    out = run(stores, by_event_state=True)                           # not attested
    (segment,) = state(out['event_state_report'])['metrics']['segments']
    assert segment['status_counts'][NOT_EVALUATED] == 1 and segment['closed'] == 0
    attest(stores)
    late = run(stores, as_of=BEFORE_EVALUATION, by_event_state=True)  # evaluated after as_of
    assert late['selection']['outcomes_supplied'] == 0
    (segment,) = state(late['event_state_report'])['metrics']['segments']
    assert segment['status_counts'][NOT_EVALUATED] == 1
    assert late['event_state_report']['outcomes_counted'] == 0


def test_event_state_refusals_fail_the_whole_run(stores, monkeypatch, capsys):
    attest(stores)
    with pytest.raises(MetricsJobError) as info:
        run(stores, by_event_state='yes')
    assert (info.value.reason, info.value.stage) == ('CONFIG_INVALID', 'INPUT')

    def refuse(*args, **kwargs):
        raise ValueError('attached event-context section refused')
    monkeypatch.setattr('app.decision_outcome_metrics_job.'
                        'aggregate_egx_checked_decision_outcomes_by_event_state', refuse)
    with pytest.raises(MetricsJobError) as info:
        run(stores, by_event_state=True)
    assert (info.value.reason, info.value.stage) == ('METRICS_REJECTED', 'METRICS')
    # Not requested: the component is not called, so the default run still reports.
    assert run(stores)['status'] == 'REPORTED'
    assert cli(stores, extra=['--by-event-state']) == 1
    out = json.loads(capsys.readouterr().out)
    assert (out['status'], out['stage'], out['reason']) == (
        'FAILED', 'METRICS', 'METRICS_REJECTED')
    assert 'report' not in out and 'event_state_report' not in out


def test_cli_by_event_state_reports_and_exits_zero(stores, capsys):
    attest(stores)
    assert cli(stores, extra=['--by-event-state']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['schema_version'] == 'decision-outcome-metrics-job-v2'
    assert out['event_state_report']['contract_version'] == (
        'decision-outcome-checked-event-state-metrics-v1')
    assert out['event_state_report']['outcomes_counted'] == 1
    assert cli(stores) == 0
    assert json.loads(capsys.readouterr().out)['event_state_report'] == 'NOT_REQUESTED'


def test_loaders_are_the_unchanged_store_loaders(stores):
    # The job sees exactly what the unchanged loaders return.
    observations, outcomes, _, _ = stores
    out = run(stores)
    assert out['selection']['observations_loaded'] == len(
        load_retained_decision_observations(str(observations), market='EGX'))
    assert out['selection']['outcomes_loaded'] == len(
        load_retained_decision_outcomes(str(outcomes), market='EGX', assumptions=ZERO))
