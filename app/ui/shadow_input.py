"""Bounded local JSON transport for canonical shadow contracts, never admission."""
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
import json
import math
import os
from pathlib import Path
import stat
from types import UnionType
from typing import Annotated, Literal, Union, get_args, get_origin
from uuid import UUID

from pydantic import BaseModel

from app.paper.shadow_records import ShadowWatchlist
from app.research.historical_evidence import HistoricalEvidencePackage

MAX_INPUT_BYTES = 4 * 1024 * 1024


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate input field")
        result[key] = value
    return result


def _decode(kind, value):
    origin, args = get_origin(kind), get_args(kind)
    if origin is Annotated:
        return _decode(args[0], value)
    if origin in (Union, UnionType):
        if value is None and type(None) in args:
            return None
        choices = [item for item in args if item is not type(None)]
        if len(choices) != 1:
            raise ValueError("unsupported transport union")
        return _decode(choices[0], value)
    if origin is Literal:
        if not any(type(value) is type(item) and value == item for item in args):
            raise ValueError("invalid literal")
        return value
    if isinstance(kind, type) and issubclass(kind, Enum):
        if type(value) is not str:
            raise ValueError("canonical enum string required")
        try:
            return kind(value)
        except ValueError as exc:
            raise ValueError("invalid enum value") from exc
    if origin is tuple:
        if type(value) is not list or len(args) != 2 or args[1] is not Ellipsis:
            raise ValueError("JSON array required")
        return tuple(_decode(args[0], item) for item in value)
    if isinstance(kind, type) and issubclass(kind, BaseModel):
        if type(value) is not dict or set(value) != set(kind.model_fields):
            raise ValueError("complete exact model fields required")
        return kind(**{key: _decode(field.annotation, value[key])
                       for key, field in kind.model_fields.items()})
    if kind in (datetime, date, Decimal, UUID):
        if type(value) is not str:
            raise ValueError("canonical string required")
        if kind is datetime:
            decoded = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if decoded.tzinfo is None or decoded.utcoffset() != timezone.utc.utcoffset(None):
                raise ValueError("explicit UTC required")
            return decoded.astimezone(timezone.utc)
        if kind is Decimal:
            decoded = Decimal(value)
            if not decoded.is_finite():
                raise ValueError("finite decimal required")
            return decoded
        return date.fromisoformat(value) if kind is date else UUID(value)
    if kind is float and type(value) is float and math.isfinite(value):
        return value
    if kind in (str, int, bool) and type(value) is kind:
        return value
    raise ValueError("invalid transport type")


