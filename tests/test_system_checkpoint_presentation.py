"""Offline checkpoint presentation contracts; checkpoint text is reported history only."""
from copy import deepcopy
from html import escape
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from app.ui.system import (load_system_state, render_project_checkpoint,
                           render_runtime_observation, render_system)


APPROVED = ('milestone', 'phase', 'current_capability', 'head', 'tests', 'deployment',
            'scheduler', 'next_action', 'hard_blocker', 'capability_blockers')
LABELS = {
    'milestone': 'Reported milestone',
    'phase': 'Reported phase',
    'current_capability': 'Reported capability',
    'head': 'Checkpoint source HEAD at cycle start (not the deployed revision)',
    'tests': 'Reported tests',
    'deployment': 'Reported deployment note',
    'scheduler': 'Reported scheduler note',
    'next_action': 'Reported next action',
    'hard_blocker': 'Reported hard blocker',
}
MARKER = '<section><h2>Project checkpoint'
BOUNDARY = ('Reported history', 'not runtime verification', 'not deployment verification',
            'not scheduler heartbeat evidence', 'not the deployed revision')


def row(label, value):
    return '<th scope="row">' + label + '</th><td>' + value + '</td>'


def valid_checkpoint():
    raw = {key: key.upper() + '_VALUE' for key in LABELS}
    raw['hard_blocker'] = None
    raw['capability_blockers'] = {'deployment': 'DEPLOYMENT_BLOCKER', 'performance': 'PERFORMANCE_BLOCKER'}
    return raw


