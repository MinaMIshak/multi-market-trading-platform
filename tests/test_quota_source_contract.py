"""Offline quota-wrapper source continuity, not provider operational evidence."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


# Load the complete module without app.data's optional runtime imports.
path = Path(__file__).parents[1] / 'app/data/quota.py'
spec = importlib.util.spec_from_file_location('quota_source_contract', path)
quota = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = quota
spec.loader.exec_module(quota)
Wrapper = quota.QuotaLimitedDailyProvider
Rejected = quota.QuotaRejected


class QuotaSourceContractTests(unittest.TestCase):
    def setUp(self):
        self.provider = SimpleNamespace(name='fixture', fetch_daily_bars=Mock(return_value='result'))
        self.guard = Mock()
        self.cost = Mock(return_value=object())
        self.wrapper = Wrapper(self.provider, self.guard, self.cost)
        self.inputs = dict(symbol='FIXTURE', start_date='start', end_date='end')

    def test_cost_callback_source_change_prevents_reservation_and_transport(self):
        def change(**kwargs):
            self.provider.name = 'different'
            return object()
        self.cost.side_effect = change
        with self.assertRaises(Rejected):
            self.wrapper.fetch_daily_bars(**self.inputs)
        self.guard.admit.assert_not_called()
        self.provider.fetch_daily_bars.assert_not_called()

    def test_guard_source_change_prevents_transport_without_refund(self):
        self.guard.admit.side_effect = lambda cost: setattr(self.provider, 'name', 'different')
        with self.assertRaises(Rejected):
            self.wrapper.fetch_daily_bars(**self.inputs)
        self.guard.admit.assert_called_once_with(self.cost.return_value)
        self.provider.fetch_daily_bars.assert_not_called()

    def test_unchanged_source_preserves_exact_request_and_result(self):
        self.assertEqual(self.wrapper.fetch_daily_bars(**self.inputs), 'result')
        self.cost.assert_called_once_with(**self.inputs)
        self.guard.admit.assert_called_once_with(self.cost.return_value)
        self.provider.fetch_daily_bars.assert_called_once_with(**self.inputs)

    def test_quota_rejection_prevents_transport(self):
        self.guard.admit.side_effect = Rejected('budget exhausted')
        with self.assertRaises(Rejected):
            self.wrapper.fetch_daily_bars(**self.inputs)
        self.provider.fetch_daily_bars.assert_not_called()

    def test_transport_failure_remains_sanitized_and_not_retried(self):
        self.provider.fetch_daily_bars.side_effect = ValueError('private detail')
        with self.assertRaisesRegex(RuntimeError, '^automatic provider attempt failed$'):
            self.wrapper.fetch_daily_bars(**self.inputs)
        self.guard.admit.assert_called_once()
        self.provider.fetch_daily_bars.assert_called_once()
