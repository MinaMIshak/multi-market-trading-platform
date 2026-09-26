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
