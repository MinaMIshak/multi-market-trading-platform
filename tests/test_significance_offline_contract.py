"""Stdlib-only checks of reporting purity; fixtures are not market evidence."""
from copy import deepcopy
import json
import random
import unittest

from app.learning import significance as sig


class OfflineReportingTests(unittest.TestCase):
    def test_sorted_session_summary_is_deterministic_and_read_only(self):
        rng = random.Random(43)
        series = {f'2026-01-{i:02d}': rng.gauss(.01, .03) for i in range(1, 29)}
        before = deepcopy(series)
        result = sig.summary(series, metric='fixture', horizon=3)
        self.assertEqual(result, sig.summary(dict(reversed(list(series.items()))), metric='fixture', horizon=3))
        self.assertEqual(series, before)
        json.dumps(result, allow_nan=False)
        self.assertEqual(result['mean']['method'], 'SIG-v1')
        self.assertIn('not sufficient for promotion', result['mean']['note'])

    def test_common_session_pairing_leaves_inputs_unchanged(self):
        champion = {str(i): i / 100 for i in range(20)}
        challenger = {str(i): i / 100 + .01 for i in range(5, 25)}
        before = deepcopy((champion, challenger))
        result = sig.paired(challenger, champion)
        self.assertEqual(result['common_sessions'], 15)
        self.assertAlmostEqual(result['estimate'], .01)
        self.assertEqual((champion, challenger), before)

    def test_unavailable_and_degenerate_evidence_never_claims_significance(self):
        for values, status in (([], 'INSUFFICIENT_SAMPLE'),
                               ([None, float('nan')] * 12, 'INSUFFICIENT_SAMPLE'),
                               ([0.] * 24, 'DEGENERATE_ZERO_VARIANCE')):
            with self.subTest(status=status):
                result = sig.mean_test(values)
                self.assertEqual(result['status'], status)
                self.assertFalse(result['distinguishable'])
                json.dumps(result, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
