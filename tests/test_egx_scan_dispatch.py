"""Dispatcher contract fixtures; no live scan or market evidence implied."""
from datetime import date
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
        self.repo.get_job.return_value = {'status': 'PENDING', 'attempt_count': 0}
        self.repo.claim_job.side_effect = self.claim
        self.repo.mark_succeeded.side_effect = self.succeed
        self.repo.successful_checkpoints.return_value = set()
        self.evaluation = NS(calendar_truth='VERIFIED_TRADING_DAY', market_date=date(2026, 9, 24),
                             due=[NS(name=Checkpoint.PRIMARY)])
        self.config = patch('app.egx_scan_dispatch.load_scan_configuration',
                            return_value=NS(symbols=('A',), sources={}, scope_reference='fixture',
                                            source_errors={'A': 'INVALID_LAUNCH_EVIDENCE'})).start()
        self.scan = patch('app.egx_scan_dispatch.scan_egx_scope',
                          return_value={'requested': 1, 'scanned': 0}).start()
        self.writer = patch('app.egx_scan_dispatch.write_scan_history').start()
        self.addCleanup(patch.stopall)

    def claim(self, **kwargs):
        self.repo.get_job.return_value = dict(status='RUNNING', attempt_count=1,
            started_at='2026-09-24T10:00:00+00:00', finished_at=None)
        return True

    def succeed(self, **kwargs):
        self.repo.get_job.return_value = self.repo.get_job.return_value | dict(
            status='SUCCEEDED', finished_at='2026-09-24T11:00:00+00:00')
        return True

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
        self.repo.get_job.return_value = {'status': 'PENDING', 'attempt_count': 0}
        self.repo.claim_job.side_effect = None
        self.repo.claim_job.return_value = False
        self.assertIsNone(self.run_dispatch())
        self.config.assert_not_called()

    def test_classification_completion_is_not_scan_success_count(self):
        result = self.run_dispatch()
        self.assertTrue(result['succeeded'])
        self.assertEqual(result['scanned'], 0)
        self.repo.mark_succeeded.assert_called_once()
        self.scan.assert_called_once()
        self.assertEqual(self.scan.call_args.kwargs['source_errors'],
                         {'A': 'INVALID_LAUNCH_EVIDENCE'})

    def test_failure_is_redacted_and_fallback_reloads(self):
        self.config.side_effect = ValueError('sensitive content')
        result = self.run_dispatch()
        self.assertEqual(result['error'], 'EGX_SCAN_FAILED:ValueError')
        self.repo.mark_succeeded.assert_not_called()
        self.repo.get_job.return_value = {'status': 'PENDING', 'attempt_count': 0}
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
                self.repo.get_job.return_value = {'status': 'PENDING', 'attempt_count': 0}
                self.scan.return_value = report
                result = self.run_dispatch()
                self.assertFalse(result['succeeded'])
                self.assertEqual(result['error'], 'EGX_SCAN_FAILED:ValueError')
                self.repo.mark_failed.assert_called_once()
                self.repo.mark_succeeded.assert_not_called()

    def test_ledger_write_failure_is_not_reported_as_success(self):
        self.repo.mark_succeeded.side_effect = None
        self.repo.mark_succeeded.return_value = False
        with self.assertRaises(RuntimeError):
            self.run_dispatch()
        self.repo.get_job.return_value = {'status': 'PENDING', 'attempt_count': 0}
        self.config.side_effect = OSError('private path')
        self.repo.mark_failed.return_value = False
        with self.assertRaises(RuntimeError):
            self.run_dispatch()


class SecurityMasterScopeDispatchTests(ScanDispatchTests):
    """Opt-in full security-master scope; explicit scope stays the default."""

    def setUp(self):
        super().setUp()
        self.master = patch('app.egx_scan_dispatch.SecurityMasterRepository').start()
        self.universe = patch('app.egx_scan_dispatch.universe_scan_configuration',
                              return_value=NS(symbols=('A', 'B'), sources={}, source_errors={},
                                              scope_reference='security-master-equity-universe')).start()

    def run_scope(self, scope, config_path='/tmp/config'):
        if scope == 'security_master':
            self.scan.return_value = {'requested': 2, 'scanned': 0}
        return dispatch_scan(evaluation=self.evaluation, repository=self.repo,
                             database='db', data_root='/tmp', config_path=config_path,
                             history_path='/tmp/history', scope=scope)

    def test_default_scope_is_explicit_configuration(self):
        self.assertTrue(self.run_dispatch()['succeeded'])
        self.universe.assert_not_called()
        self.master.assert_not_called()

    def test_security_master_scope_without_config_scans_universe_blocked(self):
        result = self.run_scope('security_master', config_path=None)
        self.assertTrue(result['succeeded'])
        self.assertEqual((result['requested'], result['scanned']), (2, 0))
        self.config.assert_not_called()
        self.master.assert_called_once_with('db')
        self.assertIsNone(self.universe.call_args.kwargs['launch'])
        self.assertEqual(self.universe.call_args.kwargs['scope_reference'],
                         'security-master-equity-universe')
        self.assertEqual(self.scan.call_args.kwargs['symbols'], ('A', 'B'))

    def test_security_master_scope_overlays_explicit_launch_evidence(self):
        self.run_scope('security_master')
        self.config.assert_called_once_with('/tmp/config')
        self.assertIs(self.universe.call_args.kwargs['launch'], self.config.return_value)

    def test_universe_failure_marks_job_failed_with_type_only(self):
        self.universe.side_effect = ValueError('security master returned no usable equity universe')
        result = self.run_scope('security_master', config_path=None)
        self.assertEqual(result, dict(checkpoint='EGX_SCAN_PRIMARY', succeeded=False,
                                      error='EGX_SCAN_FAILED:ValueError'))
        self.repo.mark_succeeded.assert_not_called()

    def test_unsupported_scope_fails_before_claim(self):
        for scope in ('all', '', None, 'SECURITY_MASTER'):
            with self.subTest(scope=scope):
                with self.assertRaises(ValueError):
                    self.run_scope(scope)
        self.repo.claim_job.assert_not_called()
        self.scan.assert_not_called()

    def test_explicit_scope_requires_config_path(self):
        with self.assertRaises(ValueError):
            self.run_scope('explicit', config_path=None)
        self.repo.claim_job.assert_not_called()
