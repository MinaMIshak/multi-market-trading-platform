"""Offline historical reporting fixtures; never runtime evidence."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.egx_scan import scan_egx_scope
from app.egx_scan_history import load_scan_history
from app.ui.product import product_state, render_product


class ProductScanHistoryContracts(unittest.TestCase):
    operational = dict(configured=False, available=False, status='UNKNOWN', symbols=[])

    def test_persisted_run_reaches_product_without_current_coverage_or_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'history.json'
            scan_egx_scope(symbols=['A', 'B'], sources={}, database=None,
                           data_root=directory, scope_reference='<fixture>', history_path=path)
            before = path.read_bytes()
            with patch.dict('os.environ', {'EGX_SCAN_HISTORY_PATH': str(path)}):
                history = load_scan_history()
            original = deepcopy(history)
            state = product_state(self.operational, section='LIVE', scan_history=history)
            run = state['scan_runs']['EGX']
            self.assertEqual(run['run']['requested'], 2)
            self.assertEqual(run['run']['scanned'], 0)
            self.assertEqual(len(run['run']['symbols']), 2)
            self.assertIsNone(run['scheduler_completion'])
            self.assertIsNone(state['scan_runs']['US']['run'])
            self.assertIsNone(state['coverage']['EGX']['scanned'])
            self.assertIsNone(state['coverage']['EGX']['candidates'])
            body = render_product(state)
            self.assertIn('&lt;fixture&gt;', body)
            self.assertIn('Historical per-target outcomes', body)
            self.assertNotIn('<fixture>', body)
            self.assertEqual(path.read_bytes(), before)
            run['run']['symbols'][0]['status'] = 'WATCH'
            run['run']['status_counts']['WATCH'] = 9
            self.assertEqual(history, original)
            us = product_state(self.operational, market='US', section='LIVE', scan_history=history)
            self.assertNotIn('EGX', us['scan_runs'])
            self.assertNotIn('fixture', render_product(us))

    def test_invalid_history_is_unknown_without_rows(self):
        for history in (None, {}, [], {'status': 'HISTORICAL_RUN', 'run': None},
                        {'status': 'HISTORICAL_RUN', 'run': {'scanned': 100}}):
            with self.subTest(history=history):
                state = product_state(self.operational, section='LIVE', scan_history=history)
                self.assertIsNone(state['scan_runs']['EGX']['run'])
                self.assertNotIn('Historical per-target outcomes', render_product(state))

    def test_reader_rejects_duplicate_oversized_nested_and_symlink_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'history.json'
            with patch.dict('os.environ', {'EGX_SCAN_HISTORY_PATH': str(path)}):
                for payload in ('{"market":"US","market":"EGX"}', ' ' * 1_048_577,
                                '[' * 2000, '\xff'):
                    path.write_text(payload)
                    self.assertEqual(load_scan_history()['status'], 'UNKNOWN')
                path.unlink()
                target = Path(directory) / 'target.json'
                scan_egx_scope(symbols=['A'], sources={}, database=None,
                               data_root=directory, scope_reference='fixture', history_path=target)
                path.symlink_to(target)
                self.assertEqual(load_scan_history()['status'], 'UNKNOWN')

    def test_inconsistent_and_future_history_cannot_reach_product(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'history.json'
            scan_egx_scope(symbols=['A'], sources={}, database=None,
                           data_root=directory, scope_reference='fixture', history_path=path)
            raw = json.loads(path.read_text())
            for changed in ({'scanned': 1}, {'completed_at': '2999-01-01T00:00:00+00:00'},
                            {'market': 'US'}, {'live_money': True}, {'symbols': raw['symbols'] * 2}):
                state = product_state(self.operational, scan_history={
                    'status': 'HISTORICAL_RUN', 'run': raw | changed})
                self.assertIsNone(state['scan_runs']['EGX']['run'])
