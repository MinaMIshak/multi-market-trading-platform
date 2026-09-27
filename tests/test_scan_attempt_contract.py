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

    def running_history(self):
        """Preserve the real pre-completion snapshot to model a process crash."""
        import json
        from app.egx_scan_history import write_scan_history
        snapshots = []
        def capture(*args, **kwargs):
            write_scan_history(*args, **kwargs)
            snapshots.append(self.path.read_bytes())
        with patch('app.egx_scan_dispatch.write_scan_history', side_effect=capture):
            self.dispatch()
        self.path.write_bytes(snapshots[0])
        return json.loads(snapshots[0])

    def test_reconciles_crash_gap_without_mutating_storage_or_coverage(self):
        raw = self.running_history()
        ledger = self.root / 'ledger.sqlite'
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        with patch.dict('os.environ', {'EGX_SCAN_LEDGER_PATH': str(ledger)}):
            history = load_scan_history()
            state = self.state()
        self.assertEqual(history['run'], raw)
        result = state['scan_runs']['EGX']['scheduler_completion']
        self.assertEqual(result['status'], 'SUCCEEDED')
        self.assertEqual(result['started_at'], raw['scheduler_attempt']['started_at'])
        self.assertEqual(state['scan_runs']['EGX']['run']['scheduler_attempt']['status'], 'RUNNING')
        self.assertIsNone(state['coverage']['EGX']['scanned'])
        self.assertIsNone(state['scan_runs']['US']['scheduler_completion'])
        self.assertIn('Recorded SUCCEEDED', render_product(state))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_reconciliation_rejects_new_attempt_and_invalid_ledger_truth(self):
        self.running_history()
        ledger = self.root / 'ledger.sqlite'
        original = self.repo.get_job(**self.key)
        for change in ({'attempt_count': original['attempt_count'] + 1},
                       {'started_at': '2026-09-24T10:00:00+00:00'},
                       {'status': 'RUNNING'}, {'status': 'FAILED'},
                       {'finished_at': None}, {'finished_at': '2999-01-01T00:00:00+00:00'},
                       {'finished_at': original['started_at']},
                       {'calendar_truth': 'UNKNOWN'}, {'market_date': '2026-09-23'},
                       {'checkpoint_name': 'OTHER'}):
            with self.subTest(change=change):
                with self.db.connect() as conn:
                    for key, value in change.items():
                        conn.execute(f'UPDATE scheduled_jobs SET {key}=? WHERE job_id=?',
                                     (value, original['job_id']))
                with patch.dict('os.environ', {'EGX_SCAN_LEDGER_PATH': str(ledger)}):
                    self.assertIsNone(self.state()['scan_runs']['EGX']['scheduler_completion'])
                with self.db.connect() as conn:
                    for key in change:
                        conn.execute(f'UPDATE scheduled_jobs SET {key}=? WHERE job_id=?',
                                     (original[key], original['job_id']))

    def test_unavailable_ledger_preserves_history_and_does_not_create_database(self):
        self.running_history()
        broken = self.root / 'broken.sqlite'
        broken.write_text('not sqlite')
        empty = self.root / 'empty.sqlite'
        sqlite3.connect(empty).close()
        link = self.root / 'link.sqlite'
        link.symlink_to(self.root / 'ledger.sqlite')
        missing = self.root / 'missing.sqlite'
        for path in ('', 'relative.sqlite', missing, broken, empty, link, self.root):
            with self.subTest(path=path), patch.dict('os.environ', {'EGX_SCAN_LEDGER_PATH': str(path)}):
                state = self.state()['scan_runs']['EGX']
                self.assertEqual(state['status'], 'HISTORICAL_RUN')
                self.assertIsNone(state['scheduler_completion'])
        self.assertFalse(missing.exists())

    def test_product_rejects_unbound_injected_reconciliation(self):
        self.running_history()
        with patch.dict('os.environ', {'EGX_SCAN_LEDGER_PATH': str(self.root / 'ledger.sqlite')}):
            history = load_scan_history()
        for change in ({'attempt_count': 99}, {'checkpoint': 'EGX_SCAN_FALLBACK'},
                       {'market_date': '2026-09-23'}, {'status': 'RUNNING'}):
            value = deepcopy(history)
            value['reconciled_scheduler_completion'].update(change)
            state = product_state(dict(configured=False, available=False, status='UNKNOWN', symbols=[]),
                                  scan_history=value)
            self.assertIsNone(state['scan_runs']['EGX']['scheduler_completion'])

    def test_locked_and_ambiguous_ledgers_fail_closed(self):
        self.running_history()
        ledger = self.root / 'ledger.sqlite'
        conn = sqlite3.connect(ledger)
        try:
            conn.execute('BEGIN EXCLUSIVE')
            with patch.dict('os.environ', {'EGX_SCAN_LEDGER_PATH': str(ledger)}):
                self.assertIsNone(self.state()['scan_runs']['EGX']['scheduler_completion'])
        finally:
            conn.rollback()
            conn.close()
        ambiguous = self.root / 'ambiguous.sqlite'
        row = self.repo.get_job(**self.key)
        with sqlite3.connect(ambiguous) as conn:
            conn.execute('''CREATE TABLE scheduled_jobs (market_date, checkpoint_name,
                attempt_count, started_at, status, finished_at, calendar_truth)''')
            values = tuple(row[key] for key in ('market_date', 'checkpoint_name',
                'attempt_count', 'started_at', 'status', 'finished_at', 'calendar_truth'))
            conn.executemany('INSERT INTO scheduled_jobs VALUES (?, ?, ?, ?, ?, ?, ?)',
                             [values, values])
        with patch.dict('os.environ', {'EGX_SCAN_LEDGER_PATH': str(ambiguous)}):
            self.assertIsNone(self.state()['scan_runs']['EGX']['scheduler_completion'])
