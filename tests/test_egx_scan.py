"""Synthetic orchestration fixtures are not operational market evidence."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.egx_scan import ScanBlocked, scan_egx_scope


class EgxScanTests(unittest.TestCase):
    def run_scope(self, symbols, sources):
        return scan_egx_scope(symbols=symbols, sources=sources, database=None,
                              data_root='/unused', scope_reference='test-only selection')

    def test_every_symbol_classified_and_failure_does_not_abort_scope(self):
        sources = {s: SimpleNamespace(symbol=s) for s in ('A', 'B', 'D')}
        verified = {'operation': 'VERIFIED_SIGNAL_NOT_PUBLISHED', 'mode': 'SHADOW',
                    'live': 'DISABLED', 'market_data': 'FRESH', 'market': 'EGX'}
        with patch('app.egx_scan._verify', side_effect=[
                RuntimeError('private provider detail'),
                verified | {'signal_status': 'READY_NO_SIGNAL', 'symbol': 'B'},
                verified | {'signal_status': 'WATCH', 'symbol': 'D'}]) as verify:
            result = self.run_scope(('A', 'B', 'C', 'D'), sources)
        self.assertEqual(verify.call_count, 3)
        self.assertEqual(result['requested'], 4)
        self.assertEqual(result['scanned'], 2)
        self.assertEqual(result['status_counts'], {'WATCH': 1, 'READY_NO_SIGNAL': 1, 'EVIDENCE_BLOCKED': 2})
        self.assertNotIn('private provider detail', str(result))
        self.assertFalse(result['live_money'])

    def test_mismatched_identity_never_invokes_pipeline(self):
        with patch('app.egx_scan._verify') as verify:
            result = self.run_scope(('A',), {'A': SimpleNamespace(symbol='B')})
        verify.assert_not_called()
        self.assertEqual(result['scanned'], 0)

    def test_configuration_rejection_overrides_source_and_redacts_unknown_code(self):
        with patch('app.egx_scan._verify') as verify:
            result = scan_egx_scope(
                symbols=('A', 'B'), sources={'A': SimpleNamespace(symbol='A')},
                source_errors={'A': 'INVALID_LAUNCH_EVIDENCE', 'B': 'private content'},
                database=None, data_root='/unused', scope_reference='fixture')
        verify.assert_not_called()
        self.assertEqual(result['scanned'], 0)
        self.assertEqual(result['status_counts']['EVIDENCE_BLOCKED'], 2)
        self.assertNotIn('private content', str(result))

    def test_result_identity_must_match_requested_market_and_symbol(self):
        verified = {'operation': 'VERIFIED_SIGNAL_NOT_PUBLISHED', 'mode': 'SHADOW',
                    'live': 'DISABLED', 'market_data': 'FRESH', 'signal_status': 'WATCH'}
        for identity in ({}, {'symbol': 'B', 'market': 'EGX'},
                         {'symbol': 'A', 'market': 'US'},
                         {'symbol': ' A', 'market': 'EGX'}):
            with self.subTest(identity=identity), patch(
                    'app.egx_scan._verify', return_value=verified | identity):
                report = self.run_scope(('A',), {'A': SimpleNamespace(symbol='A')})
                self.assertEqual(report['scanned'], 0)
                self.assertEqual(report['status_counts']['EVIDENCE_BLOCKED'], 1)

    def test_reviewed_admission_reason_is_preserved(self):
        with patch('app.egx_scan._verify', side_effect=ScanBlocked('required calendar evidence unavailable')):
            result = self.run_scope(('A',), {'A': SimpleNamespace(symbol='A')})
        self.assertEqual(result['symbols'][0]['reason'], 'required calendar evidence unavailable')
        self.assertEqual(result['scanned'], 0)

    def test_incomplete_or_unsafe_result_is_not_a_scan(self):
        for result in ({}, {'signal_status': 'WATCH'}, {'operation': 'REFRESH_COMPLETED_SIGNAL_NOT_RUN'},
                       {'operation': 'VERIFIED_SIGNAL_NOT_PUBLISHED', 'signal_status': 'WATCH',
                        'mode': 'LIVE', 'live': 'ENABLED', 'market_data': 'FRESH'}):
            with self.subTest(result=result), patch('app.egx_scan._verify', return_value=result):
                report = self.run_scope(('A',), {'A': SimpleNamespace(symbol='A')})
                self.assertEqual(report['scanned'], 0)
                self.assertEqual(report['symbols'][0]['status'], 'EVIDENCE_BLOCKED')

    def test_invalid_scope_rejected_before_any_work(self):
        for scope in ((), ('A', 'A'), (' A',), (None,)):
            with self.subTest(scope=scope), patch('app.egx_scan._verify') as verify:
                with self.assertRaises(ValueError):
                    self.run_scope(scope, {})
                verify.assert_not_called()


if __name__ == '__main__':
    unittest.main()
