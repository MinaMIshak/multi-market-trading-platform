"""Explicit local scan configuration; loading never authorizes execution."""
from dataclasses import dataclass
from pathlib import Path


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
            or any(type(s) is not str or not s or s != s.strip() for s in raw['symbols'])
            or len(set(raw['symbols'])) != len(raw['symbols'])
            or type(raw['launch_inputs']) is not dict
            or not set(raw['launch_inputs']) <= set(raw['symbols'])):
        raise ValueError('invalid explicit scan configuration')
    sources = {}
    for symbol, document in raw['launch_inputs'].items():
        try:
            source = _decode_launch(document)
            if source.symbol == symbol:
                sources[symbol] = source
        except ValueError:
            # Invalid per-symbol evidence stays in scope with no launch input.
            # Never expose raw document/validation errors (may contain secrets).
            continue
    return ScanConfiguration(tuple(raw['symbols']), raw['scope_reference'], sources)
