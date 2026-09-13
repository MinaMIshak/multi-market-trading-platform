"""Artificial policy fixtures only; no empirical market evidence."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import json

import pytest

from app.paper import shadow_portfolio as sp


def policy(**changes):
    values = dict(base_currency="USD", effective_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
                  egx_capital_fraction=Decimal('.4'), us_capital_fraction=Decimal('.5'),
                  minimum_cash_fraction=Decimal('.1'), max_position_fraction=Decimal('.1'),
                  max_position_risk_fraction=Decimal('.01'),
                  max_portfolio_risk_fraction=Decimal('.05'))
    return sp.ShadowPortfolioPolicy(**(values | changes))


@pytest.mark.parametrize('changes', [
    dict(egx_capital_fraction=Decimal('1'), us_capital_fraction=Decimal('1')),
    dict(us_capital_fraction=Decimal('.50000000000000000000000000001')),
    dict(max_position_risk_fraction=Decimal('.06')),
    dict(max_position_fraction=Decimal('.6')),
    dict(max_portfolio_risk_fraction=Decimal('.95')),
    dict(egx_capital_fraction=Decimal('0'), us_capital_fraction=Decimal('0')),
    dict(us_capital_fraction=float('nan')), dict(us_capital_fraction=.5),
    dict(us_capital_fraction=Decimal('Infinity')),
    dict(effective_at=datetime(2030, 1, 1)),
])
def test_reject_invalid_budget(changes):
    with localcontext() as context:
        context.prec = 3
        with pytest.raises(ValueError):
            policy(**changes)


def test_freeze_audit_and_competing_policy(tmp_path, monkeypatch):
    p = policy()
    monkeypatch.setattr(sp, '_now', lambda: p.effective_at - timedelta(days=1))
    sp.freeze_portfolio_policy(tmp_path, p)
    assert sp.audit_portfolio_policy(tmp_path, p)['status'] == 'POLICY ONLY / NOT ALLOCATED'
    with pytest.raises(FileExistsError):
        sp.freeze_portfolio_policy(tmp_path, policy(base_currency='EGP'))
    with pytest.raises(ValueError):
        sp.audit_portfolio_policy(tmp_path, policy(base_currency='EGP'))


@pytest.mark.parametrize('offset', [0, 1])
def test_late_freeze(tmp_path, monkeypatch, offset):
    p = policy()
    monkeypatch.setattr(sp, '_now', lambda: p.effective_at + timedelta(seconds=offset))
    with pytest.raises(ValueError):
        sp.freeze_portfolio_policy(tmp_path, p)
    assert not (tmp_path / 'portfolio-policy.json').exists()


@pytest.mark.parametrize('corruption', ['budget', 'future', 'duplicate', 'extra'])
def test_receipt_corruption(tmp_path, monkeypatch, corruption):
    p = policy()
    now = p.effective_at - timedelta(days=1)
    monkeypatch.setattr(sp, '_now', lambda: now)
    path = sp.freeze_portfolio_policy(tmp_path, p)
    data = json.loads(path.read_text())
    if corruption == 'budget':
        data['policy']['us_capital_fraction'] = '1'
    elif corruption == 'future':
        data['frozen_at'] = (now + timedelta(seconds=1)).isoformat()
    elif corruption == 'extra':
        data['allocated'] = True
    path.write_text(json.dumps(data))
    if corruption == 'duplicate':
        path.write_text(path.read_text()[:-1] + ',"status":"POLICY ONLY / NOT ALLOCATED"}')
    with pytest.raises(ValueError):
        sp.audit_portfolio_policy(tmp_path, p)


def test_rollback_cleans_receipt(tmp_path, monkeypatch):
    p = policy()
    now = p.effective_at - timedelta(days=1)
    ticks = iter([now, now - timedelta(seconds=1)])
    monkeypatch.setattr(sp, '_now', lambda: next(ticks))
    with pytest.raises(ValueError):
        sp.freeze_portfolio_policy(tmp_path, p)
    assert not (tmp_path / 'portfolio-policy.json').exists()
