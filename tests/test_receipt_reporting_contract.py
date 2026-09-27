"""Artificial SQLite receipts exercise reporting, not operational market evidence."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.ui.operational import load_operational_state
from app.ui.product import product_state, render_product


class ReceiptReportingContracts(unittest.TestCase):
    def test_reader_statuses_reach_scoped_product_counts_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'platform.db'
            now = datetime.now(timezone.utc)
            with sqlite3.connect(path) as con:
                con.execute('CREATE TABLE daily_canonical_artifacts (canonical_symbol TEXT)')
                con.execute('CREATE TABLE audit_events (event_id TEXT, event_type TEXT, entity_id TEXT, payload_json TEXT, created_at TEXT)')
                con.execute("INSERT INTO audit_events VALUES ('pit', 'PIT_DATA_VALIDATED', 'fixture', '{}', '0')")
                for symbol in ('watch', 'ready', 'stale', 'invalid', 'future', 'missing'):
                    con.execute('INSERT INTO daily_canonical_artifacts VALUES (?)', (symbol,))
                    if symbol == 'missing':
                        continue
                    receipt = dict(symbol=symbol, market='EGX', pit_audit_id='pit',
                                   status='READY_NO_SIGNAL' if symbol == 'ready' else 'WATCH',
                                   live='DISABLED', mode='SHADOW', trade_plan=None,
                                   decision_at=(now - timedelta(days=2)).isoformat(),
                                   valid_until=(now + timedelta(days=1)).isoformat(),
                                   last_verified_session='fixture', entry_session='fixture',
                                   provider='fixture', history_start='fixture', bar_count=260)
                    if symbol == 'stale':
                        receipt['valid_until'] = (now - timedelta(days=1)).isoformat()
                    if symbol == 'future':
                        receipt['decision_at'] = (now + timedelta(hours=1)).isoformat()
                    payload = json.dumps(receipt)
                    event_id = sha256(payload.encode()).hexdigest() if symbol != 'invalid' else 'tampered'
                    con.execute('INSERT INTO audit_events VALUES (?, ?, ?, ?, ?)',
                                (event_id, 'PAPER_SIGNAL_VERIFIED', symbol, payload, now.isoformat()))
            before = path.read_bytes()
            with patch.dict('os.environ', {'EGX_PAPER_RUNTIME': directory}, clear=True):
                state = product_state(load_operational_state())
            counts = state['coverage']['EGX']
            self.assertEqual(counts['observed_symbols'], 6)
            self.assertEqual(counts['status_counts'], dict(WATCH=1, READY_NO_SIGNAL=1,
                             DATA_STALE=1, EVIDENCE_BLOCKED=2, NOT_READY=1, UNKNOWN=0))
            self.assertIsNone(counts['scanned'])
            self.assertIsNone(counts['candidates'])
            self.assertEqual(state['markets']['EGX']['status'], 'PARTIAL')
            self.assertIn('not scan coverage', render_product(state))
            self.assertEqual(before, path.read_bytes())

    def test_missing_database_is_unknown_not_empty_and_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict('os.environ', {'EGX_PAPER_RUNTIME': directory}, clear=True):
                state = product_state(load_operational_state())
            self.assertIsNone(state['coverage']['EGX']['status_counts'])
            self.assertIsNone(state['coverage']['EGX']['observed_symbols'])
            self.assertEqual(state['markets']['EGX']['status'], 'EVIDENCE_BLOCKED')
            self.assertEqual(list(Path(directory).iterdir()), [])
