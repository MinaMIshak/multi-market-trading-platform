"""Lexical EGX scope checks; valid spelling is never membership evidence."""
import re
from typing import Literal, get_args


_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,63}")


def valid_scope_symbol(value):
    """Match the existing UniverseMember contract without normalizing identity."""
    return isinstance(value, str) and _SYMBOL.fullmatch(value) is not None


SCAN_STATUSES = ("WATCH", "READY_NO_SIGNAL", "EVIDENCE_BLOCKED", "DATA_STALE", "NOT_READY")

# Supported launch/refresh scope, never authoritative universe evidence.
LaunchSymbol = Literal["COMI", "EAST", "FWRY", "ORAS", "SWDY"]
LAUNCH_SYMBOLS = get_args(LaunchSymbol)


def require_equity_identity(resolved, *, symbol, instrument_id):
    """Admit an exact master identity; this does not prove dated membership."""
    if (not isinstance(resolved, dict)
            or not valid_scope_symbol(symbol) or instrument_id is None
            or str(resolved.get('instrument_id')) != str(instrument_id)
            or resolved.get('canonical_ticker') != symbol):
        raise ValueError('security-master identity mismatch')
    if resolved.get('instrument_type') != 'EQUITY':
        raise ValueError('security-master equity classification required')
