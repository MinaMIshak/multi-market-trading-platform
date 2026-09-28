"""Offline heartbeat presentation contracts, not runtime observations."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.scheduler_heartbeat import load_heartbeat, write_heartbeat
from app.ui.system import load_system_state, render_runtime_observation, render_system


class RuntimeObservationTests(unittest.TestCase):
    def test_real_reader_missing_recent_stale_and_invalid_evidence(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'heartbeat.json'
            with patch.dict('os.environ', {'EGX_SCHEDULER_HEARTBEAT_PATH': str(path)}, clear=True):
                write_heartbeat(mode='observe', poll_seconds=30, now=now)
                valid = json.loads(path.read_text())
                cases = [(None, now, 'UNKNOWN'), (valid, now, 'RECENT_POLL'),
                         (valid, now + timedelta(seconds=90), 'STALE')]
                for mode in ('paper_refresh',):
                    cases.append((valid | {'mode': mode}, now, 'RECENT_POLL'))
                for key, value in (
                    ('observed_at', '2026-01-01T00:00:00'),
                    ('observed_at', (now + timedelta(seconds=1)).isoformat()),
                    ('valid_until', now.isoformat()), ('mode', '<script>live</script>'),
                    ('market', 'US'), ('schema_version', True)):
                    cases.append((valid | {key: value}, now, 'UNKNOWN'))
                cases.extend((payload, now, 'UNKNOWN') for payload in ('{', '[]', 'null'))
                for payload, clock, expected in cases:
                    with self.subTest(payload=payload, clock=clock):
                        if payload is None:
                            path.unlink(missing_ok=True)
                        else:
                            path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
                        before = path.read_bytes() if path.exists() else None
                        with patch('app.ui.system.load_heartbeat', lambda: load_heartbeat(now=clock)):
                            state = load_system_state()
                        snapshot = deepcopy(state)
                        html = render_runtime_observation(state)
                        self.assertEqual(state['scheduler']['status'], expected)
                        self.assertIn('<td>' + expected + '</td>', html)
                        self.assertNotIn('<pre>', html)
                        self.assertIn('API response timestamp', html)
                        self.assertIn(state['observed_at'], html)
                        self.assertIn('Deployment-injected revision</th><td>UNKNOWN', html)
                        self.assertIn('US worker health: UNKNOWN', html)
                        self.assertIn('Heartbeats do not establish scan completion', html)
                        if expected == 'UNKNOWN':
                            self.assertIn('Worker poll observed at</th><td>UNKNOWN', html)
                            self.assertNotIn(valid['observed_at'], html)
                        else:
                            self.assertIn(valid['observed_at'], html)
                            self.assertIn(valid['valid_until'], html)
                            self.assertIn('exclusive', html)
                        if expected == 'RECENT_POLL':
                            self.assertIn('recent completed EGX poll', html)
                            self.assertNotIn('Worker health: UNKNOWN', html)
                        else:
                            self.assertIn('Worker health: UNKNOWN', html)
                        if expected == 'STALE':
                            self.assertIn('historical poll only', html)
                        self.assertIn(html, render_system(state))
                        self.assertEqual(state, snapshot)
                        self.assertEqual(before, path.read_bytes() if path.exists() else None)
                        self.assertIsNone(state['markets']['EGX']['scanned'])
                        self.assertIsNone(state['markets']['US']['scanned'])

    def test_escaped_metadata_and_approved_fields_only_preserve_api(self):
        with patch.dict('os.environ', {'EGX_BUILD_REVISION': '<script>revision</script>'}, clear=True):
            state = load_system_state()
        state['api']['status'] = '<response>'
        state['observed_at'] = '<timestamp>'
        state['scheduler']['reason'] = '<private-extra>'
        state['build']['extra'] = '<private-extra>'
        before = deepcopy(state)
        html = render_runtime_observation(state)
        for value in ('script', 'response', 'timestamp'):
            self.assertNotIn('<' + value + '>', html)
            self.assertIn('&lt;' + value + '&gt;', html)
        self.assertNotIn('private-extra', html)
        self.assertEqual(state, before)
        self.assertEqual(state['build']['revision'], '<script>revision</script>')
