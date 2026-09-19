"""Bounded local JSON transport for canonical shadow contracts, never admission."""
from datetime import date, datetime, timezone
from decimal import Decimal
import json
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
    if kind in (str, int, bool) and type(value) is kind:
        return value
    raise ValueError("invalid transport type")


def read_shadow_input(directory: Path):
    """Read only the fixed local input file; callers must re-audit the ledger."""
    from app.ui.shadow import ShadowWatchlistInput

    path = directory / 'input.json'
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
    if type(document) is not dict or set(document) != {'schema_version', 'watchlist', 'evidence_packages'}:
        raise ValueError("invalid shadow input envelope")
    if document['schema_version'] != 'shadow-ui-input-v1':
        raise ValueError("unsupported input version")
    return ShadowWatchlistInput(
        _decode(ShadowWatchlist, document['watchlist']),
        _decode(tuple[HistoricalEvidencePackage, ...], document['evidence_packages']),
    )
