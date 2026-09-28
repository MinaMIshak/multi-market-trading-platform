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
    def test_aggregate_status_requires_current_verified_symbols(self):
        # Real SQLite reader, synthetic receipts: no market observations.
        cases = [([], 'NOT_READY', 0),
                 (['NOT_READY'], 'NOT_READY', 0),
                 (['DATA_STALE'], 'DATA_STALE', 0),
                 (['NOT_READY', 'DATA_STALE'], 'PARTIAL', 0),
                 (['WATCH', 'DATA_STALE'], 'PARTIAL', 1),
                 (['READY_NO_SIGNAL', 'NOT_READY'], 'PARTIAL', 1),
                 (['WATCH', 'READY_NO_SIGNAL'], 'OPERATIONAL', 2)]
        now = datetime.now(timezone.utc)
        for statuses, expected, verified in cases:
            with self.subTest(statuses=statuses), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'platform.db'
                with sqlite3.connect(path) as db:
                    db.executescript('CREATE TABLE daily_canonical_artifacts(canonical_symbol TEXT); CREATE TABLE audit_events(event_id TEXT, event_type TEXT, entity_id TEXT, created_at TEXT, payload_json TEXT);')
                    db.execute('INSERT INTO audit_events VALUES (?, ?, ?, ?, ?)',
                               ('pit', 'PIT_DATA_VALIDATED', '', '', '{}'))
                    for index, status in enumerate(statuses):
                        symbol = 'FIXTURE' + str(index)
                        db.execute('INSERT INTO daily_canonical_artifacts VALUES (?)', (symbol,))
                        if status == 'NOT_READY':
                            continue
                        expiry = now + timedelta(hours=-1 if status == 'DATA_STALE' else 1)
                        payload = json.dumps(dict(
                            symbol=symbol, market='EGX', live='DISABLED', mode='SHADOW',
                            status='READY_NO_SIGNAL' if status == 'DATA_STALE' else status,
                            pit_audit_id='pit', decision_at=(now - timedelta(days=1)).isoformat(),
                            valid_until=expiry.isoformat()))
                        db.execute('INSERT INTO audit_events VALUES (?, ?, ?, ?, ?)',
                                   (sha256(payload.encode()).hexdigest(), 'PAPER_SIGNAL_VERIFIED',
                                    symbol, now.isoformat(), payload))
                before = path.read_bytes()
                with patch.dict('os.environ', {'EGX_PAPER_RUNTIME': directory}, clear=True):
                    state = load_system_state()
                self.assertEqual(state['markets']['EGX']['status'], expected)
                self.assertIsNone(state['markets']['EGX']['scanned'])
                self.assertEqual(state['markets']['EGX']['observed_symbols'], len(statuses))
                counts = state['markets']['EGX']['status_counts']
                self.assertEqual(counts['WATCH'] + counts['READY_NO_SIGNAL'], verified)
                self.assertEqual(before, path.read_bytes())

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
        invalid_windows = [
            # An expired receipt is only stale if its original window was valid.
            {'valid_until': (now - timedelta(hours=2)).isoformat()},
            {'valid_until': valid['decision_at']},
            {'decision_at': (now - timedelta(hours=1)).replace(tzinfo=None).isoformat()},
            {'valid_until': (now + timedelta(hours=1)).replace(tzinfo=None).isoformat()},
            {'decision_at': (now + timedelta(minutes=30)).isoformat()},
        ]
        invalid.extend(json.dumps(valid | {'symbol': 'BAD'} | window)
                       for window in invalid_windows)
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
                                 (None, 1, 1))
                self.assertEqual(len(egx['receipts']), 2)
                self.assertEqual(egx['receipts'][0]['reason'], 'invalid verification receipt')
                self.assertEqual(before, path.read_bytes())
                # With no valid neighbor, preserve the blocked symbol and report
                # blocked receipt counts while scan coverage remains unknown.
                with sqlite3.connect(path) as db:
                    db.execute("DELETE FROM daily_canonical_artifacts WHERE canonical_symbol='GOOD'")
                with patch.dict('os.environ', {'EGX_PAPER_RUNTIME': directory}, clear=True):
                    state = load_system_state()
                    from app.ui.operational import load_operational_state, render_operational
                    operational = load_operational_state()
                self.assertEqual(state['markets']['EGX']['status'], 'EVIDENCE_BLOCKED')
                self.assertIsNone(state['markets']['EGX']['scanned'])
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
            self.assertEqual((egx['scanned'], egx['watch'], egx['data_stale'], egx['not_ready']), (None, 1, 1, 1))
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
                'verified_symbols_at_observation': 1,
                'observed_at': egx['observed_at'],
                'stale_receipt_symbols': 1, 'other_symbols': 0,
            }])
            html = render_system(state)
            self.assertNotIn('<script>', html)
            self.assertIn('&lt;script&gt;', html)
            self.assertIn('LIVE MONEY DISABLED', html)
            table = html.split('<section id="provider-receipts">')[1].split('</section>')[0]
            self.assertIn('<td>EGX</td><td>&lt;script&gt;fixture&lt;/script&gt;</td><td>1</td><td>1</td><td>0</td>', table)
            self.assertIn(egx['observed_at'], table)
            self.assertIn('Symbols without provider attribution: 1', table)
            # Checkpoint prose may name the compatibility alias; runtime UI must not.
            runtime_html = html.split('<section><h2>Project checkpoint')[0]
            self.assertNotIn('current_verified_symbols', runtime_html)
            self.assertNotIn('&quot;providers&quot;', runtime_html)
            self.assertEqual(before, path.read_bytes())

    def test_provider_html_distinguishes_unknown_empty_and_unattributed(self):
        from copy import deepcopy
        from app.ui.system import provider_receipt_summary, render_provider_receipts
        base = dict(available=True, observed_at='2026-01-01T00:00:00+00:00', symbols=[])
        cases = [
            (base, True, 0),
            (base | {'symbols': [dict(symbol='A', status='NOT_READY')]}, True, 1),
            (base | {'available': False}, False, None),
            (base | {'observed_at': None}, False, None),
            (base | {'observed_at': '2026-01-01T00:00:00'}, False, None),
            (base | {'observed_at': '9999-01-01T00:00:00+00:00'}, False, None),
            (base | {'symbols': [dict(symbol='A', provider='hidden', status='NOT_READY')] * 2}, False, None),
        ]
        for operational, available, unattributed in cases:
            with self.subTest(operational=operational):
                summary = provider_receipt_summary(operational)
                before = deepcopy(summary)
                html = render_provider_receipts(summary)
                self.assertIn('Provider health: UNKNOWN', html)
                self.assertIn('US provider observation: UNKNOWN', html)
                self.assertNotIn('<table>', html)
                if available:
                    self.assertIn('Attributed providers: 0 at reader observation time', html)
                    self.assertIn('Symbols without provider attribution: ' + str(unattributed), html)
                    self.assertIn(base['observed_at'], html)
                else:
                    self.assertIn('Receipt attribution: UNAVAILABLE', html)
                    self.assertNotIn('Attributed providers: 0', html)
                    self.assertNotIn('hidden', html)
                self.assertEqual(summary, before)

    def test_provider_html_uses_historical_counts_without_mutating_api_alias(self):
        from copy import deepcopy
        from app.ui.system import provider_receipt_summary, render_provider_receipts
        summary = provider_receipt_summary(dict(
            available=True, observed_at='2026-01-02T00:00:00+00:00', symbols=[dict(
                symbol='EXPIRED', provider='fixture', status='DATA_STALE',
                decision_at='2025-01-01T00:00:00+00:00', valid_until='2026-01-01T00:00:00+00:00')]))
        # Renderer must never select the legacy count alias.
        summary['sources'][0]['current_verified_symbols'] = 999
        before = deepcopy(summary)
        html = render_provider_receipts(summary)
        self.assertIn('<td>EGX</td><td>fixture</td><td>0</td><td>1</td><td>0</td>', html)
        self.assertIn('Verified at observation', html)
        self.assertIn('Stale at observation', html)
        self.assertNotIn('999', html)
        self.assertEqual(summary, before)

    def test_provider_sources_remain_separate_and_missing_identity_is_explicit(self):
        from app.ui.system import provider_receipt_summary

        symbols = [dict(provider='second', status='WATCH'),
                   dict(provider='first', status='READY_NO_SIGNAL'),
                   dict(provider='first', status='DATA_STALE'),
                   dict(provider='second', status='EVIDENCE_BLOCKED')]
        symbols.extend(dict(provider=value, status='NOT_READY')
                       for value in (None, '', ' ', {}, []))
        for index, item in enumerate(symbols):
            item['symbol'] = 'FIXTURE' + str(index)
            item['decision_at'] = '2025-01-01T00:00:00+00:00'
            item['valid_until'] = ('2025-01-02T00:00:00+00:00' if item['status'] == 'DATA_STALE'
                                   else '2027-01-01T00:00:00+00:00')
        result = provider_receipt_summary({'available': True, 'observed_at': '2026-01-01T00:00:00+00:00', 'symbols': symbols})
        self.assertEqual(result['unattributed_symbols'], 5)
        first, second = result['sources']
        self.assertEqual((first['provider'], second['provider']), ('first', 'second'))
        self.assertEqual((first['current_verified_symbols'], first['stale_receipt_symbols']), (1, 1))
        self.assertEqual((second['current_verified_symbols'], second['other_symbols']), (1, 1))
        self.assertTrue(all(source['health'] == 'UNKNOWN' for source in result['sources']))
        empty = provider_receipt_summary({'available': True, 'observed_at': '2026-01-01T00:00:00+00:00', 'symbols': []})
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

    def test_system_receipt_projection_matches_product_without_coverage_inference(self):
        from copy import deepcopy
        from app.ui.product import product_state
        base = dict(market='EGX', provider='fixture', status='WATCH')
        cases = [[],
                 [base | {'symbol': str(i), 'status': status} for i, status in enumerate(
                     ('WATCH', 'READY_NO_SIGNAL', 'NOT_READY', 'DATA_STALE', 'EVIDENCE_BLOCKED', 'unexpected'))],
                 [base | {'symbol': 'DUP'}, base | {'symbol': 'DUP', 'status': 'NOT_READY'}],
                 [base | {'symbol': 'DUP'}, base | {'symbol': 'DUP', 'provider': 'other'}],
                 [base | {'symbol': None}],
                 [base | {'symbol': 'US_ONLY', 'market': 'US'}]]
        for available in (True, False):
            for rows in cases:
                with self.subTest(available=available, rows=rows):
                    operational = dict(configured=True, available=available, status='PARTIAL', symbols=rows)
                    before = deepcopy(operational)
                    with patch('app.ui.system.load_operational_state', return_value=operational), \
                            patch('app.ui.system.load_scan_history', return_value=None), \
                            patch.dict('os.environ', {}, clear=True):
                        state = load_system_state()
                    expected = product_state(operational)['coverage']['EGX']
                    egx = state['markets']['EGX']
                    for key in ('observed_symbols', 'status_counts', 'scope'):
                        self.assertEqual(egx[key], expected[key])
                    for key in ('data_ready', 'eligible', 'scanned'):
                        self.assertIsNone(egx[key])
                    for status, count in (expected['status_counts'] or {}).items():
                        self.assertEqual(egx[status.lower()], count)
                    self.assertIsNone(state['markets']['US']['observed_symbols'])
                    if available and rows and rows[0].get('symbol') == 'DUP':
                        self.assertEqual(egx['evidence_blocked'], 1)
                        self.assertEqual(egx['watch'], 0)
                        self.assertEqual(state['providers']['sources'], [])
                        self.assertEqual(state['providers']['receipt_observation'], 'UNAVAILABLE')
                    self.assertEqual(before, operational)
                    self.assertIn('receipt classifications only', render_system(state))

    def test_market_tables_preserve_unknown_empty_and_market_separation(self):
        from copy import deepcopy
        from app.ui.system import render_market_receipts
        base = dict(configured=True, available=True, status='NOT_READY',
                    observed_at='2026-01-02T00:00:00+00:00', symbols=[])
        for source, expected in ((base, '0'), (base | {'available': False}, 'UNKNOWN'),
                                 (base | {'symbols': [dict(symbol=None, market='EGX')]}, 'UNKNOWN')):
            with self.subTest(source=source), patch('app.ui.system.load_operational_state', return_value=source), patch.dict('os.environ', {}, clear=True):
                state = load_system_state()
                before = deepcopy(state)
                egx = render_market_receipts('EGX', state['markets']['EGX'])
                us = render_market_receipts('US', state['markets']['US'])
                self.assertIn('Distinct observed symbols: ' + expected, egx)
                self.assertIn('Baseline universe target: 224', egx)
                self.assertIn('Authoritative universe: UNKNOWN', egx)
                self.assertIn('Distinct observed symbols: UNKNOWN', us)
                self.assertNotIn('<table>', us)
                self.assertNotIn('224', us)
                for html in (egx, us):
                    self.assertNotIn('<pre>', html)
                    self.assertIn('Data-ready / eligible / scanned / candidates: UNKNOWN', html)
                self.assertIn('Receipt details: 0 observed symbols' if expected == '0'
                              else 'Receipt counts and details: UNKNOWN', egx)
                self.assertEqual(state, before)

    def test_market_details_use_validated_windows_and_block_duplicate_identity(self):
        from copy import deepcopy
        from app.ui.system import render_market_receipts
        row = dict(symbol='<fixture>', market='EGX', provider='<provider>',
                   status='DATA_STALE', decision_at='2025-01-01T00:00:00+00:00',
                   valid_until='2026-01-01T00:00:00+00:00', reason='<reason>',
                   last_verified_session='<session>')
        base = dict(configured=True, available=True, status='DATA_STALE',
                    observed_at='2026-01-02T00:00:00+00:00', symbols=[row])
        cases = [(base, True, False), (base | {'observed_at': None}, False, False),
                 (base | {'observed_at': '2026-01-02'}, False, False),
                 (base | {'observed_at': '9999-01-02T00:00:00+00:00'}, False, False),
                 (base | {'symbols': [row, row | {'provider': 'ambiguous'}]}, False, True),
                 (base | {'symbols': [row | {'status': 'WATCH'}]}, False, False)]
        for source, window, duplicate in cases:
            with self.subTest(source=source), patch('app.ui.system.load_operational_state', return_value=source), patch.dict('os.environ', {}, clear=True):
                state = load_system_state()
                before = deepcopy(state)
                html = render_market_receipts('EGX', state['markets']['EGX'])
                self.assertEqual(row['decision_at'] in html, window)
                self.assertEqual(row['valid_until'] in html, window)
                self.assertIn('expiry exclusive', html)
                self.assertIn('Expired bounds do not imply current validity', html)
                self.assertNotIn('<fixture>', html)
                self.assertEqual(html.count('&lt;fixture&gt;'), 1)
                if duplicate:
                    self.assertIn('Ambiguous duplicate receipt identity', html)
                    self.assertNotIn('&lt;provider&gt;', html)
                    self.assertNotIn('ambiguous', html)
                else:
                    for value in ('provider', 'reason', 'session'):
                        self.assertIn('&lt;' + value + '&gt;', html)
                self.assertEqual(state, before)
