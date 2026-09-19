"""Artificial capital settlement integration; no empirical evidence."""
import json

import pytest

from app import main
from app.paper import shadow_allocations
from tests.test_shadow_allocations import setup_settlement, setup_continuation_settlement


def settlement_input(tmp_path, monkeypatch, continuation, closed=True):
    def encode(value):
        if isinstance(value, tuple):
            return [encode(item) for item in value]
        return value.model_dump(mode='json')

    chain_fields = {}
    if continuation:
        args, portfolio, chain, chain_packages, policy, packages, _ = (
            setup_continuation_settlement(tmp_path, monkeypatch, result='CLOSED' if closed else 'OPEN')
        )
        chain_fields = {'continuations': encode(chain), 'continuation_packages': encode(chain_packages)}
        receipt = shadow_allocations.append_continuation_capital_settlement(
            tmp_path, *args, portfolio, chain, chain_packages, policy, packages,
        ) if closed else None
    else:
        args, portfolio, policy, packages, _ = setup_settlement(tmp_path, monkeypatch, closed=closed)
        receipt = shadow_allocations.append_capital_settlement(
            tmp_path, *args, portfolio, policy, packages,
        ) if closed else None
    (tmp_path / 'input.json').write_text(json.dumps({
        'schema_version': 'shadow-ui-input-v1',
        'watchlist': encode(args[0]), 'evidence_packages': encode(args[1]),
    }))
    document = dict(zip(
        ('facts', 'fact_packages', 'fill_policy', 'fill_packages', 'exit_policy', 'exit_packages'),
        map(encode, (*args[2:], policy, packages)), strict=True,
    ))
    document.update(chain_fields, portfolio=encode(portfolio), schema_version=(
        'shadow-ui-continuation-settlement-v1' if continuation else 'shadow-ui-settlement-v1'
    ))
    path = tmp_path / 'execution.json'
    path.write_text(json.dumps(document))
    monkeypatch.setenv('EGX_SHADOW_DIRECTORY', str(tmp_path))
    return path, document, receipt


@pytest.mark.parametrize('continuation', [False, True])
def test_settlement_ui_reaudits_native_cash_without_writes(tmp_path, monkeypatch, continuation):
    _, _, receipt = settlement_input(tmp_path, monkeypatch, continuation)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    view = main.shadow()['execution']
    expected = json.loads(receipt.read_bytes())
    assert view['collection_status'] == 'CAPITAL SETTLED'
    assert view['position_status'] == 'CLOSED'
    for key in ('currency', 'capital_released', 'risk_released', 'exit_notional', 'exit_cost', 'net_exit_proceeds'):
        assert view['capital_settlement'][key] == expected[key]
    assert view['performance']['nav'] is None
    body = main.shadow_page().body.decode()
    assert 'capital settlement' in body
    assert 'AUTHENTICATED NATIVE CASH FLOW / NO NAV OR PERFORMANCE' in body
    assert 'EXPERIMENTAL / PAPER ONLY' in body
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


@pytest.mark.parametrize('continuation', [False, True])
@pytest.mark.parametrize('damage', ['receipt', 'missing', 'reservation', 'policy', 'package', 'mixed', 'cash'])
def test_settlement_ui_fails_closed_on_damaged_ancestry(tmp_path, monkeypatch, continuation, damage):
    path, document, receipt = settlement_input(tmp_path, monkeypatch, continuation)
    assert main.shadow()['execution'] is not None
    if damage == 'receipt':
        receipt.write_text('{}')
    elif damage == 'missing':
        receipt.unlink()
    elif damage == 'reservation':
        next((tmp_path / 'capital-reservations').glob('*.json')).write_text('{}')
    elif damage == 'policy':
        document['portfolio']['initial_capital'] = '999'
    elif damage == 'package':
        document['exit_packages'][0]['review']['approved'] = False
    elif damage == 'mixed':
        document['evaluation_facts'] = None
    else:
        document['net_exit_proceeds'] = '999'
    path.write_text(json.dumps(document))
    state = main.shadow()
    assert state['available']
    assert state['execution'] is None
    assert str(tmp_path) not in json.dumps(state)
    assert 'NO AUDITED EXECUTION' in main.shadow_page().body.decode()


@pytest.mark.parametrize('continuation', [False, True])
def test_open_trade_cannot_be_presented_as_settled(tmp_path, monkeypatch, continuation):
    settlement_input(tmp_path, monkeypatch, continuation, closed=False)
    assert main.shadow()['execution'] is None
    assert not (tmp_path / 'capital-settlements').exists()
