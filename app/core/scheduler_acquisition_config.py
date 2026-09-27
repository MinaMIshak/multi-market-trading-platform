"""Explicit dated acquisition configuration; declarations are not source review.

No free daily transport is registered yet. Unknown sources fail before legacy
secret access. A future adapter must reuse reviewed source admission, never
interpret this configuration as licensing or market readiness evidence.
"""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path

from app.data.daily_refresh_job import DailyRefreshTarget
from app.data.quota import DailyQuotaCostContract, DailyQuotaCostContracts, VerifiedQuotaCost


MAX_CONFIG_BYTES = 1_000_000


def _object(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise ValueError('invalid acquisition configuration fields')
    return value


def _text(value):
    if type(value) is not str or not value or value != value.strip():
        raise ValueError('explicit acquisition identity required')
    return value


def _date(value):
    value = _text(value)
    result = date.fromisoformat(value)
    if result.isoformat() != value:
        raise ValueError('ISO dated acquisition scope required')
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate acquisition configuration field')
        result[key] = value
    return result


def configured_provider(name):
    # Do not dynamically import code or silently select a paid/legacy source.
    raise ValueError('configured free daily provider adapter unavailable')


def load_acquisition_options(path, *, provider_factory=configured_provider):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('absolute acquisition configuration path required')
    with path.open('rb') as stream:
        payload = stream.read(MAX_CONFIG_BYTES + 1)
    if len(payload) > MAX_CONFIG_BYTES:
        raise ValueError('acquisition configuration too large')
    doc = _object(json.loads(payload, object_pairs_hook=_unique_object),
                  ('schema_version', 'provider', 'lookback_days', 'targets', 'cost_contracts'))
    if type(doc['schema_version']) is not int or doc['schema_version'] != 1:
        raise ValueError('unsupported acquisition schema')
    provider_name = _text(doc['provider'])
    if provider_name != provider_name.lower() or provider_name in {'canonical', 'eodhd', 'egid'}:
        raise ValueError('explicit free daily provider required')
    lookback = doc['lookback_days']
    if type(lookback) is not int or not 1 <= lookback <= 1500:
        raise ValueError('bounded acquisition lookback required')
    if type(doc['targets']) is not list or not doc['targets']:
        raise ValueError('explicit acquisition targets required')
    targets = []
    for item in doc['targets']:
        item = _object(item, ('canonical_symbol', 'provider_symbol'))
        canonical, alias = (_text(item[key]) for key in ('canonical_symbol', 'provider_symbol'))
        if canonical != canonical.upper() or alias != alias.upper():
            raise ValueError('exact uppercase acquisition symbols required')
        targets.append(DailyRefreshTarget(canonical, alias))
    if (len({t.canonical_symbol for t in targets}) != len(targets)
            or len({t.provider_symbol for t in targets}) != len(targets)):
        raise ValueError('duplicate acquisition target')
    if type(doc['cost_contracts']) is not list:
        raise ValueError('exact acquisition cost contracts required')
    contracts = []
    aliases = {t.provider_symbol for t in targets}
    for item in doc['cost_contracts']:
        item = _object(item, ('symbol', 'start_date', 'end_date', 'units', 'evidence'))
        contract = DailyQuotaCostContract(provider_name, _text(item['symbol']),
            _date(item['start_date']), _date(item['end_date']),
            VerifiedQuotaCost(item['units'], _text(item['evidence'])))
        if contract.symbol not in aliases or (contract.end_date - contract.start_date).days != lookback:
            raise ValueError('acquisition cost scope mismatch')
        contracts.append(contract)
    batch = DailyQuotaCostContracts(tuple(contracts))
    # Each configured session must cover the entire batch before construction.
    for start, end in {(c.start_date, c.end_date) for c in contracts}:
        batch.require_requests(provider_name=provider_name, targets=targets,
                               start_date=start, end_date=end)
    provider = provider_factory(provider_name)
    if getattr(provider, 'name', None) != provider_name:
        raise ValueError('acquisition provider identity mismatch')
    return dict(provider=provider, targets=tuple(targets),
                quota_cost_contract=batch, lookback_days=lookback)


def worker_acquisition_options(mode, environ, *, loader=load_acquisition_options):
    # OBSERVE never opens acquisition files or constructs a transport.
    if mode == 'observe':
        return {}
    path = environ.get('EGX_ACQUISITION_CONFIG_PATH', '')
    if not path:
        return {}  # Preserve the existing explicitly selected legacy mode.
    return loader(path)
