"""Offline execution-window regressions; fixtures are not scheduler runtime evidence."""
import ast
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).parents[1]


def load_execution():
    # Execute the actual enum and adapter while excluding optional runtime imports.
    # No scheduler/database/provider implementation is substituted into source.
    schedule = ast.parse((ROOT / 'app/core/schedule.py').read_text())
    enum_node = next(node for node in schedule.body
                     if isinstance(node, ast.ClassDef) and node.name == 'CheckpointName')
    namespace = {'StrEnum': StrEnum}
    exec(compile(ast.Module(body=[enum_node], type_ignores=[]),
                 'app/core/schedule.py', 'exec'), namespace)
    job = ModuleType('offline_execution_job')
    sys.modules[job.__name__] = job
    exec(compile((ROOT / 'app/data/daily_refresh_job.py').read_text(),
                 'app/data/daily_refresh_job.py', 'exec'), job.__dict__)
    path = ROOT / 'app/core/daily_refresh_execution.py'
    tree = ast.parse(path.read_text())
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and node.module in (
            'app.core.schedule', 'app.data.daily_refresh_job'))]
    module = ModuleType('offline_refresh_execution')
    module.CheckpointName = namespace['CheckpointName']
    module.DailyRefreshJobError = job.DailyRefreshJobError
    sys.modules[module.__name__] = module
    exec(compile(tree, str(path), 'exec'), module.__dict__)
    return module


execution = load_execution()


class ExecutionWindowContractTests(unittest.TestCase):
    def adapter(self, days=400):
        self.repo, self.job = Mock(), Mock()
        self.repo.claim_job.return_value = True
        self.repo.mark_succeeded.return_value = True
        self.repo.mark_failed.return_value = True
        self.repo.successful_checkpoints.return_value = set()
        self.job.run.return_value = SimpleNamespace(items=(object(),))
        return execution.DailyRefreshExecutionAdapter(
            scheduler_repository=self.repo, refresh_job=self.job, lookback_days=days)

    def execute(self, adapter, day, checkpoint):
        return adapter.execute(market_date=day, checkpoint_name=checkpoint, provider=object())

    def test_invalid_lookbacks_rejected_at_construction(self):
        for days in (True, False, 0, -1, 1.5, 400.0, float('nan'), float('inf'), '400', None):
            with self.subTest(days=days), self.assertRaisesRegex(ValueError, 'positive integer'):
                self.adapter(days)
            self.assertEqual(self.repo.mock_calls, [])
            self.assertEqual(self.job.mock_calls, [])

    def test_invalid_market_dates_never_touch_ledger(self):
        for checkpoint in execution._ALLOWED_CHECKPOINTS:
            for day in ('2026-09-27', datetime(2026, 9, 27),
                        datetime(2026, 9, 27, tzinfo=timezone.utc), True, None):
                adapter = self.adapter()
                with self.subTest(day=day, checkpoint=checkpoint):
                    with self.assertRaisesRegex(ValueError, 'calendar date'):
                        self.execute(adapter, day, checkpoint)
                    self.assertEqual(self.repo.mock_calls, [])
                    self.job.run.assert_not_called()

    def test_overflow_never_claims_or_reads_fallback_ledger(self):
        for checkpoint in execution._ALLOWED_CHECKPOINTS:
            for days, day in ((400, date.min), (10**30, date(2026, 9, 27))):
                adapter = self.adapter(days)
                with self.subTest(days=days, checkpoint=checkpoint):
                    with self.assertRaisesRegex(ValueError, 'date range'):
                        self.execute(adapter, day, checkpoint)
                    self.assertEqual(self.repo.mock_calls, [])
                    self.job.run.assert_not_called()

    def test_valid_primary_and_fallback_keep_exact_windows(self):
        day = date(2026, 9, 27)
        for checkpoint in execution._ALLOWED_CHECKPOINTS:
            adapter = self.adapter()
            result = self.execute(adapter, day, checkpoint)
            self.assertTrue(result.succeeded)
            self.assertEqual(result.item_count, 1)
            self.repo.claim_job.assert_called_once_with(market_date=day, checkpoint_name=checkpoint)
            self.repo.mark_succeeded.assert_called_once()
            self.repo.mark_failed.assert_not_called()
            args = self.job.run.call_args.kwargs
            self.assertEqual(args['start_date'], day - timedelta(days=400))
            self.assertEqual(args['end_date'], day)
            self.assertEqual(args['snapshot_date'], day)

    def test_provider_failure_still_finalizes_claim_as_failed(self):
        adapter = self.adapter()
        self.job.run.side_effect = RuntimeError('fixture failure')
        with self.assertRaises(RuntimeError):
            self.execute(adapter, date(2026, 9, 27),
                         execution.CheckpointName.AFTER_SESSION_PRIMARY)
        self.repo.mark_failed.assert_called_once()
        self.repo.mark_succeeded.assert_not_called()


if __name__ == '__main__':
    unittest.main()
