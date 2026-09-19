"""Artificial execution integration fixtures; never empirical evidence."""
import json
from decimal import Decimal

import pytest

from app import main
from app.paper.shadow_exits import append_exit_event
from tests.test_shadow_exits import prepared_exit


def execution_input(tmp_path, monkeypatch, high='101', low='99', exit_capacity=None):
    args, _, policy, packages = prepared_exit(
        tmp_path, monkeypatch, bar_changes={'high': Decimal(high), 'low': Decimal(low)},
    )
    if exit_capacity is not None:
        policy = policy.model_copy(update={'max_volume_participation_pct': Decimal(exit_capacity)})
    receipt = append_exit_event(tmp_path, *args, policy, packages)
    def encode(value):
        if isinstance(value, tuple):
            return [item.model_dump(mode='json') for item in value]
        return value.model_dump(mode='json')
    (tmp_path / 'input.json').write_text(json.dumps({
        'schema_version': 'shadow-ui-input-v1',
        'watchlist': encode(args[0]), 'evidence_packages': encode(args[1]),
    }))
    document = dict(zip(
        ('facts', 'fact_packages', 'fill_policy', 'fill_packages', 'exit_policy', 'exit_packages'),
        map(encode, (*args[2:], policy, packages)), strict=True,
    ))
    document.update(schema_version='shadow-ui-execution-v1',
                    evaluation_facts=None, evaluation_fact_packages=None)
    path = tmp_path / 'execution.json'
    path.write_text(json.dumps(document))
    monkeypatch.setenv('EGX_SHADOW_DIRECTORY', str(tmp_path))
    return path, receipt, document


@pytest.mark.parametrize('high,status', [('101', 'OPEN'), ('110', 'CLOSED')])
def test_execution_route_reaudits_without_writes(tmp_path, monkeypatch, high, status):
    execution_input(tmp_path, monkeypatch, high)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    state = main.shadow()
    assert state['available']
    view = state['execution']
    assert view['position_status'] == status
    assert view['performance']['nav'] is None
    if status == 'OPEN':
        assert view['open_paper_positions']['position']['unrealized_pnl'] is None
    else:
        assert view['closed_paper_trades']['trade']['net_pnl'] is not None
    body = main.shadow_page().body.decode()
    assert 'current position status UNKNOWN' in body
    assert 'EXPERIMENTAL / PAPER ONLY' in body
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


@pytest.mark.parametrize('damage', ['missing', 'receipt', 'package', 'numeric', 'extra', 'duplicate', 'symlink'])
def test_execution_damage_never_retains_result(tmp_path, monkeypatch, damage):
    path, receipt, document = execution_input(tmp_path, monkeypatch)
    assert main.shadow()['execution'] is not None
    if damage == 'missing':
        path.unlink()
    elif damage == 'receipt':
        receipt.write_text('{}')
    elif damage == 'package':
        document['exit_packages'][0]['review']['approved'] = False
        path.write_text(json.dumps(document))
    elif damage == 'numeric':
        document['exit_policy']['cost_bps_per_side'] = 5
        path.write_text(json.dumps(document))
    elif damage == 'extra':
        document['nav'] = '100'
        path.write_text(json.dumps(document))
    elif damage == 'duplicate':
        path.write_text('{"schema_version":"a","schema_version":"b"}')
    else:
        path.unlink()
        path.symlink_to(receipt)
    state = main.shadow()
    assert state['execution'] is None
    assert str(tmp_path) not in json.dumps(state)
    assert 'NO AUDITED EXECUTION' in main.shadow_page().body.decode()
    if damage != 'symlink':
        assert state['available']  # Candidate audit is independent of execution.


def test_unknown_exit_capacity_exposes_no_trade_or_pnl(tmp_path, monkeypatch):
    execution_input(tmp_path, monkeypatch, low='95', exit_capacity='0.001')
    view = main.shadow()['execution']
    assert view['position_status'] == 'UNKNOWN'
    assert view['closed_paper_trades'] == {'status': 'NOT EVALUATED'}
    assert view['open_paper_positions'] == {'status': 'NOT EVALUATED'}
    assert all(value is None for key, value in view['performance'].items() if key != 'status')


