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
    def test_invalid_receipt_isolated_without_hiding_verified_symbols(self):
        # Classification fixtures only; no real scans or market data are created.
        now = datetime.now(timezone.utc)
        valid = dict(market='EGX', status='READY_NO_SIGNAL', live='DISABLED',
                     mode='SHADOW', pit_audit_id='pit',
                     decision_at=(now - timedelta(hours=1)).isoformat(),
                     valid_until=(now + timedelta(hours=1)).isoformat())
        invalid = ['invalid json', '[]', '{}',
                   json.dumps(valid | {'symbol': 'BAD', 'pit_audit_id': {}}),
                   json.dumps(valid | {'symbol': 'OTHER'}),
                   json.dumps(valid | {'symbol': 'BAD', 'decision_at': 'invalid'})]
        for bad_payload in invalid:
            with self.subTest(payload=bad_payload), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'platform.db'
                with sqlite3.connect(path) as db:
                    db.executescript('CREATE TABLE daily_canonical_artifacts(canonical_symbol TEXT); CREATE TABLE audit_events(event_id TEXT, event_type TEXT, entity_id TEXT, created_at TEXT, payload_json TEXT);')
                    db.execute('INSERT INTO audit_events VALUES (?, ?, ?, ?, ?)',
                               ('pit', 'PIT_DATA_VALIDATED', '', '', '{}'))
                    for symbol, payload in [('GOOD', json.dumps(valid | {'symbol': 'GOOD'})),
                                            ('BAD', bad_payload)]:
                        db.execute('INSERT INTO daily_canonical_artifacts VALUES (?)', (symbol,))
                        db.execute('INSERT INTO audit_events VALUES (?, ?, ?, ?, ?)',
                                   (sha256(payload.encode()).hexdigest(), 'PAPER_SIGNAL_VERIFIED',
                                    symbol, now.isoformat(), payload))
                before = path.read_bytes()
                with patch.dict('os.environ', {'EGX_PAPER_RUNTIME': directory}, clear=True):
                    state = load_system_state()
                egx = state['markets']['EGX']
                self.assertEqual(egx['status'], 'PARTIAL')
                self.assertEqual((egx['scanned'], egx['ready_no_signal'], egx['evidence_blocked']),
                                 (1, 1, 1))
                self.assertEqual(len(egx['receipts']), 2)
                self.assertEqual(egx['receipts'][0]['reason'], 'invalid verification receipt')
                self.assertEqual(before, path.read_bytes())
                # With no valid neighbor, preserve the blocked symbol and report
                # zero verified scans, not unknown database availability.
                with sqlite3.connect(path) as db:
                    db.execute("DELETE FROM daily_canonical_artifacts WHERE canonical_symbol='GOOD'")
                with patch.dict('os.environ', {'EGX_PAPER_RUNTIME': directory}, clear=True):
                    state = load_system_state()
                    from app.ui.operational import load_operational_state, render_operational
                    operational = load_operational_state()
                self.assertEqual(state['markets']['EGX']['status'], 'EVIDENCE_BLOCKED')
                self.assertEqual(state['markets']['EGX']['scanned'], 0)
                self.assertEqual(state['markets']['EGX']['evidence_blocked'], 1)
                self.assertIsNone(operational['symbols'][0]['trade_plan'])
                self.assertIn('invalid verification receipt', render_operational(operational))

    def test_packaged_checkpoint_and_build_revision_remain_separate(self):
        # Mirror the Docker image layout using only explicitly copied files.
        import shutil
        import subprocess
        import sys

        root = Path(__file__).resolve().parents[1]
        dockerfile = (root / 'Dockerfile').read_text()
        self.assertIn('COPY PROGRESS.json ./PROGRESS.json', dockerfile)
        self.assertIn('ENV EGX_BUILD_REVISION=${EGX_BUILD_REVISION}', dockerfile)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            shutil.copytree(root / 'app', target / 'app',
                            ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copy(root / 'PROGRESS.json', target / 'PROGRESS.json')
            result = subprocess.run(
                [sys.executable, '-c',
                 'import json; from app.ui.system import load_system_state; '
                 'print(json.dumps(load_system_state()))'],
                cwd=target, env={'EGX_BUILD_REVISION': 'fixture-build'},
                capture_output=True, text=True, check=True)
            state = json.loads(result.stdout)
            self.assertEqual(state['checkpoint_status'], 'AVAILABLE')
            self.assertEqual(state['build']['revision'], 'fixture-build')
            self.assertEqual(state['checkpoint']['head'],
                             json.loads((root / 'PROGRESS.json').read_text())['head'])
            self.assertIsNone(state['markets']['EGX']['scanned'])

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
            self.assertEqual(state['providers']['receipt_observation'], 'UNAVAILABLE')
            self.assertIsNone(state['providers']['unattributed_symbols'])
            self.assertEqual(state['providers']['sources'], [])

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
            providers = state['providers']
            self.assertEqual(providers['status'], 'UNKNOWN')
            self.assertEqual(providers['receipt_observation'], 'AVAILABLE')
            self.assertEqual(providers['unattributed_symbols'], 1)
            self.assertEqual(providers['sources'], [{
                'market': 'EGX', 'provider': '<script>fixture</script>',
                'health': 'UNKNOWN', 'current_verified_symbols': 1,
                'stale_receipt_symbols': 1, 'other_symbols': 0,
            }])
            html = render_system(state)
            self.assertNotIn('<script>', html)
            self.assertIn('&lt;script&gt;', html)
            self.assertIn('LIVE MONEY DISABLED', html)

    def test_provider_sources_remain_separate_and_missing_identity_is_explicit(self):
        from app.ui.system import provider_receipt_summary

        symbols = [dict(provider='second', status='WATCH'),
                   dict(provider='first', status='READY_NO_SIGNAL'),
                   dict(provider='first', status='DATA_STALE'),
                   dict(provider='second', status='EVIDENCE_BLOCKED')]
        symbols.extend(dict(provider=value, status='NOT_READY')
                       for value in (None, '', ' ', {}, []))
        result = provider_receipt_summary({'available': True, 'symbols': symbols})
        self.assertEqual(result['unattributed_symbols'], 5)
        first, second = result['sources']
        self.assertEqual((first['provider'], second['provider']), ('first', 'second'))
        self.assertEqual((first['current_verified_symbols'], first['stale_receipt_symbols']), (1, 1))
        self.assertEqual((second['current_verified_symbols'], second['other_symbols']), (1, 1))
        self.assertTrue(all(source['health'] == 'UNKNOWN' for source in result['sources']))
        empty = provider_receipt_summary({'available': True, 'symbols': []})
        self.assertEqual(empty['unattributed_symbols'], 0)
        self.assertEqual(empty['receipt_observation'], 'AVAILABLE')
        self.assertEqual(empty['status'], 'UNKNOWN')

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
