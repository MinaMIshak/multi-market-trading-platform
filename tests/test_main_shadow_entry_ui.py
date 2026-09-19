"""Artificial lifecycle fixtures; no authentic market evidence or performance."""
import json
from decimal import Decimal

import pytest

from app import main
from app.paper import shadow_fills, shadow_positions, shadow_triggers
from tests.test_shadow_fills import prepared
from tests.test_shadow_triggers import admitted


def publish_input(directory, monkeypatch, args, stage):
    def encode(value):
        if isinstance(value, tuple):
            return [encode(item) for item in value]
        return value.model_dump(mode='json')

    (directory / 'input.json').write_text(json.dumps({
        'schema_version': 'shadow-ui-input-v1',
        'watchlist': encode(args[0]), 'evidence_packages': encode(args[1]),
    }))
    fields = ('facts', 'fact_packages') if stage == 'trigger' else (
        'facts', 'fact_packages', 'fill_policy', 'fill_packages')
    document = dict(zip(fields, map(encode, args[2:]), strict=True))
    document['schema_version'] = f'shadow-ui-{stage}-v1'
    path = directory / 'execution.json'
    path.write_text(json.dumps(document))
    monkeypatch.setenv('EGX_SHADOW_DIRECTORY', str(directory))
    return path, document


def snapshot(directory):
    return {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()}


def assert_unscored(view):
    assert view['scoring'] == 'NOT SCORED'
    assert view['open_paper_positions'] == {'status': 'NOT EVALUATED'}
    assert view['closed_paper_trades'] == {'status': 'NOT EVALUATED'}
    assert all(v is None for k, v in view['performance'].items() if k != 'status')


@pytest.mark.parametrize('changes,expected', [
    ({}, 'TRIGGERED'),
    ({'open': '105', 'high': '106', 'low': '102', 'close': '104'}, 'NOT_TRIGGERED'),
    ({'open': '94', 'high': '100', 'low': '90', 'close': '96'}, 'INVALIDATED_OPEN_GAP'),
    ({'open': '110', 'high': '112', 'low': '100', 'close': '105'}, 'TARGET_PASSED_OPEN_GAP'),
    ({'open': '105', 'high': '111', 'low': '100', 'close': '101'}, 'TRIGGERED_AMBIGUOUS_BAR'),
])
def test_trigger_surface_requires_no_fill_or_exit(tmp_path, monkeypatch, changes, expected):
    args = admitted(tmp_path, monkeypatch, {k: Decimal(v) for k, v in changes.items()})[:4]
    shadow_triggers.append_trigger_event(tmp_path, *args)
    publish_input(tmp_path, monkeypatch, args, 'trigger')
    before = snapshot(tmp_path)
    view = main.shadow()['execution']
    assert view['trigger_evaluation']['status'] == expected
    assert view['execution_status'] == 'NO FILL OR POSITION CREATED'
    assert 'fill' not in view and 'position' not in view and 'paper_economics' not in view
    assert_unscored(view)
    body = main.shadow_page().body.decode()
    assert expected in body and 'NO FILL OR POSITION CREATED' in body
    assert 'EXPERIMENTAL / PAPER ONLY' in body
    assert snapshot(tmp_path) == before
    assert not (tmp_path / 'fill-events').exists()


@pytest.mark.parametrize('participation,expected', [
    ('0.10', 'SIMULATED ENTRY FILL CREATED'),
    ('0.0009', 'NO FILL: INSUFFICIENT CAPACITY'),
])
def test_fill_surface_preserves_no_fill_without_inventing_position(
    tmp_path, monkeypatch, participation, expected,
):
    args = prepared(tmp_path, monkeypatch, participation=Decimal(participation))[:-1]
    receipt = shadow_fills.append_fill_event(tmp_path, *args)
    publish_input(tmp_path, monkeypatch, args, 'entry-fill')
    before = snapshot(tmp_path)
    view = main.shadow()['execution']
    assert view['execution_status'] == expected
    assert view['fill'] == json.loads(receipt.read_bytes())['fill']
    assert (view['fill'] is None) == (participation == '0.0009')
    assert 'position' not in view
    assert view['paper_economics']['entry_policy'] == args[4].model_dump(mode='json')
    assert view['paper_economics']['exit_policy'] is None
    assert view['current_position_status'].startswith('UNKNOWN')
    assert_unscored(view)
    body = main.shadow_page().body.decode()
    assert expected in body and 'NO VERIFIED IBKR SCHEDULE BINDING' in body
    assert snapshot(tmp_path) == before
    assert not (tmp_path / 'position-open-events').exists()


