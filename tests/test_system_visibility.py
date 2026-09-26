"""Offline fixtures test visibility only; never operational market evidence."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.ui.system import load_system_state, render_system


class SystemVisibilityTests(unittest.TestCase):
    def test_absent_runtime_does_not_reuse_checkpoint_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / 'progress.json'
            checkpoint.write_text(json.dumps({'head': 'historical', 'egx': {'scanned': 224}}))
            with patch('app.ui.system.CHECKPOINT', checkpoint), patch.dict('os.environ', {}, clear=True):
                state = load_system_state()
            self.assertIsNone(state['markets']['EGX']['scanned'])
            self.assertIsNone(state['markets']['US']['configured_universe'])
            self.assertIsNone(state['build']['revision'])
            self.assertEqual(state['checkpoint']['head'], 'historical')
            self.assertEqual(state['scheduler']['status'], 'UNKNOWN')

    def test_current_expired_and_unverified_symbols_have_distinct_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'platform.db'
            now = datetime.now(timezone.utc)
            with sqlite3.connect(path) as db:
                db.executescript('CREATE TABLE daily_canonical_artifacts(canonical_symbol TEXT); CREATE TABLE audit_events(event_id TEXT, event_type TEXT, entity_id TEXT, created_at TEXT, payload_json TEXT);')
                db.execute('INSERT INTO audit_events VALUES (?, ?, ?, ?, ?)', ('pit', 'PIT_DATA_VALIDATED', '', '', '{}'))
                for symbol, expiry in [('CURRENT', now + timedelta(hours=1)), ('EXPIRED', now - timedelta(hours=1)), ('UNVERIFIED', None)]:
                    db.execute('INSERT INTO daily_canonical_artifacts VALUES (?)', (symbol,))
                    if expiry is None:
                        continue
                    payload = json.dumps(dict(symbol=symbol, market='EGX', status='WATCH', live='DISABLED', mode='SHADOW', pit_audit_id='pit', decision_at=(now - timedelta(days=1)).isoformat(), valid_until=expiry.isoformat(), provider='<script>fixture</script>'))
                    db.execute('INSERT INTO audit_events VALUES (?, ?, ?, ?, ?)', (sha256(payload.encode()).hexdigest(), 'PAPER_SIGNAL_VERIFIED', symbol, now.isoformat(), payload))
            before = path.read_bytes()
            with patch.dict('os.environ', {'EGX_PAPER_RUNTIME': directory}, clear=True):
                state = load_system_state()
            egx = state['markets']['EGX']
            self.assertEqual((egx['scanned'], egx['watch'], egx['data_stale'], egx['not_ready']), (1, 1, 1, 1))
            self.assertEqual(egx['baseline_universe'], 224)
            self.assertIsNone(egx['authoritative_universe'])
            self.assertEqual(before, path.read_bytes())
            html = render_system(state)
            self.assertNotIn('<script>', html)
            self.assertIn('&lt;script&gt;', html)
            self.assertIn('LIVE MONEY DISABLED', html)

    def test_corrupt_checkpoint_and_runtime_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / 'progress.json'
            for value in ('invalid', '[]'):
                checkpoint.write_text(value)
                with patch('app.ui.system.CHECKPOINT', checkpoint), patch.dict('os.environ', {'EGX_PAPER_RUNTIME': directory}, clear=True):
                    state = load_system_state()
                self.assertEqual(state['checkpoint_status'], 'UNAVAILABLE')
                self.assertEqual(state['markets']['EGX']['status'], 'EVIDENCE_BLOCKED')
                self.assertIsNone(state['markets']['EGX']['scanned'])
                self.assertFalse((Path(directory) / 'platform.db').exists())