def _read_document(path: Path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_INPUT_BYTES:
            raise ValueError("bounded regular input required")
        content = stream.read(MAX_INPUT_BYTES + 1)
    if len(content) > MAX_INPUT_BYTES:
        raise ValueError("input too large")
    document = json.loads(content, object_pairs_hook=_unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid number")))
    return document


def read_shadow_input(directory: Path):
    """Read only the fixed local input file; callers must re-audit the ledger."""
    from app.ui.shadow import ShadowWatchlistInput

    document = _read_document(directory / 'input.json')
    if type(document) is not dict or set(document) != {'schema_version', 'watchlist', 'evidence_packages'}:
        raise ValueError("invalid shadow input envelope")
    if document['schema_version'] != 'shadow-ui-input-v1':
        raise ValueError("unsupported input version")
    return ShadowWatchlistInput(
        _decode(ShadowWatchlist, document['watchlist']),
        _decode(tuple[HistoricalEvidencePackage, ...], document['evidence_packages']),
    )


def read_shadow_missed(directory: Path):
    """Read an explicit missed-session receipt request, never reconstruct picks."""
    from app.paper.shadow_records import ShadowSession
    from app.paper.shadow_report import missed_collection_view

    document = _read_document(directory / 'missed.json')
    fields = {'record_id': str, 'session': ShadowSession,
              'session_package': HistoricalEvidencePackage}
    if (type(document) is not dict or set(document) != {'schema_version', *fields}
            or document['schema_version'] != 'shadow-ui-missed-v1'):
        raise ValueError('invalid missed collection envelope')
    return missed_collection_view(
        directory, **{key: _decode(kind, document[key]) for key, kind in fields.items()},
    )


def read_shadow_security_types(directory: Path, source):
    """Re-audit explicit exact-dated candidate classifications; never infer ETF peers."""
    from app.paper.shadow_security_type import audit_security_type_binding
    from app.us.historical_identity import HistoricalUSListingFact

    document = _read_document(directory / 'security-type.json')
    if (type(document) is not dict
            or set(document) != {'schema_version', 'bindings'}
            or document['schema_version'] != 'shadow-ui-security-types-v1'
            or type(document['bindings']) is not list
            or not 1 <= len(document['bindings']) <= 512):
        raise ValueError('invalid security-type envelope')

    results = {}
    for item in document['bindings']:
        if (type(item) is not dict
                or set(item) != {'candidate_id', 'fact'}
                or type(item['candidate_id']) is not str
                or not item['candidate_id']):
            raise ValueError('invalid security-type binding input')
        candidate_id = item['candidate_id']
        if candidate_id in results:
            raise ValueError('duplicate security-type candidate binding')
        fact = _decode(HistoricalUSListingFact, item['fact'])
        results[candidate_id] = audit_security_type_binding(
            directory,
            source.watchlist,
            source.evidence_packages,
            candidate_id,
            fact,
        )

    return {
        'status': 'AUDITED EXACT-DATED SECURITY TYPES / NOT AN ETF COMPARISON',
        'bindings': results,
    }


def read_shadow_execution(directory: Path, source):
    """Decode one execution observation and re-audit its complete ancestry."""
    from app.paper.shadow_continuations import ForwardContinuationBundle
    from app.paper.shadow_exits import ShadowExitPolicy
    from app.paper.shadow_facts import ForwardFactBundle
    from app.paper.shadow_fills import ShadowFillPolicy
    from app.paper.shadow_portfolio import ShadowPortfolioPolicy
    from app.paper.shadow_report import (
        capital_reservation_view, capital_settlement_view, continuation_capital_settlement_view,
        continuation_exit_evaluation_view, exit_evaluation_view,
        entry_fill_view, position_open_view, trigger_evaluation_view,
    )

    fields = {
        'facts': ForwardFactBundle,
        'fact_packages': tuple[HistoricalEvidencePackage, ...],
        'fill_policy': ShadowFillPolicy,
        'fill_packages': tuple[HistoricalEvidencePackage, ...],
        'exit_policy': ShadowExitPolicy,
        'exit_packages': tuple[HistoricalEvidencePackage, ...],
        'evaluation_facts': ForwardFactBundle | None,
        'evaluation_fact_packages': tuple[HistoricalEvidencePackage, ...] | None,
    }
    document = _read_document(directory / 'execution.json')
    version = document.get('schema_version') if type(document) is dict else None
    reader = exit_evaluation_view
    early_readers = {
        'shadow-ui-trigger-v1': trigger_evaluation_view,
        'shadow-ui-entry-fill-v1': entry_fill_view,
        'shadow-ui-position-open-v1': position_open_view,
        'shadow-ui-reservation-v1': capital_reservation_view,
    }
    if version in early_readers:
        reader = early_readers[version]
        fields = {key: fields[key] for key in (
            ('facts', 'fact_packages') if version == 'shadow-ui-trigger-v1'
            else ('facts', 'fact_packages', 'fill_policy', 'fill_packages')
        )}
        if version == 'shadow-ui-reservation-v1':
            fields['portfolio'] = ShadowPortfolioPolicy
    if version in ('shadow-ui-continuation-v1', 'shadow-ui-continuation-settlement-v1'):
        fields.pop('evaluation_facts')
        fields.pop('evaluation_fact_packages')
        fields.update(
            continuations=tuple[ForwardContinuationBundle, ...],
            continuation_packages=tuple[tuple[HistoricalEvidencePackage, ...], ...],
        )
        reader = continuation_exit_evaluation_view
    if version in ('shadow-ui-settlement-v1', 'shadow-ui-continuation-settlement-v1'):
        fields.pop('evaluation_facts', None)
        fields.pop('evaluation_fact_packages', None)
        fields['portfolio'] = ShadowPortfolioPolicy
        reader = (continuation_capital_settlement_view
                  if version == 'shadow-ui-continuation-settlement-v1'
                  else capital_settlement_view)
    if version == 'shadow-ui-settlement-v2':
        # v2 keeps the explicit (nullable) same-session evaluation facts that a
        # single-session exit and its settlement were audited against.
        fields['portfolio'] = ShadowPortfolioPolicy
        reader = capital_settlement_view
    if (type(document) is not dict or set(document) != {'schema_version', *fields}
            or version not in ('shadow-ui-execution-v1', 'shadow-ui-continuation-v1',
                               'shadow-ui-settlement-v1', 'shadow-ui-settlement-v2',
                               'shadow-ui-continuation-settlement-v1',
                               *early_readers)):
        raise ValueError('invalid execution envelope')
    decoded = {key: _decode(kind, document[key]) for key, kind in fields.items()}
    report = reader(
        directory, source.watchlist, source.evidence_packages, **decoded,
    )
    if 'fill_policy' not in decoded:
        return report
    # Only surface policies after the execution reader authenticates their ancestry.
    return report | {'paper_economics': {
        'status': 'AUDITED PAPER ASSUMPTIONS / NOT A BROKER QUOTE',
        'ibkr_applicability': 'NOT ESTABLISHED / NO VERIFIED IBKR SCHEDULE BINDING',
        'entry_policy': decoded['fill_policy'].model_dump(mode='json'),
        'exit_policy': (decoded['exit_policy'].model_dump(mode='json')
                        if 'exit_policy' in decoded else None),
    }}


def read_shadow_portfolio(directory: Path):
    """Re-audit a dated native snapshot; never accept supplied valuation JSON."""
    from app.paper.shadow_daily_snapshots import (
        audit_cash_only_daily_portfolio_snapshot, audit_marked_daily_portfolio_snapshot,
    )
    from app.paper.shadow_portfolio import ShadowPortfolioPolicy

    document = _read_document(directory / 'portfolio.json')
    if (type(document) is not dict
            or set(document) != {'schema_version', 'portfolio', 'snapshot_date_utc', 'requests'}
            or document['schema_version'] != 'shadow-ui-portfolio-v1'):
        raise ValueError('invalid portfolio envelope')
    portfolio = _decode(ShadowPortfolioPolicy, document['portfolio'])
    snapshot_date = _decode(date, document['snapshot_date_utc'])
    requests = _decode_mark_requests(document['requests'])
    if requests:
        return audit_marked_daily_portfolio_snapshot(directory, portfolio, snapshot_date, requests)
    return audit_cash_only_daily_portfolio_snapshot(directory, portfolio, snapshot_date)


def _decode_mark_requests(items):
    from typing import get_type_hints
    from app.paper.shadow_continuations import ForwardContinuationBundle
    from app.paper.shadow_daily_portfolio import SingleSessionMarkRequest, ContinuationMarkRequest

    if type(items) is not list:
        raise ValueError('mark request array required')
    requests = []
    for item in items:
        if type(item) is not dict or set(item) != {'kind', 'inputs'}:
            raise ValueError('invalid mark envelope')
        if item['kind'] == 'SAME_SESSION':
            kind = SingleSessionMarkRequest
        elif item['kind'] == 'CONTINUATION':
            kind = ContinuationMarkRequest
        else:
            raise ValueError('unsupported mark kind')
        fields = get_type_hints(kind)
        if kind is ContinuationMarkRequest:
            fields['continuations'] = tuple[ForwardContinuationBundle, ...]
            fields['continuation_packages'] = tuple[tuple[HistoricalEvidencePackage, ...], ...]
        inputs = item['inputs']
        if type(inputs) is not dict or set(inputs) != set(fields):
            raise ValueError('complete exact mark inputs required')
        requests.append(kind(**{key: _decode(annotation, inputs[key])
                                for key, annotation in fields.items()}))
    return tuple(requests)


def read_shadow_series(directory: Path):
    """Re-audit every dated snapshot; never accept computed series values."""
    from app.paper.shadow_daily_series import (
        CashSnapshotRequest, MarkedSnapshotRequest, authenticated_daily_portfolio_series,
    )
    from app.paper.shadow_portfolio import ShadowPortfolioPolicy

    document = _read_document(directory / 'series.json')
    if (type(document) is not dict
            or set(document) != {'schema_version', 'portfolio', 'snapshots'}
            or document['schema_version'] != 'shadow-ui-series-v1'
            or type(document['snapshots']) is not list
            or not 2 <= len(document['snapshots']) <= 512):
        raise ValueError('invalid series envelope')
    portfolio = _decode(ShadowPortfolioPolicy, document['portfolio'])
    requests = []
    for item in document['snapshots']:
        if type(item) is not dict or set(item) != {'snapshot_date_utc', 'requests'}:
            raise ValueError('invalid snapshot request')
        day = _decode(date, item['snapshot_date_utc'])
        marks = _decode_mark_requests(item['requests'])
        requests.append(MarkedSnapshotRequest(day, marks) if marks else CashSnapshotRequest(day))
    return authenticated_daily_portfolio_series(directory, portfolio, tuple(requests))
