"""Deterministic research notes from verified EGX operational receipts.

No language model and no generated claims: each fact restates a field of a
hash-verified PAPER_SIGNAL_VERIFIED receipt with its audit locator. Trade plans
are deliberately excluded; research carries no entry, stop or order levels.
Anything unverifiable collapses to an UNKNOWN-only note.
"""
import re
from datetime import date, datetime

from pydantic import ValidationError

from app.research.intelligence import ResearchNote

_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_UNSOURCED = (
    ('fundamentals', 'Fundamentals and valuation', 'No platform-owned fundamentals source is connected'),
    ('disclosures', 'Corporate disclosures and news', 'No platform-owned disclosure source is connected'),
)


def _unknown_note(symbol, generated_at, reason):
    statements = [{'statement_id': 'verification', 'kind': 'UNKNOWN',
                   'text': 'Verified operational receipt', 'reason': reason}]
    statements += [{'statement_id': key, 'kind': 'UNKNOWN', 'text': text, 'reason': why}
                   for key, text, why in _UNSOURCED]
    return ResearchNote.model_validate({
        'schema_version': 'research-note-v1', 'market': 'EGX', 'subject': symbol,
        'generated_at': generated_at, 'statements': statements}).projection()


def _text(item, key):
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'missing {key}')
    return value


def _receipt_note(item, generated_at):
    status = item.get('status')
    if status not in ('WATCH', 'READY_NO_SIGNAL', 'DATA_STALE'):
        raise ValueError('no verified receipt')
    decided = datetime.fromisoformat(_text(item, 'decision_at'))
    expiry = datetime.fromisoformat(_text(item, 'valid_until'))
    session = date.fromisoformat(_text(item, 'last_verified_session'))
    history_start = date.fromisoformat(_text(item, 'history_start'))
    raw_sha256 = _text(item, 'raw_sha256')
    bars = item.get('bar_count')
    if not _SHA256.match(raw_sha256) or type(bars) is not int or bars < 1 or history_start > session:
        raise ValueError('invalid history evidence')
    strategy = _text(item, 'strategy_id') + ' v' + _text(item, 'strategy_version')
    locator = 'audit_event:PIT_DATA_VALIDATED:' + _text(item, 'pit_audit_id')
    # The receipt is recorded at decision time; the reader overwrites an expired
    # status with DATA_STALE, so the original status is not restated then.
    shown = '' if status == 'DATA_STALE' else ' status ' + status
    statements = [
        {'statement_id': 'verification', 'kind': 'SOURCE_FACT',
         'text': (f'{strategy} PAPER/SHADOW verification receipt{shown} for session '
                  f'{session.isoformat()}; empirical status {_text(item, "empirical_status")}'),
         'provenance': {'source_id': 'platform-operational-receipt', 'locator': locator,
                        'observed_at': decided, 'as_of': session}},
        {'statement_id': 'history', 'kind': 'SOURCE_FACT',
         'text': (f'{bars} daily bars {history_start.isoformat()} to {session.isoformat()} '
                  f'from provider {_text(item, "provider")}'),
         'provenance': {'source_id': 'platform-daily-canonical', 'locator': 'raw_sha256:' + raw_sha256,
                        'observed_at': decided, 'as_of': session}},
    ]
    if status == 'DATA_STALE':
        if generated_at < expiry:
            raise ValueError('stale status without expiry')
        statements.append({'statement_id': 'freshness', 'kind': 'DERIVED_METRIC',
                           'text': f'DATA_STALE: verification window ended {expiry.isoformat()}',
                           'inputs': ['verification'],
                           'method': 'reader observed_at >= receipt valid_until'})
    statements += [{'statement_id': key, 'kind': 'UNKNOWN', 'text': text, 'reason': why}
                   for key, text, why in _UNSOURCED]
    return ResearchNote.model_validate({
        'schema_version': 'research-note-v1', 'market': 'EGX', 'subject': item['symbol'],
        'generated_at': generated_at, 'statements': statements}).projection()


def receipt_research_notes(market_state):
    """One note per observed EGX symbol, or None when the reader is unavailable."""
    if not market_state.get('available'):
        return None
    try:
        generated_at = datetime.fromisoformat(market_state['observed_at'])
    except (KeyError, TypeError, ValueError):
        return None
    grouped = {}
    for item in market_state['symbols']:
        symbol = item.get('symbol')
        if not isinstance(symbol, str) or not symbol.strip():
            return None
        grouped.setdefault(symbol, []).append(item)
    notes = []
    for symbol, rows in grouped.items():
        try:
            if len(rows) != 1:
                raise ValueError('ambiguous duplicate receipts')
            notes.append(_receipt_note(rows[0], generated_at))
            continue
        except ValidationError:
            reason = 'receipt failed research note validation'
        except (ValueError, TypeError) as error:
            reason = str(error)[:200] or 'unverified receipt'
        try:
            notes.append(_unknown_note(symbol, generated_at, reason))
        except ValidationError:
            return None
    return notes
