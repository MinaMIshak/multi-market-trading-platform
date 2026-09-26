"""Explicit local scan configuration; loading never authorizes execution."""
from dataclasses import dataclass, field
from pathlib import Path

from app.egx_scope import LAUNCH_SYMBOLS, valid_scope_symbol


def _read(path):
    from app.ui.shadow_input import _read_document
    return _read_document(path)


def _decode_launch(document):
    from app.paper.swing_launch import SwingLaunchInput
    from app.ui.shadow_input import _decode
    return _decode(SwingLaunchInput, document)


@dataclass(frozen=True)
class ScanConfiguration:
    symbols: tuple[str, ...]
    scope_reference: str
    sources: dict
    source_errors: dict = field(default_factory=dict)


def load_scan_configuration(path):
    """Decode an exact envelope and retain missing evidence as blocked scope.

    Inline launch documents use the existing strict transport and admission
    contract. A scope reference is attribution, not dated membership evidence.
    No providers, verification, publication or database writes occur here.
    """
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('absolute scan configuration path required')
    raw = _read(path)
    if (type(raw) is not dict
            or set(raw) != {'schema_version', 'scope_reference', 'symbols', 'launch_inputs'}
            or raw['schema_version'] != 'egx-explicit-scan-v1'
            or type(raw['scope_reference']) is not str or not raw['scope_reference'].strip()
            or type(raw['symbols']) is not list or not raw['symbols']
            or any(not valid_scope_symbol(s) for s in raw['symbols'])
            or len(set(raw['symbols'])) != len(raw['symbols'])
            or type(raw['launch_inputs']) is not dict
            or not set(raw['launch_inputs']) <= set(raw['symbols'])):
        raise ValueError('invalid explicit scan configuration')
    sources = {}
    source_errors = {}
    for symbol, document in raw['launch_inputs'].items():
        if symbol not in LAUNCH_SYMBOLS:
            source_errors[symbol] = 'LAUNCH_SYMBOL_UNSUPPORTED'
            continue
        try:
            source = _decode_launch(document)
            if source.symbol == symbol:
                sources[symbol] = source
            else:
                source_errors[symbol] = 'LAUNCH_IDENTITY_MISMATCH'
        except ValueError:
            # Invalid per-symbol evidence stays in scope with no launch input.
            # Never expose raw document/validation errors (may contain secrets).
            source_errors[symbol] = 'INVALID_LAUNCH_EVIDENCE'
    return ScanConfiguration(tuple(raw['symbols']), raw['scope_reference'], sources, source_errors)
