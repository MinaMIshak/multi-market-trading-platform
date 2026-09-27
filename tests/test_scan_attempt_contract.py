"""Offline SQLite ledger/history fixtures; no runtime or market evidence.

Load the repository class without its dependency-backed package imports. SQL
and persistence methods are the actual implementation, not a mocked ledger.
"""
import ast
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from app.egx_scan_dispatch import dispatch_scan
from app.egx_scan_history import load_scan_history, _valid_summary
from app.ui.product import product_state, render_product
from test_egx_scan_dispatch import Checkpoint


def repository_class():
    source = ast.parse(Path('app/storage/scheduler_repository.py').read_text())
    source.body = [n for n in source.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
    namespace = dict(date=date, datetime=datetime, timedelta=timedelta, timezone=timezone,
                     CheckpointName=Checkpoint, ScheduleEvaluation=object,
                     SchedulerJobStatus=object, Database=object)
    exec(compile(source, '<scheduler repository offline>', 'exec'), namespace)
    return namespace['SchedulerRepository']


class AttemptContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'history.json'
        def connect():
            conn = sqlite3.connect(self.root / 'ledger.sqlite')
            conn.row_factory = sqlite3.Row
            return conn
        self.db = NS(connect=connect)
        # Use the production schema without importing dependency-backed packages.
        tree = ast.parse(Path('app/storage/database.py').read_text())
        schema = next(ast.literal_eval(n.value) for n in tree.body
                      if isinstance(n, ast.Assign) and any(
                          isinstance(t, ast.Name) and t.id == 'SCHEMA_SQL' for t in n.targets))
        with connect() as conn:
            conn.executescript(schema)
            for checkpoint in Checkpoint:
                conn.execute('''INSERT INTO scheduled_jobs
                    (market_date, checkpoint_name, status, scheduled_at, expires_at,
                     calendar_truth, first_seen_at, last_seen_at)
                    VALUES (?, ?, 'PENDING', ?, ?, 'VERIFIED_TRADING_DAY', ?, ?)''',
                    ('2026-09-24', checkpoint.value, *(['2026-09-24T10:00:00+00:00'] * 4)))
        self.repo = repository_class()(self.db)
        self.key = dict(market_date=date(2026, 9, 24), checkpoint_name=Checkpoint.PRIMARY)
        self.evaluation = NS(market_date=self.key['market_date'],
                             calendar_truth='VERIFIED_TRADING_DAY', due=[NS(name=Checkpoint.PRIMARY)])
        self.config = patch('app.egx_scan_dispatch.load_scan_configuration', return_value=NS(
            symbols=('A',), sources={}, source_errors={}, scope_reference='offline fixture')).start()
        patch.dict('os.environ', {'EGX_SCAN_HISTORY_PATH': str(self.path)}).start()
        self.addCleanup(patch.stopall)

    def dispatch(self):
        return dispatch_scan(evaluation=self.evaluation, repository=self.repo,
                             database=None, data_root=self.root, config_path=self.root / 'config',
                             history_path=self.path)

    def state(self):
        return product_state(dict(configured=False, available=False, status='UNKNOWN', symbols=[]),
                             section='LIVE', scan_history=load_scan_history())

    def test_success_is_dated_and_bound_without_current_coverage(self):
        self.assertTrue(self.dispatch()['succeeded'])
        state = self.state()
        result = state['scan_runs']['EGX']['scheduler_completion']
        row = self.repo.get_job(**self.key)
        self.assertEqual(result, dict(market_date='2026-09-24', checkpoint=Checkpoint.PRIMARY.value,
            attempt_count=row['attempt_count'], started_at=row['started_at'],
            status='SUCCEEDED', finished_at=row['finished_at']))
        self.assertIsNone(state['coverage']['EGX']['scanned'])
        self.assertIsNone(state['scan_runs']['US']['scheduler_completion'])
        self.assertIn('Recorded SUCCEEDED', render_product(state))
        self.assertIsNone(self.dispatch())
        self.evaluation.due = [NS(name=Checkpoint.FALLBACK)]
        self.assertIsNone(self.dispatch())

    def test_failed_initial_history_write_keeps_fallback_available(self):
        with patch('app.egx_scan_history.os.replace', side_effect=OSError('private')):
            self.assertFalse(self.dispatch()['succeeded'])
        self.assertEqual(self.repo.get_job(**self.key)['status'], 'FAILED')
        self.assertIsNone(self.dispatch())
        self.evaluation.due = [NS(name=Checkpoint.FALLBACK)]
        self.assertTrue(self.dispatch()['succeeded'])
        self.assertEqual(self.state()['scan_runs']['EGX']['scheduler_completion']['checkpoint'],
                         Checkpoint.FALLBACK.value)

    def test_final_write_failure_does_not_reverse_success(self):
        import os
        replace = os.replace
        calls = []
        def fail_second(*args):
            calls.append(args)
            if len(calls) == 2:
                raise OSError('fixture')
            return replace(*args)
        with patch('app.egx_scan_history.os.replace', side_effect=fail_second):
            with self.assertRaises(OSError):
                self.dispatch()
        self.assertEqual(self.repo.get_job(**self.key)['status'], 'SUCCEEDED')
        self.assertIsNone(self.state()['scan_runs']['EGX']['scheduler_completion'])
        self.evaluation.due = [NS(name=Checkpoint.FALLBACK)]
        self.assertIsNone(self.dispatch())

    def test_ledger_rejection_cannot_publish_completion(self):
        with patch.object(self.repo, 'mark_succeeded', return_value=False):
            with self.assertRaises(RuntimeError):
                self.dispatch()
        self.assertIsNone(self.state()['scan_runs']['EGX']['scheduler_completion'])
        self.assertEqual(load_scan_history()['run']['scheduler_attempt']['status'], 'RUNNING')

    def test_stale_attempt_cannot_complete_or_fail_reclaimed_job(self):
        self.assertTrue(self.repo.claim_job(**self.key))
        row = self.repo.get_job(**self.key)
        old = (row['attempt_count'], row['started_at'])
        self.assertTrue(self.repo.claim_job(**self.key, stale_after_seconds=-1))
        self.assertFalse(self.repo.mark_succeeded(**self.key, expected_attempt=old))
        self.assertFalse(self.repo.mark_failed(**self.key, expected_attempt=old, error='old'))
        row = self.repo.get_job(**self.key)
        self.assertEqual(row['status'], 'RUNNING')
        self.assertTrue(self.repo.mark_succeeded(**self.key,
                        expected_attempt=(row['attempt_count'], row['started_at'])))

    def test_invalid_identity_and_legacy_injected_completion_fail_closed(self):
        self.dispatch()
        raw = load_scan_history()['run']
        for changes in ({'market_date': 'invalid'}, {'checkpoint': 'US_SCAN'},
                        {'attempt_count': True}, {'attempt_count': 0}, {'status': 'FAILED'},
                        {'started_at': '2026-09-24T10:00:00'}, {'finished_at': None},
                        {'finished_at': '2999-01-01T00:00:00+00:00'},
                        {'finished_at': '2000-01-01T00:00:00+00:00'}, {'extra': 'unapproved'}):
            with self.subTest(changes=changes):
                value = deepcopy(raw)
                value['scheduler_attempt'].update(changes)
                self.assertFalse(_valid_summary(value))
        self.assertFalse(_valid_summary(raw | {'schema_version': 2}))

    def test_completion_reread_must_match_same_attempt(self):
        original = self.repo.get_job
        def changed(**kwargs):
            row = original(**kwargs)
            if row['status'] == 'SUCCEEDED':
                row['attempt_count'] += 1
            return row
        with patch.object(self.repo, 'get_job', side_effect=changed):
            with self.assertRaisesRegex(RuntimeError, 'identity mismatch'):
                self.dispatch()
        self.assertIsNone(self.state()['scan_runs']['EGX']['scheduler_completion'])

    def test_legacy_unguarded_repository_call_remains_compatible(self):
        self.repo.claim_job(**self.key)
        self.assertTrue(self.repo.mark_failed(**self.key, error='fixture'))
        self.repo.claim_job(**self.key)
        self.assertTrue(self.repo.mark_succeeded(**self.key))