def continuation_input(tmp_path, monkeypatch, status='CLOSED'):
    from tests.test_shadow_allocations import setup_continuation_settlement

    args, _, chain, chain_packages, policy, packages, receipt = (
        setup_continuation_settlement(tmp_path, monkeypatch, result=status)
    )

    def encode(value):
        if isinstance(value, tuple):
            return [encode(item) for item in value]
        return value.model_dump(mode='json')

    (tmp_path / 'input.json').write_text(json.dumps({
        'schema_version': 'shadow-ui-input-v1',
        'watchlist': encode(args[0]), 'evidence_packages': encode(args[1]),
    }))
    document = dict(zip(
        ('facts', 'fact_packages', 'fill_policy', 'fill_packages',
         'continuations', 'continuation_packages', 'exit_policy', 'exit_packages'),
        map(encode, (*args[2:], chain, chain_packages, policy, packages)), strict=True,
    ))
    document['schema_version'] = 'shadow-ui-continuation-v1'
    path = tmp_path / 'execution.json'
    path.write_text(json.dumps(document))
    monkeypatch.setenv('EGX_SHADOW_DIRECTORY', str(tmp_path))
    return path, receipt, document


@pytest.mark.parametrize('status', ['OPEN', 'CLOSED', 'UNKNOWN'])
def test_continuation_ui_preserves_observation_semantics(tmp_path, monkeypatch, status):
    continuation_input(tmp_path, monkeypatch, status)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    view = main.shadow()['execution']
    assert view['position_status'] == status
    assert view['collection_status'] == 'CONTINUATION EXIT EVALUATED'
    assert view['performance']['nav'] is None
    if status == 'CLOSED':
        assert view['closed_paper_trades']['trade']['net_pnl'] is not None
    elif status == 'OPEN':
        assert view['open_paper_positions']['position']['unrealized_pnl'] is None
    else:
        assert view['open_paper_positions'] == {'status': 'NOT EVALUATED'}
        assert view['closed_paper_trades'] == {'status': 'NOT EVALUATED'}
    body = main.shadow_page().body.decode()
    assert 'continuation evaluations' in body
    assert 'current position status UNKNOWN' in body
    assert 'same-session observation' not in body
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


@pytest.mark.parametrize('damage', ['receipt', 'package', 'empty', 'omitted', 'duplicate', 'mixed'])
def test_continuation_ui_rejects_damaged_ancestry(tmp_path, monkeypatch, damage):
    path, receipt, document = continuation_input(tmp_path, monkeypatch)
    assert main.shadow()['execution'] is not None
    if damage == 'receipt':
        receipt.write_text('{}')
    elif damage == 'package':
        document['continuation_packages'][0][0]['review']['approved'] = False
    elif damage == 'empty':
        document['continuations'] = []
        document['continuation_packages'] = []
    elif damage == 'omitted':
        document['continuation_packages'] = []
    elif damage == 'duplicate':
        document['continuations'] *= 2
        document['continuation_packages'] *= 2
    else:
        document['evaluation_facts'] = None
    path.write_text(json.dumps(document))
    state = main.shadow()
    assert state['available']
    assert state['execution'] is None
    assert str(tmp_path) not in json.dumps(state)
    assert 'NO AUDITED EXECUTION' in main.shadow_page().body.decode()


def test_audited_paper_costs_do_not_claim_ibkr_applicability(tmp_path, monkeypatch):
    path, _, document = execution_input(tmp_path, monkeypatch)
    economics = main.shadow()['execution']['paper_economics']
    assert economics['entry_policy'] == document['fill_policy']
    assert economics['exit_policy'] == document['exit_policy']
    assert economics['ibkr_applicability'] == 'NOT ESTABLISHED / NO VERIFIED IBKR SCHEDULE BINDING'
    assert 'NOT A BROKER QUOTE' in main.shadow_page().body.decode()
    document['fill_policy']['fixed_cost_per_side'] = '999'
    path.write_text(json.dumps(document))
    assert main.shadow()['execution'] is None
    assert 'AUDITED PAPER ASSUMPTIONS' not in main.shadow_page().body.decode()
