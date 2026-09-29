"""Read-only operational verification receipts; never infer fills or performance."""
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
from html import escape
import json
import os
from pathlib import Path
import sqlite3

from app.path_safety import symlinked
from app.data.source_admission import daily_source_admission


def load_operational_state():
    configured = os.getenv('EGX_PAPER_RUNTIME')
    state = {'configured': bool(configured), 'available': False, 'symbols': [],
             'observed_at': None, 'status': 'NOT_READY', 'mode': 'PAPER/SHADOW ONLY', 'live': 'DISABLED'}
    if not configured:
        return state
    try:
        root = Path(configured)
        path = root / 'platform.db'
        if not root.is_absolute() or symlinked(path):
            raise ValueError('isolated absolute runtime required')
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as con:
            con.row_factory = sqlite3.Row
            con.execute('BEGIN')
            symbols = con.execute('SELECT DISTINCT canonical_symbol FROM daily_canonical_artifacts ORDER BY canonical_symbol').fetchall()
            # Classify all rows at one reader snapshot time.
            now = datetime.now(timezone.utc)
            for symbol_row in symbols:
                symbol = symbol_row[0]
                item = {'symbol': symbol, 'market': 'EGX', 'status': 'NOT_READY', 'trade_plan': None}
                row = con.execute("SELECT event_id, payload_json FROM audit_events WHERE event_type='PAPER_SIGNAL_VERIFIED' AND entity_id=? ORDER BY created_at DESC, event_id DESC LIMIT 1", (symbol,)).fetchone()
                if row:
                    try:
                        payload = row['payload_json']
                        receipt = json.loads(payload)
                        if (not isinstance(receipt, dict)
                                or not isinstance(receipt.get('pit_audit_id'), str)
                                or not receipt['pit_audit_id']):
                            raise ValueError('invalid verification receipt')
                        audit = con.execute("SELECT 1 FROM audit_events WHERE event_id=? AND event_type='PIT_DATA_VALIDATED'", (receipt['pit_audit_id'],)).fetchone()
                        if (sha256(payload.encode()).hexdigest() != row['event_id'] or not audit
                                or receipt['symbol'] != symbol or receipt['market'] != 'EGX'
                                or receipt['status'] not in ('WATCH', 'READY_NO_SIGNAL')
                                or receipt['live'] != 'DISABLED' or receipt['mode'] != 'SHADOW'):
                            raise ValueError('invalid verification receipt')
                        decision_at = datetime.fromisoformat(receipt['decision_at'])
                        valid_until = datetime.fromisoformat(receipt['valid_until'])
                        if (decision_at.utcoffset() is None or valid_until.utcoffset() is None
                                or valid_until <= decision_at):
                            raise ValueError('invalid verification window')
                        if decision_at > now:
                            raise ValueError('future verification receipt')
                        item = receipt
                        # Label, never rewrite: preserved receipts keep their recorded
                        # status; admission reflects the current source registry.
                        admission = daily_source_admission(receipt.get('provider'), 'EGX')
                        item = item | {'source_status': admission.status,
                                       'source_reason': admission.reason}
                        if now >= valid_until:
                            item = item | {'status': 'DATA_STALE', 'market_data': 'DATA_STALE', 'trade_plan': None}
                    except (ValueError, TypeError, KeyError):
                        item = {'symbol': symbol, 'market': 'EGX',
                                'status': 'EVIDENCE_BLOCKED', 'trade_plan': None,
                                'reason': 'invalid verification receipt'}
                state['symbols'].append(item)
        statuses = {item['status'] for item in state['symbols']}
        if not statuses:
            status = 'NOT_READY'
        elif statuses <= {'WATCH', 'READY_NO_SIGNAL'}:
            status = 'OPERATIONAL'
        elif len(statuses) == 1:
            status = next(iter(statuses))
        else:
            status = 'PARTIAL'
        return state | {'available': True, 'status': status, 'observed_at': now.isoformat()}
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return state | {'symbols': [], 'status': 'EVIDENCE_BLOCKED'}


def render_operational(state, *, fragment=False, heading='Operational Paper/Shadow'):
    def text(value):
        return escape(str(value))
    # Reader admission only requires status fields; a hash-consistent receipt may
    # still lack display fields. Show those as UNKNOWN rather than failing the page.
    def field(source, key):
        value = source.get(key)
        return 'UNKNOWN' if value is None or value == '' else text(value)
    content = '<h2>' + text(heading) + '</h2><p>LIVE MONEY DISABLED · Candidate != fill · No execution or performance inference</p>'
    content += '<p>' + text(state['status']) + '</p>'
    for item in state['symbols']:
        content += '<article><h3>' + text(item['symbol']) + ' · ' + text(item['market']) + ' · ' + text(item['status']) + '</h3>'
        if item.get('reason'):
            content += '<p>' + text(item['reason']) + '</p>'
        if 'decision_at' in item:
            content += '<p>Signal session: ' + field(item, 'last_verified_session') + ' · Expected entry session: ' + field(item, 'entry_session') + '</p>'
            content += ('<p>Receipt source: ' + field(item, 'provider') + ' · Source admission: '
                        + text(item.get('source_status') or 'EVIDENCE_BLOCKED') + ' ('
                        + text(item.get('source_reason') or 'source admission unavailable')
                        + ') · Fresh at verification: ' + field(item, 'decision_at') + '</p>')
            if item.get('source_status') != 'ADMITTED':
                content += ('<p>Preserved historical receipt. Its source is not admitted, so '
                            'it cannot seed new signals or candidates.</p>')
            content += '<p>Operational window: ' + field(item, 'history_start') + ' through ' + field(item, 'last_verified_session') + ' · ' + field(item, 'bar_count') + ' bars. Full immutable source history retained.</p>'
        plan = item.get('trade_plan')
        if item['status'] == 'WATCH' and plan is not None:
            levels = ('entry_low', 'entry_high', 'stop_price', 'target_1')
            if isinstance(plan, dict) and all(plan.get(key) not in (None, '') for key in levels):
                content += '<p>Unsized WATCH · Entry band: ' + text(plan['entry_low']) + '–' + text(plan['entry_high']) + ' · Stop: ' + text(plan['stop_price']) + ' · Target: ' + text(plan['target_1']) + '</p>'
            else:
                # Never present a partial plan; missing levels are not inferable.
                content += '<p>Unsized WATCH · Trade plan: UNKNOWN (incomplete receipt)</p>'
        content += '</article>'
    if fragment:
        return content
    return ('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Paper/Shadow operational dashboard</title><style>'
            'body{background:#071019;color:#e9f0f5;font:16px Arial,sans-serif;max-width:1100px;margin:32px auto;padding:20px}'
            'article{background:#0d1b26;border:1px solid #1e3241;border-radius:10px;padding:20px;margin:20px 0}'
            'a{color:#8bd5b0}p{line-height:1.6}</style></head><body>'
            '<nav><a href="/system">SYSTEM</a> · <a href="/shadow">Audited collection</a></nav>' + content + '</body></html>')
