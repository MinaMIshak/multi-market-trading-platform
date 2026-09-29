"""Artificial clock fixtures verify liveness, not operational market evidence."""
from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.scheduler_heartbeat import load_heartbeat, write_heartbeat
from app.ui.system import load_system_state


class HeartbeatTests(unittest.TestCase):
    def test_compose_shares_heartbeat_between_worker_and_api_processes(self):
        root = Path(__file__).resolve().parents[1]
        compose = (root / 'docker-compose.yml').read_text()
        api, scheduler = compose.split('  scheduler:', 1)
        for service in (api, scheduler):
            self.assertIn('EGX_SCHEDULER_HEARTBEAT_PATH: /app/data/scheduler-heartbeat.json', service)
            self.assertIn('- ./data:/app/data', service)
            self.assertIn('EGX_SCAN_HISTORY_PATH: /app/data/egx-scan-history.json', service)
        self.assertIn('EGX_SCHEDULER_MODE: observe', scheduler)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'scheduler-heartbeat.json'
            env = {'EGX_SCHEDULER_HEARTBEAT_PATH': str(path)}
            subprocess.run(
                [sys.executable, '-c',
                 'from app.scheduler_heartbeat import write_heartbeat; '
                 'write_heartbeat(mode="observe", poll_seconds=30)'],
                cwd=root, env=env, check=True, capture_output=True)
            before = path.read_bytes()
            result = subprocess.run(
                [sys.executable, '-c',
                 'import json; from app.ui.system import load_system_state; '
                 'print(json.dumps(load_system_state()))'],
                cwd=root, env=env, check=True, capture_output=True, text=True)
            state = json.loads(result.stdout)
            self.assertEqual(state['scheduler']['status'], 'RECENT_POLL')
            self.assertEqual(state['scheduler']['mode'], 'observe')
            self.assertIsNone(state['markets']['EGX']['scanned'])
            self.assertIsNone(state['markets']['US']['scanned'])
            self.assertEqual(before, path.read_bytes())

    def test_completed_poll_expires_and_system_reads_without_writing(self):
        now = datetime(2026, 9, 26, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'heartbeat.json'
            with patch.dict('os.environ', {'EGX_SCHEDULER_HEARTBEAT_PATH': str(path)}, clear=True):
                self.assertEqual(load_heartbeat(now=now)['status'], 'UNKNOWN')
                for mode in ('observe', 'paper_refresh'):
                    write_heartbeat(mode=mode, poll_seconds=30, now=now)
                    before = path.read_bytes()
                    self.assertEqual(load_heartbeat(now=now)['status'], 'RECENT_POLL')
                    self.assertEqual(load_heartbeat(now=now)['mode'], mode)
                    self.assertEqual(load_heartbeat(now=now + timedelta(seconds=90))['status'], 'STALE')
                    with patch('app.ui.system.load_heartbeat', lambda: load_heartbeat(now=now)):
                        self.assertEqual(load_system_state()['scheduler']['status'], 'RECENT_POLL')
                    self.assertEqual(before, path.read_bytes())
                self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_corrupt_future_naive_and_invalid_interval_fail_closed(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'heartbeat.json'
            with patch.dict('os.environ', {'EGX_SCHEDULER_HEARTBEAT_PATH': str(path)}, clear=True):
                write_heartbeat(mode='observe', poll_seconds=30, now=now)
                valid = json.loads(path.read_text())
                damaged = ['[]', 'null', '{']
                for key, value in [('mode', 'live'), ('market', 'US'), ('schema_version', 2),
                                   ('schema_version', True), ('schema_version', 1.0),
                                   ('observed_at', (now + timedelta(seconds=1)).isoformat()),
                                   ('observed_at', now.replace(tzinfo=None).isoformat()),
                                   ('valid_until', now.isoformat())]:
                    damaged.append(json.dumps({**valid, key: value}))
                for payload in damaged:
                    path.write_text(payload)
                    self.assertEqual(load_heartbeat(now=now)['status'], 'UNKNOWN')

    def test_pathological_heartbeat_file_fails_closed_without_breaking_system(self):
        # Match load_scan_history: a damaged shared file must degrade to UNKNOWN,
        # never raise out of the /api/system state loader.
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'heartbeat.json'
            with patch.dict('os.environ', {'EGX_SCHEDULER_HEARTBEAT_PATH': str(path)}, clear=True):
                write_heartbeat(mode='observe', poll_seconds=30, now=now)
                valid = json.loads(path.read_text())
                oversized = json.dumps({**valid, 'padding': 'x' * 70_000})
                for payload in ('[' * 100_000 + ']' * 100_000, oversized):
                    with self.subTest(size=len(payload)):
                        path.write_text(payload)
                        self.assertEqual(load_heartbeat(now=now)['status'], 'UNKNOWN')
                        self.assertEqual(load_system_state()['scheduler']['status'], 'UNKNOWN')

    def test_invalid_writer_input_preserves_previous_heartbeat(self):
        now = datetime(2026, 9, 26, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'heartbeat.json'
            with patch.dict('os.environ', {'EGX_SCHEDULER_HEARTBEAT_PATH': str(path)}, clear=True):
                valid = dict(mode='observe', poll_seconds=30, now=now)
                write_heartbeat(**valid)
                before = path.read_bytes()
                invalid = [('mode', 'live'), ('mode', None),
                           ('now', now.replace(tzinfo=None)), ('now', '2026-09-26'),
                           ('now', False), ('now', 0)]
                invalid.extend(('poll_seconds', value) for value in
                               (True, False, 0, -1, 1.0, float('nan'), float('inf'), '30', None))
                for key, value in invalid:
                    with self.subTest(key=key, value=value):
                        with self.assertRaises(ValueError):
                            write_heartbeat(**{**valid, key: value})
                        self.assertEqual(before, path.read_bytes())
                        self.assertEqual(load_heartbeat(now=now)['status'], 'RECENT_POLL')
                        self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_unconfigured_disabled_and_relative_path_rejected(self):
        with patch.dict('os.environ', {}, clear=True):
            write_heartbeat(mode='observe', poll_seconds=30)
            self.assertEqual(load_heartbeat()['status'], 'UNKNOWN')
        with patch.dict('os.environ', {'EGX_SCHEDULER_HEARTBEAT_PATH': 'relative'}, clear=True):
            self.assertEqual(load_heartbeat()['status'], 'UNKNOWN')
            with self.assertRaises(ValueError):
                write_heartbeat(mode='observe', poll_seconds=30)
