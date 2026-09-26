"""Dispatcher contract fixtures; no live scan or market evidence implied."""
from enum import StrEnum
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
from app.egx_scan_dispatch import dispatch_scan


class Checkpoint(StrEnum):
    PRIMARY = 'EGX_SCAN_PRIMARY'
    FALLBACK = 'EGX_SCAN_FALLBACK'


class ScanDispatchTests(unittest.TestCase):
    def setUp(self):
        self.repo = Mock()
        self.repo.get_job.return_value = {'status': 'PENDING'}
        self.repo.claim_job.return_value = True
        self.repo.successful_checkpoints.return_value = set()
        self.evaluation = NS(calendar_truth='VERIFIED_TRADING_DAY', market_date='fixture',
                             due=[NS(name=Checkpoint.PRIMARY)])
        self.config = patch('app.egx_scan_dispatch.load_scan_configuration',
                            return_value=NS(symbols=('A',), sources={}, scope_reference='fixture')).start()
        self.scan = patch('app.egx_scan_dispatch.scan_egx_scope',
                          return_value={'requested': 1, 'scanned': 0}).start()
        self.addCleanup(patch.stopall)

    def run_dispatch(self):
        return dispatch_scan(evaluation=self.evaluation, repository=self.repo,
                             database=None, data_root='/tmp', config_path='/tmp/config',
                             history_path='/tmp/history')

    def test_calendar_gate_and_no_due_work(self):
        for truth in ('UNVERIFIED', 'VERIFIED_NON_TRADING_DAY'):
            self.evaluation.calendar_truth = truth
            self.assertIsNone(self.run_dispatch())
        self.evaluation.calendar_truth = 'VERIFIED_TRADING_DAY'
        self.evaluation.due = []
        self.assertIsNone(self.run_dispatch())
        self.repo.claim_job.assert_not_called()
        self.scan.assert_not_called()

    def test_no_poll_retry_or_duplicate_claim(self):
        for status in ('FAILED', 'RUNNING', 'SUCCEEDED', 'BLOCKED'):
            self.repo.get_job.return_value = {'status': status}
            self.assertIsNone(self.run_dispatch())
        self.repo.get_job.return_value = {'status': 'PENDING'}
        self.repo.claim_job.return_value = False
        self.assertIsNone(self.run_dispatch())
        self.config.assert_not_called()

    def test_classification_completion_is_not_scan_success_count(self):
        result = self.run_dispatch()
        self.assertTrue(result['succeeded'])
        self.assertEqual(result['scanned'], 0)
        self.repo.mark_succeeded.assert_called_once()
        self.scan.assert_called_once()

    def test_failure_is_redacted_and_fallback_reloads(self):
        self.config.side_effect = ValueError('sensitive content')
        result = self.run_dispatch()
        self.assertEqual(result['error'], 'EGX_SCAN_FAILED:ValueError')
        self.repo.mark_succeeded.assert_not_called()
        self.config.side_effect = None
        self.evaluation.due = [NS(name=Checkpoint.FALLBACK)]
        self.assertTrue(self.run_dispatch()['succeeded'])
        self.assertEqual(self.config.call_count, 2)

    def test_successful_primary_suppresses_fallback(self):
        self.evaluation.due = [NS(name=Checkpoint.FALLBACK)]
        self.repo.successful_checkpoints.return_value = {Checkpoint.PRIMARY}
        self.assertIsNone(self.run_dispatch())
        self.repo.claim_job.assert_not_called()

    def test_malformed_completion_fails_before_success_is_persisted(self):
        for report in (None, {}, {'requested': 1},
                       {'requested': True, 'scanned': 0},
                       {'requested': 1, 'scanned': False},
                       {'requested': 2, 'scanned': 1},
                       {'requested': 1, 'scanned': -1},
                       {'requested': 1, 'scanned': 2}):
            with self.subTest(report=report):
                self.repo.reset_mock()
                self.scan.return_value = report
                result = self.run_dispatch()
                self.assertFalse(result['succeeded'])
                self.assertEqual(result['error'], 'EGX_SCAN_FAILED:ValueError')
                self.repo.mark_failed.assert_called_once()
                self.repo.mark_succeeded.assert_not_called()

    def test_ledger_write_failure_is_not_reported_as_success(self):
        self.repo.mark_succeeded.return_value = False
        with self.assertRaises(RuntimeError):
            self.run_dispatch()
        self.config.side_effect = OSError('private path')
        self.repo.mark_failed.return_value = False
        with self.assertRaises(RuntimeError):
            self.run_dispatch()