class CheckpointPresentationTests(unittest.TestCase):
    def observe(self, payload):
        """Load, render and verify read-only behavior; payload None means missing file."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'PROGRESS.json'
            if payload is not None:
                path.write_bytes(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
            before = path.read_bytes() if path.exists() else None
            with patch('app.ui.system.CHECKPOINT', path), patch.dict('os.environ', {}, clear=True):
                state = load_system_state()
            snapshot = deepcopy(state)
            fragment = render_project_checkpoint(state)
            page = render_system(state)
            self.assertEqual(state, snapshot)
            self.assertEqual(before, path.read_bytes() if path.exists() else None)
        self.assertTrue(fragment.startswith(MARKER))
        self.assertIn(fragment, page)
        self.assertEqual(page.count(MARKER), 1)
        self.assertNotIn('<pre>', page)
        for phrase in BOUNDARY:
            self.assertIn(phrase, fragment)
        self.assertNotIn('Deployment-injected revision', fragment)
        self.assertNotIn('Deployed revision', fragment)
        return state, fragment, page

    def test_missing_checkpoint_is_unavailable_without_field_table(self):
        state, fragment, _ = self.observe(None)
        self.assertIsNone(state['checkpoint'])
        self.assertEqual(state['checkpoint_status'], 'UNAVAILABLE')
        self.assertIn('Project checkpoint: UNAVAILABLE', fragment)
        self.assertNotIn('<table', fragment)
        self.assertNotIn('<th scope="row">', fragment)

    def test_corrupt_and_non_object_checkpoints_fail_closed(self):
        for payload in (b'invalid CORRUPT_MARKER', b'{', b'{"head": "CORRUPT_MARKER"',
                        b'[]', b'["CORRUPT_MARKER"]', b'null', b'42',
                        b'\xff\xfe{"head": "CORRUPT_MARKER"}'):
            with self.subTest(payload=payload):
                state, fragment, page = self.observe(payload)
                self.assertIsNone(state['checkpoint'])
                self.assertEqual(state['checkpoint_status'], 'UNAVAILABLE')
                self.assertIn('Project checkpoint: UNAVAILABLE', fragment)
                self.assertNotIn('<table', fragment)
                self.assertNotIn('CORRUPT_MARKER', page)

    def test_incomplete_checkpoint_renders_unknown_and_none_reported(self):
        for raw in ({}, {'milestone': 'M9', 'head': 'partial-head'}):
            with self.subTest(raw=raw):
                state, fragment, _ = self.observe(raw)
                self.assertEqual(state['checkpoint_status'], 'AVAILABLE')
                self.assertEqual(state['checkpoint'], {key: raw.get(key) for key in APPROVED})
                for key, label in LABELS.items():
                    if key == 'hard_blocker':
                        self.assertIn(row(label, 'None reported'), fragment)
                    elif key in raw:
                        self.assertIn(row(label, raw[key]), fragment)
                    else:
                        self.assertIn(row(label, 'UNKNOWN'), fragment)
                self.assertIn('Reported capability blockers: UNKNOWN', fragment)
                for claim in ('No hard blocker', 'no hard blocker', 'no blocker exists',
                              'No blockers', 'no blockers'):
                    self.assertNotIn(claim, fragment)

    def test_valid_checkpoint_renders_approved_fields_only(self):
        raw = valid_checkpoint() | {
            'schema_version': 'SCHEMA_MARKER', 'starting_head': 'STARTING_MARKER',
            'egx': {'scanned': 'EGX_MARKER'}, 'us': {'scanned': 'US_MARKER'},
            'live_money': 'LIVE_MARKER', 'head_semantics': 'SEMANTICS_MARKER',
            'commit_status': 'COMMIT_MARKER', '<private>': 'PRIVATE_MARKER'}
        state, fragment, _ = self.observe(raw)
        self.assertEqual(state['checkpoint_status'], 'AVAILABLE')
        self.assertEqual(list(state['checkpoint']), list(APPROVED))
        self.assertEqual(state['checkpoint'], {key: raw[key] for key in APPROVED})
        for key, label in LABELS.items():
            expected = 'None reported' if key == 'hard_blocker' else raw[key]
            self.assertIn(row(label, expected), fragment)
        for key, value in raw['capability_blockers'].items():
            self.assertIn(row(key, value), fragment)
        headers = re.findall(r'<th scope="row">(.*?)</th>', fragment)
        self.assertEqual(sorted(headers), sorted(list(LABELS.values()) + list(raw['capability_blockers'])))
        for value in ('SCHEMA_MARKER', 'STARTING_MARKER', 'EGX_MARKER', 'US_MARKER', 'LIVE_MARKER',
                      'SEMANTICS_MARKER', 'COMMIT_MARKER', 'PRIVATE_MARKER', 'private',
                      'schema_version', 'starting_head', 'live_money', 'head_semantics', 'commit_status'):
            self.assertNotIn(value, fragment)

    def test_reported_hard_blocker_text_is_rendered_escaped(self):
        raw = valid_checkpoint() | {'hard_blocker': '<b>blocked</b>'}
        _, fragment, _ = self.observe(raw)
        self.assertIn(row(LABELS['hard_blocker'], '&lt;b&gt;blocked&lt;/b&gt;'), fragment)
        self.assertNotIn('None reported', fragment)
        self.assertNotIn('<b>', fragment)

    def test_every_dynamic_value_is_escaped(self):
        raw = {key: '<script>' + key + '</script> & "q"' for key in LABELS}
        raw['capability_blockers'] = {'<b>key</b>': '<img src=x onerror=1>', 'plain': 'a & b'}
        _, fragment, page = self.observe(raw)
        for key, label in LABELS.items():
            self.assertIn(row(label, escape(raw[key])), fragment)
        for key, value in raw['capability_blockers'].items():
            self.assertIn(row(escape(key), escape(value)), fragment)
        for unsafe in ('<script>', '<b>', '<img'):
            self.assertNotIn(unsafe, fragment)
            self.assertNotIn(unsafe, page)

    def test_unsupported_field_types_render_unknown_without_echo(self):
        for value, hidden in (({'nested': 'TYPE_MARKER'}, 'TYPE_MARKER'), (['TYPE_MARKER'], 'TYPE_MARKER'),
                              (987654321, '987654321'), (12.5, '12.5'), (True, 'True'), (False, 'False')):
            for key, label in LABELS.items():
                with self.subTest(key=key, value=value):
                    raw = valid_checkpoint() | {key: value}
                    state, fragment, _ = self.observe(raw)
                    self.assertEqual(state['checkpoint'][key], value)
                    self.assertIn(row(label, 'UNKNOWN'), fragment)
                    self.assertNotIn(hidden, fragment)

    def test_unsupported_capability_blocker_shapes_fail_closed(self):
        for value in (['BLOCKER_MARKER'], 'BLOCKER_MARKER', 424242, True):
            with self.subTest(value=value):
                _, fragment, _ = self.observe(valid_checkpoint() | {'capability_blockers': value})
                self.assertIn('Reported capability blockers: UNKNOWN', fragment)
                for hidden in ('BLOCKER_MARKER', '424242', 'True'):
                    self.assertNotIn(hidden, fragment)
        blockers = {'nested': {'x': 'BLOCKER_MARKER'}, 'listed': ['BLOCKER_MARKER'],
                    'number': 424242, 'flag': True, 'empty': None, 'ok': 'fine'}
        _, fragment, _ = self.observe(valid_checkpoint() | {'capability_blockers': blockers})
        for key in ('nested', 'listed', 'number', 'flag', 'empty'):
            self.assertIn(row(key, 'UNKNOWN'), fragment)
        self.assertIn(row('ok', 'fine'), fragment)
        for hidden in ('BLOCKER_MARKER', '424242', 'True'):
            self.assertNotIn(hidden, fragment)
        _, fragment, _ = self.observe(valid_checkpoint() | {'capability_blockers': {}})
        self.assertIn('Reported capability blockers: none reported', fragment)

    def test_checkpoint_head_is_not_presented_as_deployed_revision(self):
        raw = valid_checkpoint() | {'head': 'CHECKPOINT_HEAD_MARKER'}
        state, fragment, page = self.observe(raw)
        runtime = render_runtime_observation(state)
        self.assertIsNone(state['build']['revision'])
        self.assertIn('Deployment-injected revision</th><td>UNKNOWN', runtime)
        self.assertNotIn('CHECKPOINT_HEAD_MARKER', runtime)
        self.assertEqual(page.count('CHECKPOINT_HEAD_MARKER'), 1)
        self.assertIn(row(LABELS['head'], 'CHECKPOINT_HEAD_MARKER'), fragment)

    def test_repository_checkpoint_keeps_existing_projection_and_marker(self):
        root = Path(__file__).resolve().parents[1]
        raw = json.loads((root / 'PROGRESS.json').read_text())
        with patch.dict('os.environ', {}, clear=True):
            state = load_system_state()
        self.assertEqual(state['checkpoint_status'], 'AVAILABLE')
        self.assertEqual(state['checkpoint'], {key: raw.get(key) for key in APPROVED})
        page = render_system(state)
        self.assertEqual(page.count(MARKER), 1)
        self.assertIn(render_project_checkpoint(state), page)
        self.assertNotIn('<pre>', page)


if __name__ == '__main__':
    unittest.main()