def test_position_surface_requires_position_event_not_just_fill(tmp_path, monkeypatch):
    *args, now = prepared(tmp_path, monkeypatch)
    shadow_fills.append_fill_event(tmp_path, *args)
    publish_input(tmp_path, monkeypatch, args, 'position-open')
    assert main.shadow()['execution'] is None
    monkeypatch.setattr(shadow_positions, '_now', lambda: now)
    receipt = shadow_positions.append_position_open_event(tmp_path, *args)
    before = snapshot(tmp_path)
    view = main.shadow()['execution']
    assert view['collection_status'] == 'SIMULATED OPEN AT ENTRY'
    assert view['position']['entry'] == json.loads(receipt.read_bytes())['entry']
    assert view['current_position_status'] == 'UNKNOWN / EXIT NOT AUDITED BY THIS VIEW'
    assert_unscored(view)
    assert 'SIMULATED OPEN AT ENTRY' in main.shadow_page().body.decode()
    assert snapshot(tmp_path) == before
    receipt.unlink()
    assert main.shadow()['execution'] is None  # No fallback to a valid fill.


@pytest.mark.parametrize('stage', ['trigger', 'entry-fill', 'position-open'])
@pytest.mark.parametrize('damage', ['receipt', 'ancestry', 'package', 'extra', 'missing',
                                   'duplicate', 'mixed', 'unapproved', 'symlink'])
def test_each_early_stage_fails_closed(tmp_path, monkeypatch, stage, damage):
    if stage == 'trigger':
        args = admitted(tmp_path, monkeypatch)[:4]
        receipt = shadow_triggers.append_trigger_event(tmp_path, *args)
    else:
        *args, now = prepared(tmp_path, monkeypatch)
        receipt = shadow_fills.append_fill_event(tmp_path, *args)
        if stage == 'position-open':
            monkeypatch.setattr(shadow_positions, '_now', lambda: now)
            receipt = shadow_positions.append_position_open_event(tmp_path, *args)
    path, document = publish_input(tmp_path, monkeypatch, args, stage)
    assert main.shadow()['execution'] is not None
    if damage == 'receipt':
        receipt.write_text('{}')
    elif damage == 'ancestry':
        next((tmp_path / 'candidate-ledger').glob('*.json')).write_text('{}')
    elif damage == 'package':
        document['fact_packages'] = []
    elif damage == 'extra':
        document['net_pnl'] = '1000'
    elif damage == 'missing':
        del document['facts']
    elif damage == 'duplicate':
        path.write_text('{"schema_version":"x","schema_version":"y"}')
    elif damage == 'mixed':
        document['exit_policy'] = None
    elif damage == 'unapproved':
        document['fact_packages'][0]['review']['approved'] = False
    else:
        path.unlink()
        path.symlink_to(receipt)
    if damage in ('package', 'extra', 'missing', 'mixed', 'unapproved'):
        path.write_text(json.dumps(document))
    before = snapshot(tmp_path)
    state = main.shadow()
    assert state['execution'] is None
    if damage not in ('ancestry', 'symlink'):
        assert state['available']
    assert str(tmp_path) not in json.dumps(state)
    assert 'NO AUDITED EXECUTION' in main.shadow_page().body.decode()
    assert snapshot(tmp_path) == before


def reservation_input(tmp_path, monkeypatch, settled=False):
    from app.paper import shadow_allocations
    from tests.test_shadow_allocations import setup_allocation, setup_settlement

    if settled:
        args, portfolio, policy, packages, _ = setup_settlement(tmp_path, monkeypatch)
        shadow_allocations.append_capital_settlement(tmp_path, *args, portfolio, policy, packages)
        receipt = next((tmp_path / 'capital-reservations').glob('*.json'))
    else:
        args, portfolio, _ = setup_allocation(tmp_path, monkeypatch)
        receipt = shadow_allocations.append_capital_reservation(tmp_path, *args, portfolio)
    path, document = publish_input(tmp_path, monkeypatch, args, 'reservation')
    document['portfolio'] = portfolio.model_dump(mode='json')
    path.write_text(json.dumps(document))
    return path, document, receipt


