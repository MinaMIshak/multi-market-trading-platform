"""RESEARCH notes are built only from verified EGX receipts; never generated or executable."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.research.intelligence import ResearchNote
from app.research.receipt_notes import receipt_research_notes
from app.ui.product import product_state, render_product
from tests.test_paper_shadow_launch import launch  # noqa: F401  (pytest fixture)

NOW = datetime.now(timezone.utc)
DECIDED = NOW - timedelta(hours=2)


def receipt(**extra):
    return {'symbol': 'COMI', 'market': 'EGX', 'status': 'WATCH', 'mode': 'SHADOW',
            'live': 'DISABLED', 'strategy_id': 'SWING', 'strategy_version': '1',
            'empirical_status': 'NOT VALIDATED', 'last_verified_session': '2026-09-27',
            'decision_at': DECIDED.isoformat(), 'valid_until': (NOW + timedelta(hours=20)).isoformat(),
            'provider': 'egid', 'raw_sha256': 'a' * 64, 'history_start': '2025-01-02',
            'bar_count': 180, 'pit_audit_id': 'pit-1',
            'trade_plan': {'entry_low': '80.0', 'stop_price': '75.0'}, **extra}


def market(*symbols, observed_at=NOW):
    return {'configured': True, 'available': True, 'status': 'OPERATIONAL',
            'observed_at': observed_at.isoformat(), 'symbols': list(symbols)}


def kinds(note):
    return [row['kind'] for row in note['statements']]


def test_unavailable_reader_yields_no_notes():
    assert receipt_research_notes({'available': False, 'symbols': [], 'observed_at': None}) is None


def test_verified_receipt_becomes_sourced_note_without_trade_plan():
    (note,) = receipt_research_notes(market(receipt()))
    ResearchNote.model_validate_json(json.dumps({k: v for k, v in note.items() if k != 'execution_authority'}))
    assert note['execution_authority'] == 'NONE'
    assert note['market'] == 'EGX' and note['subject'] == 'COMI'
    assert kinds(note)[:2] == ['SOURCE_FACT', 'SOURCE_FACT']
    assert set(kinds(note)[2:]) == {'UNKNOWN'}
    verification, history = note['statements'][:2]
    assert verification['provenance']['locator'] == 'audit_event:PIT_DATA_VALIDATED:pit-1'
    assert verification['provenance']['as_of'] == '2026-09-27'
    assert 'WATCH' in verification['text'] and 'NOT VALIDATED' in verification['text']
    assert history['provenance']['locator'] == 'raw_sha256:' + 'a' * 64
    assert '180' in history['text'] and 'egid' in history['text']
    text = repr(note)
    assert '80.0' not in text and '75.0' not in text and 'trade_plan' not in text


def test_stale_receipt_adds_derived_expiry_on_fact():
    (note,) = receipt_research_notes(market(receipt(status='DATA_STALE',
                                                    valid_until=(NOW - timedelta(minutes=1)).isoformat())))
    derived = [row for row in note['statements'] if row['kind'] == 'DERIVED_METRIC']
    assert len(derived) == 1 and derived[0]['inputs'] == ['verification']
    assert 'DATA_STALE' in derived[0]['text']


def test_blocked_or_unverified_symbols_are_unknown_only():
    blocked = {'symbol': 'HRHO', 'market': 'EGX', 'status': 'EVIDENCE_BLOCKED', 'trade_plan': None,
               'reason': 'invalid verification receipt'}
    missing = {'symbol': 'ETEL', 'market': 'EGX', 'status': 'NOT_READY', 'trade_plan': None}
    notes = {n['subject']: n for n in receipt_research_notes(market(blocked, missing))}
    assert set(notes) == {'HRHO', 'ETEL'}
    for note in notes.values():
        assert set(kinds(note)) == {'UNKNOWN'}


def test_fact_after_reader_observation_fails_closed_to_unknown():
    late = receipt(decision_at=(NOW + timedelta(minutes=5)).isoformat())
    (note,) = receipt_research_notes(market(late))
    assert set(kinds(note)) == {'UNKNOWN'}


def test_malformed_receipt_fields_fail_closed_to_unknown():
    for broken in (receipt(pit_audit_id=''), receipt(decision_at='yesterday'),
                   receipt(bar_count='many'), receipt(provider=None)):
        (note,) = receipt_research_notes(market(broken))
        assert set(kinds(note)) == {'UNKNOWN'}


def test_duplicate_symbol_rows_are_blocked_not_selected():
    notes = receipt_research_notes(market(receipt(), receipt(status='READY_NO_SIGNAL')))
    assert len(notes) == 1 and set(kinds(notes[0])) == {'UNKNOWN'}


def test_research_view_renders_receipt_notes_for_egx_only():
    operational = market(receipt())
    state = product_state(operational, 'EGX', 'RESEARCH')
    assert [n['subject'] for n in state['research']['notes']] == ['COMI']
    page = render_product(state)
    assert 'SOURCE_FACT' in page and 'audit_event:PIT_DATA_VALIDATED:pit-1' in page
    assert 'Sourced research notes: UNKNOWN' not in page
    assert product_state(operational, 'US', 'RESEARCH')['research']['notes'] is None


def test_research_view_escapes_receipt_text():
    state = product_state(market(receipt(symbol='<b>X</b>')), 'EGX', 'RESEARCH')
    page = render_product(state)
    assert '<b>X</b>' not in page and '&lt;b&gt;X&lt;/b&gt;' in page


@pytest.mark.parametrize('scenario', ['watch', 'expired', 'tampered'])
def test_runtime_receipts_flow_into_research_notes(launch, monkeypatch, scenario):
    import sqlite3

    from app import main
    from app.paper.swing_launch import run_signal
    from app.ui import operational

    db, root, source, directory, _, _, at = launch
    run_signal(db, root, source, directory, publish=False)
    runtime = db.path.parent
    with db.connect() as original, sqlite3.connect(runtime / 'platform.db') as copy:
        original.backup(copy)
    monkeypatch.setenv('EGX_PAPER_RUNTIME', str(runtime))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at + timedelta(days=5) if scenario == 'expired' else at
    monkeypatch.setattr(operational, 'datetime', Clock)
    if scenario == 'tampered':
        with sqlite3.connect(runtime / 'platform.db') as con:
            con.execute("UPDATE audit_events SET payload_json='{}' WHERE event_type='PAPER_SIGNAL_VERIFIED'")
    before = (runtime / 'platform.db').read_bytes()
    (note,) = main.product('EGX', 'RESEARCH')['research']['notes']
    assert before == (runtime / 'platform.db').read_bytes()
    assert note['execution_authority'] == 'NONE'
    found = [row['kind'] for row in note['statements']]
    if scenario == 'tampered':
        assert set(found) == {'UNKNOWN'}
    else:
        assert found[:2] == ['SOURCE_FACT', 'SOURCE_FACT']
        assert ('DERIVED_METRIC' in found) == (scenario == 'expired')
        assert 'trade_plan' not in repr(note)
    assert 'SOURCE_FACT' in main.root('EGX', 'RESEARCH').body.decode() or scenario == 'tampered'
