"""Offline visibility fixtures, not market evidence."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.egx_scan import scan_egx_scope
from app.egx_scan_history import load_scan_history
from app.ui.system import load_system_state, render_system


class ScanHistoryTests(unittest.TestCase):
    def test_persisted_blocked_scope_visible_without_current_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'scan.json'
            scan_egx_scope(symbols=['A', 'B'], sources={}, database=None,
                           data_root=directory, scope_reference='fixture <scope>', history_path=path)
            with patch.dict('os.environ', {'EGX_SCAN_HISTORY_PATH': str(path)}, clear=True):
                state = load_system_state()
            history = state['egx_scan_history']
            self.assertEqual(history['status'], 'HISTORICAL_RUN')
            self.assertEqual(history['run']['requested'], 2)
            self.assertEqual(history['run']['scanned'], 0)
            self.assertIsNone(state['markets']['EGX']['scanned'])
            self.assertIn('fixture &lt;scope&gt;', render_system(state))
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_corrupt_inconsistent_and_future_history_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'scan.json'
            scan_egx_scope(symbols=['A'], sources={}, database=None,
                           data_root=directory, scope_reference='fixture', history_path=path)
            valid = json.loads(path.read_text())
            invalid = [None, [], {}, valid | {'scanned': 1},
                       valid | {'completed_at': '2999-01-01T00:00:00+00:00'},
                       valid | {'live_money': True}, valid | {'symbols': [None]}]
            with patch.dict('os.environ', {'EGX_SCAN_HISTORY_PATH': str(path)}, clear=True):
                for value in invalid:
                    path.write_text(json.dumps(value))
                    self.assertEqual(load_scan_history()['status'], 'UNKNOWN')
                path.unlink()
                self.assertEqual(load_scan_history()['status'], 'UNKNOWN')

    def test_failed_atomic_replace_preserves_previous_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'scan.json'
            path.write_text('previous')
            with patch('app.egx_scan_history.os.replace', side_effect=OSError('fixture')):
                with self.assertRaises(OSError):
                    scan_egx_scope(symbols=['A'], sources={}, database=None,
                                   data_root=directory, scope_reference='fixture', history_path=path)
            self.assertEqual(path.read_text(), 'previous')
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_noninteger_counts_and_untrimmed_identity_are_not_displayed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'scan.json'
            scan_egx_scope(symbols=['A'], sources={}, database=None,
                           data_root=directory, scope_reference='fixture', history_path=path)
            valid = json.loads(path.read_text())
            invalid = [valid | {'schema_version': value} for value in (True, 1.0)]
            for status, count in valid['status_counts'].items():
                for value in (bool(count), float(count)):
                    invalid.append(valid | {'status_counts': valid['status_counts'] | {status: value}})
            invalid.extend(valid | {'symbols': [valid['symbols'][0] | {'symbol': symbol}]}
                           for symbol in (' A ', 'comi', 'A/B', 'A\n', 'Ａ', 'A' * 65))
            with patch.dict('os.environ', {'EGX_SCAN_HISTORY_PATH': str(path)}, clear=True):
                self.assertEqual(load_scan_history()['status'], 'HISTORICAL_RUN')
                for value in invalid:
                    with self.subTest(value=value):
                        path.write_text(json.dumps(value))
                        state = load_system_state()
                        self.assertEqual(state['egx_scan_history']['status'], 'UNKNOWN')
                        self.assertIsNone(state['markets']['EGX']['scanned'])
