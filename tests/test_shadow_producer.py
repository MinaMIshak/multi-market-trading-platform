"""Offline producer-to-existing-UI tests with artificial evidence, never market results."""
import json
from datetime import timedelta

import pytest

from app import main
from app.paper import shadow_collection, shadow_freeze, shadow_ledger, shadow_producer
from app.paper.shadow_producer import (
    StrategyShadowRequest, StrategyShadowSelection, produce_strategy_watchlist,
)
from app.ui.shadow_input import _decode
from tests.test_shadow_candidate_admission import strategy_candidate, watch_admission
from tests.test_shadow_collection import authenticated_watchlist
from tests.test_shadow_records import AT
from tools.produce_shadow_watchlist import main as produce_cli


def request():
    watchlist, packages = authenticated_watchlist()
    candidate = strategy_candidate(symbol='IBM', decision_time=watchlist.information_cutoff,
                                   data_cutoff=watchlist.information_cutoff)
    admission = watch_admission(evidence_ids=(packages[1].identity,))
    return StrategyShadowRequest(
        record_id=watchlist.record_id, information_cutoff=watchlist.information_cutoff,
        session=watchlist.session, evidence=watchlist.evidence,
        selections=(StrategyShadowSelection(candidate=candidate, admission=admission),),
        evidence_packages=packages,
    )


@pytest.fixture(autouse=True)
def clocks(monkeypatch):
    for module in (shadow_producer, shadow_collection, shadow_freeze, shadow_ledger):
        monkeypatch.setattr(module, '_now', lambda: AT)


