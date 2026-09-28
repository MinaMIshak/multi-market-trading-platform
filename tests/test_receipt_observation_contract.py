"""Offline artificial receipt observation contracts; no market evidence."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.ui.operational import load_operational_state
from app.ui.product import product_state, render_product
from app.ui.system import load_system_state


NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class ReceiptObservationContracts(unittest.TestCase):
    def source(self):
        return dict(configured=True, available=True, status='OPERATIONAL',
                    observed_at=NOW.isoformat(), symbols=[dict(
                        symbol='<fixture>', market='EGX', status='WATCH',
                        decision_at='2026-01-01T00:00:00+00:00',
                        valid_until='2026-01-03T00:00:00+00:00')])

    def evidence(self, source):
        return product_state(source)['coverage']['EGX']['observation_evidence']

    def test_invalid_or_unavailable_reader_time_is_unknown(self):
        for value in (None, '', '<script>', 12, '2026-01-02', '2999-01-01T00:00:00+00:00'):
            source = self.source()
            source['observed_at'] = value
            self.assertIsNone(self.evidence(source))
        source = self.source()
        source['available'] = False
        self.assertIsNone(self.evidence(source))
        source.pop('observed_at')
        self.assertIsNone(self.evidence(source))

    def test_windows_require_unambiguous_consistent_classification(self):
        cases = [({}, True), ({'status': 'DATA_STALE'}, False),
                 ({'valid_until': NOW.isoformat()}, False),
                 ({'valid_until': NOW.isoformat(), 'status': 'DATA_STALE'}, True),
                 ({'decision_at': '2026-01-01'}, False),
                 ({'decision_at': '2026-01-03T00:00:00+00:00'}, False),
                 ({'valid_until': '2025-01-01T00:00:00+00:00'}, False),
                 ({'valid_until': None}, False), ({'status': 'EVIDENCE_BLOCKED'}, False),
                 ({'status': 'NOT_READY'}, False)]
        for changes, valid in cases:
            with self.subTest(changes=changes):
                source = self.source()
                source['symbols'][0].update(changes)
                before = deepcopy(source)
                evidence = self.evidence(source)
                self.assertEqual(evidence['receipt_windows'][0]['verification_window'] is not None, valid)
                self.assertEqual(source, before)
        source = self.source()
        source['symbols'] *= 2
        self.assertIsNone(self.evidence(source)['receipt_windows'][0]['verification_window'])
        source['symbols'][0]['symbol'] = None
        self.assertIsNone(self.evidence(source))

    def test_shared_projection_empty_source_and_market_separation(self):
        source = self.source()
        before = deepcopy(source)
        with patch('app.ui.system.load_operational_state', return_value=source), patch.dict('os.environ', {}, clear=True):
            system = load_system_state()
        evidence = self.evidence(source)
        self.assertEqual(system['markets']['EGX']['observation_evidence'], evidence)
        self.assertEqual(system['markets']['EGX']['observed_at'], NOW.isoformat())
        self.assertIsNone(system['markets']['US']['observed_at'])
        state = product_state(source, section='LIVE')
        self.assertIsNone(state['coverage']['US']['observation_evidence'])
        self.assertIsNone(state['coverage']['EGX']['data_ready'])
        html = render_product(state)
        self.assertIn('expiry exclusive', html)
        self.assertIn('not source freshness', html)
        self.assertNotIn('<fixture>', html)
        self.assertEqual(source, before)
        source['symbols'] = []
        self.assertEqual(self.evidence(source)['receipt_windows'], [])

    def test_real_reader_expiry_boundary_and_storage_immutability(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'platform.db'
            with sqlite3.connect(path) as db:
                db.executescript('CREATE TABLE daily_canonical_artifacts(canonical_symbol TEXT); CREATE TABLE audit_events(event_id TEXT, event_type TEXT, entity_id TEXT, created_at TEXT, payload_json TEXT);')
                db.execute('INSERT INTO audit_events VALUES (?, ?, ?, ?, ?)', ('pit', 'PIT_DATA_VALIDATED', '', '', '{}'))
                for symbol, expiry in [('EXPIRED', NOW.isoformat()), ('FRESH', '2026-01-03T00:00:00+00:00')]:
                    row = self.source()['symbols'][0] | dict(symbol=symbol, valid_until=expiry, pit_audit_id='pit', live='DISABLED', mode='SHADOW')
                    payload = json.dumps(row)
                    db.execute('INSERT INTO daily_canonical_artifacts VALUES (?)', (symbol,))
                    db.execute('INSERT INTO audit_events VALUES (?, ?, ?, ?, ?)', (sha256(payload.encode()).hexdigest(), 'PAPER_SIGNAL_VERIFIED', symbol, NOW.isoformat(), payload))
            before = path.read_bytes()
            with patch.dict('os.environ', {'EGX_PAPER_RUNTIME': directory}, clear=True), patch('app.ui.operational.datetime', FixedClock):
                source = load_operational_state()
            self.assertEqual(source['observed_at'], NOW.isoformat())
            self.assertEqual([r['status'] for r in source['symbols']], ['DATA_STALE', 'WATCH'])
            self.assertTrue(all(r['verification_window'] for r in self.evidence(source)['receipt_windows']))
            self.assertEqual(before, path.read_bytes())
            with patch.dict('os.environ', {'EGX_PAPER_RUNTIME': directory + '/missing'}, clear=True):
                self.assertIsNone(load_operational_state()['observed_at'])