@pytest.mark.parametrize('settled', [False, True])
def test_reservation_surface_is_historical_even_after_settlement(tmp_path, monkeypatch, settled):
    _, document, receipt = reservation_input(tmp_path, monkeypatch, settled)
    before = snapshot(tmp_path)
    view = main.shadow()['execution']
    assert view['collection_status'] == 'CAPITAL RESERVATION RECORDED'
    event = json.loads(receipt.read_bytes())
    reservation = view['capital_reservation']
    for key in ('market', 'currency', 'capital_reserved', 'risk_reserved', 'recorded_at'):
        assert reservation[key] == event[key]
    assert reservation['status'] == 'HISTORICAL RESERVATION / CURRENT AVAILABILITY NOT EVALUATED'
    assert view['portfolio_policy'] == document['portfolio']
    assert view['audit_references']['reservation_id'] == event['reservation_id']
    assert_unscored(view)
    body = main.shadow_page().body.decode()
    assert 'CURRENT AVAILABILITY NOT EVALUATED' in body
    assert 'NOT A BOUND ON GAP OR SLIPPAGE LOSS' in body
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize('damage', ['missing', 'amount', 'policy', 'position', 'fill', 'computed'])
def test_reservation_surface_reaudits_capital_and_full_entry_ancestry(tmp_path, monkeypatch, damage):
    path, document, receipt = reservation_input(tmp_path, monkeypatch)
    assert main.shadow()['execution'] is not None
    if damage == 'missing':
        receipt.unlink()
    elif damage == 'amount':
        from tests.test_shadow_allocations import rewrite_reservation
        event = json.loads(receipt.read_bytes())
        event['capital_reserved'] = '1'
        rewrite_reservation(receipt, event)  # Rehashing cannot bless false economics.
    elif damage == 'policy':
        document['portfolio']['initial_capital'] = '2000'
        path.write_text(json.dumps(document))
    elif damage == 'computed':
        document['available_cash'] = '1000'
        path.write_text(json.dumps(document))
    else:
        folder = 'position-open-events' if damage == 'position' else 'fill-events'
        next((tmp_path / folder).glob('*.json')).write_text('{}')
    before = snapshot(tmp_path)
    assert main.shadow()['available']
    assert main.shadow()['execution'] is None
    assert 'NO AUDITED EXECUTION' in main.shadow_page().body.decode()
    assert snapshot(tmp_path) == before


def test_missed_collection_surface_is_distinct_and_fails_closed(
    tmp_path,
    monkeypatch,
):
    """A retained missed session is neither a reconstructed nor empty watchlist."""
    from app.paper import shadow_collection
    from tests.test_shadow_collection import authenticated_watchlist
    from tests.test_today_ui import make_ui_db

    shadow_dir = tmp_path / "shadow"
    shadow_dir.mkdir()

    db_dir = tmp_path / "db"
    db_dir.mkdir()
    database = make_ui_db(db_dir)

    item, packages = authenticated_watchlist()

    monkeypatch.setattr(
        shadow_collection,
        "_now",
        lambda: item.session.decision_cutoff,
    )

    receipt = shadow_collection.record_missed_session(
        shadow_dir,
        record_id=item.record_id,
        session=item.session,
        session_package=packages[0],
        reason="NO_TIMELY_WATCHLIST",
    )

    missed_input = {
        "schema_version": "shadow-ui-missed-v1",
        "record_id": item.record_id,
        "session": item.session.model_dump(mode="json"),
        "session_package": packages[0].model_dump(mode="json"),
    }

    (shadow_dir / "missed.json").write_text(
        json.dumps(missed_input),
    )

    monkeypatch.setenv(
        "EGX_SHADOW_DIRECTORY",
        str(shadow_dir),
    )
    monkeypatch.setenv(
        "EGX_DB_PATH",
        str(database),
    )

    before = snapshot(shadow_dir)

    state = main.shadow()

    assert state["available"] is False
    assert state["collection"] is None
    assert state["execution"] is None

    missed = state["missed"]
    assert missed is not None
    assert missed["collection_status"] == "MISSED / NOT SCORED"
    assert missed["record_id"] == item.record_id
    assert missed["reason"] == "NO_TIMELY_WATCHLIST"
    assert missed["candidates"] is None
    assert missed["candidate_count"] is None

    shadow_body = main.shadow_page().body.decode()
    assert "MISSED / NOT SCORED" in shadow_body
    assert "No candidates reconstructed; not a zero-candidate decision." in shadow_body

    today_body = main.root().body.decode()
    assert "MISSED / NOT SCORED" in today_body
    assert "No candidates reconstructed." in today_body

    assert not (shadow_dir / "candidate-ledger").exists()
    assert not (shadow_dir / "watchlists").exists()
    assert not (shadow_dir / "execution.json").exists()
    assert snapshot(shadow_dir) == before

    # A locally modified receipt must not be rendered as trusted missed state.
    payload = json.loads(receipt.read_bytes())
    payload["reason"] = "VERIFIED"
    receipt.write_text(json.dumps(payload))

    tampered = snapshot(shadow_dir)

    state = main.shadow()

    assert state["available"] is False
    assert state["collection"] is None
    assert state["missed"] is None
    assert state["execution"] is None

    assert "MISSED / NOT SCORED" not in main.shadow_page().body.decode()
    assert "MISSED / NOT SCORED" not in main.root().body.decode()

    # Reads fail closed without altering or reconstructing retained state.
    assert snapshot(shadow_dir) == tampered
    assert not (shadow_dir / "candidate-ledger").exists()
    assert not (shadow_dir / "watchlists").exists()