def test_producer_reaches_existing_ui_without_execution(tmp_path, monkeypatch):
    source = request()
    directory = tmp_path / 'collection'
    result = produce_strategy_watchlist(directory, source)
    monkeypatch.setenv('EGX_SHADOW_DIRECTORY', str(directory))
    before = {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    state = main.shadow()
    assert state['available']
    candidate = state['collection']['candidates'][0]
    assert candidate['decision_status'] == 'WATCH'  # M4 READY was not promoted.
    assert candidate['entry_low'] is None and candidate['paper_quantity'] == 0
    assert 'state=READY' in candidate['context'][0]
    assert state['execution'] is state['portfolio'] is state['series'] is None
    assert result['scoring'] == 'NOT SCORED'
    assert result['execution_status'] == 'NO EXECUTION INFERENCE'
    assert json.loads((directory / 'strategy-source.json').read_bytes()) == source.model_dump(mode='json')
    assert 'IBM' in main.shadow_page().body.decode()
    assert before == {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    with pytest.raises(FileExistsError):
        produce_strategy_watchlist(directory, source)
    assert before == {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()}


def test_cli_reads_canonical_request(tmp_path, monkeypatch, capsys):
    source = tmp_path / 'request.json'
    source.write_text(request().model_dump_json())
    destination = tmp_path / 'collection'
    assert produce_cli([str(source), str(destination)]) == 0
    assert json.loads(capsys.readouterr().out)['candidate_count'] == 1
    monkeypatch.setenv('EGX_SHADOW_DIRECTORY', str(destination))
    assert main.shadow()['available']


@pytest.mark.parametrize('damage', ['late_decision', 'future_data', 'execution', 'nan',
                                   'quantity_bool', 'unapproved', 'missing_evidence', 'late_session'])
def test_invalid_inputs_leave_no_output(tmp_path, damage):
    source = request()
    selected = source.selections[0]
    if damage in ('late_decision', 'future_data', 'execution', 'nan'):
        updates = {
            'late_decision': {'decision_time': source.information_cutoff + timedelta(seconds=1)},
            'future_data': {'data_cutoff': AT},
            'execution': {'execution_allowed': True},
            'nan': {'entry_reference': float('nan')},
        }[damage]
        selected = selected.model_copy(update={'candidate': selected.candidate.model_copy(update=updates)})
        source = source.model_copy(update={'selections': (selected,)})
    elif damage == 'quantity_bool':
        selected = selected.model_copy(update={
            'admission': selected.admission.model_copy(update={'paper_quantity': False})})
        source = source.model_copy(update={'selections': (selected,)})
    elif damage == 'unapproved':
        package = source.evidence_packages[0]
        package = package.model_copy(update={'review': package.review.model_copy(update={'approved': False})})
        source = source.model_copy(update={'evidence_packages': (package, source.evidence_packages[1])})
    elif damage == 'missing_evidence':
        source = source.model_copy(update={'evidence_packages': source.evidence_packages[:1]})
    else:
        source = source.model_copy(update={'session': source.session.model_copy(update={'decision_cutoff': AT})})
    with pytest.raises((ValueError, TypeError)):
        produce_strategy_watchlist(tmp_path / 'collection', source)
    assert not list(tmp_path.iterdir())


def test_explicit_empty_collection_is_not_inferred(tmp_path, monkeypatch):
    source = request()
    source = source.model_copy(update={'selections': (), 'evidence': source.evidence[:1],
                                      'evidence_packages': source.evidence_packages[:1]})
    result = produce_strategy_watchlist(tmp_path / 'collection', source)
    monkeypatch.setenv('EGX_SHADOW_DIRECTORY', str(tmp_path / 'collection'))
    assert result['candidate_count'] == 0 and result['scoring'] == 'NOT SCORED'
    assert main.shadow()['collection']['candidates'] == []


@pytest.mark.parametrize('stage', ['complete_watchlist', 'append_candidate_event', 'audit_candidate_event'])
def test_interrupted_publication_never_exposes_ui_input(tmp_path, monkeypatch, stage):
    def fail(*args, **kwargs):
        raise ValueError('artificial failure')
    monkeypatch.setattr(shadow_producer, stage, fail)
    directory = tmp_path / 'collection'
    with pytest.raises(ValueError):
        produce_strategy_watchlist(directory, request())
    assert not (directory / 'input.json').exists()
    monkeypatch.setenv('EGX_SHADOW_DIRECTORY', str(directory))
    assert not main.shadow()['available']


def test_missed_session_cannot_be_reconstructed(tmp_path, monkeypatch):
    source = request()
    monkeypatch.setattr(shadow_producer, '_now', lambda: source.session.decision_cutoff)
    with pytest.raises(ValueError):
        produce_strategy_watchlist(tmp_path / 'collection', source)
    assert not list(tmp_path.iterdir())


def test_cutoff_crossed_during_collection_leaves_no_ui_input(tmp_path, monkeypatch):
    source = request()
    monkeypatch.setattr(shadow_collection, '_now', lambda: source.session.decision_cutoff)
    with pytest.raises(ValueError):
        produce_strategy_watchlist(tmp_path / 'collection', source)
    assert not (tmp_path / 'collection' / 'input.json').exists()


def test_linked_destination_rejected(tmp_path):
    link = tmp_path / 'link'
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match='unlinked'):
        produce_strategy_watchlist(link / 'collection', request())
    assert not (tmp_path / 'collection').exists()


@pytest.mark.parametrize('damage', ['extra', 'duplicate', 'float_string', 'bool_float', 'missing_selection'])
def test_cli_rejects_ambiguous_transport(tmp_path, damage, capsys):
    document = request().model_dump(mode='json')
    if damage == 'extra':
        document['generated_at'] = AT.isoformat()
    elif damage == 'missing_selection':
        del document['selections']
    elif damage in ('float_string', 'bool_float'):
        document['selections'][0]['candidate']['entry_reference'] = '10.0' if damage == 'float_string' else True
    content = json.dumps(document)
    if damage == 'duplicate':
        content = '{"record_id":"other",' + content[1:]
    path = tmp_path / 'request.json'
    path.write_text(content)
    with pytest.raises(SystemExit) as error:
        produce_cli([str(path), str(tmp_path / 'collection')])
    assert error.value.code == 1
    assert 'rejected' in capsys.readouterr().err
    assert not (tmp_path / 'collection').exists()


def test_decoder_roundtrip():
    source = request()
    assert _decode(StrategyShadowRequest, source.model_dump(mode='json')) == source


def test_today_displays_frozen_candidates_separately_from_data(tmp_path, monkeypatch):
    """The unified product shell at / never renders frozen shadow candidates;
    they remain visible only through the dedicated /shadow surface. Root's
    legacy fallback to render_today_dashboard was intentionally removed in
    commit c7abf60 (unified operational product shell)."""
    directory = tmp_path / 'collection'
    produce_strategy_watchlist(directory, request())
    monkeypatch.setenv('EGX_SHADOW_DIRECTORY', str(directory))
    monkeypatch.setenv('EGX_DB_PATH', str(tmp_path / 'missing.db'))
    shadow_body = main.shadow_page().body.decode()
    assert 'US · 2026-09-14 · Record US-20260914-fixture' in shadow_body
    assert 'IBM · WATCH' in shadow_body
    assert 'Freshness NOT ESTABLISHED' in shadow_body
    assert 'Frozen declarations; fills and current positions NOT EVALUATED.' in shadow_body
    today_page = main.root().body.decode()
    assert 'IBM' not in today_page
    assert 'Authenticated trade observations are required' in main.performance().body.decode()
    assert not (tmp_path / 'missing.db').exists()


def test_today_escapes_frozen_candidate_text():
    from app.ui.today import render_today_dashboard
    page = render_today_dashboard({}, shadow_state={'collection': {
        'market': '<script>', 'market_date': '<date>', 'information_cutoff': '<clock>',
        'candidates': [{'ticker': '<ticker>', 'decision_status': '<decision>'}],
    }})
    assert '<script>' not in page and '&lt;script&gt;' in page
    assert '&lt;ticker&gt;' in page and '&lt;decision&gt;' in page


def test_oversized_collection_rejected_before_publication(tmp_path):
    source = request()
    selection = source.selections[0]
    selection = selection.model_copy(update={'admission': selection.admission.model_copy(update={
        'thesis': 'x' * (4 * 1024 * 1024),
    })})
    source = source.model_copy(update={'selections': (selection,)})
    with pytest.raises(ValueError, match='size limit'):
        produce_strategy_watchlist(tmp_path / 'collection', source)
    assert not list(tmp_path.iterdir())
