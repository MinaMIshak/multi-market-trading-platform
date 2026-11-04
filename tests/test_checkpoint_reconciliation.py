"""Dependency-free evidence boundaries for the durable operator checkpoint."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.checkpoint = json.loads((ROOT / 'PROGRESS.json').read_text())

    def test_unverified_runtime_does_not_present_current_counts(self):
        if self.checkpoint['reconciliation']['runtime_verified']:
            self.skipTest('A later cycle has supplied runtime verification')
        for market, fields in {'egx': ('data_ready', 'eligible', 'scanned', 'watch',
                                      'ready_no_signal', 'not_ready', 'data_stale', 'evidence_blocked'),
                               'us': ('configured_universe', 'data_ready', 'eligible',
                                      'scanned', 'candidates', 'strong_candidates')}.items():
            for field in fields:
                with self.subTest(market=market, field=field):
                    self.assertIsNone(self.checkpoint[market][field])
        self.assertIsNone(self.checkpoint['egx']['observed_at'])
        self.assertEqual(self.checkpoint['us']['session_verification'], 'UNKNOWN')

    def test_prior_claims_remain_explicitly_archived(self):
        archive = ROOT / self.checkpoint['reconciliation']['previous_checkpoint']
        historical = json.loads(archive.read_text())
        self.assertEqual(historical['head'], 'f618abb9aeea57b476764951081d9fbd7ecdff77')
        self.assertEqual(historical['egx']['scanned'], 221)
        self.assertEqual(historical['us']['scanned'], 500)
        self.assertNotEqual(historical['head'], self.checkpoint['head'])

    def test_learning_does_not_authorize_promotion_or_live_money(self):
        self.assertIs(self.checkpoint['live_money'], False)
        self.assertEqual(self.checkpoint['egx']['baseline_universe'], 224)
        for market in ('egx', 'us'):
            self.assertFalse(self.checkpoint['learning'][market]['tradeable_edge_proven'])
            self.assertEqual(self.checkpoint['learning'][market]['promotion'], 'NOT_AUTHORIZED')


if __name__ == '__main__':
    unittest.main()
