"""Lexical EGX scope checks; valid spelling is never membership evidence."""
import re


_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,63}")


def valid_scope_symbol(value):
    """Match the existing UniverseMember contract without normalizing identity."""
    return isinstance(value, str) and _SYMBOL.fullmatch(value) is not None


SCAN_STATUSES = ("WATCH", "READY_NO_SIGNAL", "EVIDENCE_BLOCKED", "DATA_STALE", "NOT_READY")
