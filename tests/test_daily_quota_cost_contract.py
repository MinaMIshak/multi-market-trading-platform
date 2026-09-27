"""Offline exact-request cost binding; fixtures are not reviewed provider evidence."""
from dataclasses import replace
from datetime import date, datetime
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tests.test_quota_source_contract import quota
from tests import test_quota_ledger_contract as ledger_tests


class DailyQuotaCostTests(unittest.TestCase):
    def setUp(self):
        self.inputs = dict(symbol='CODE', start_date=date(2025, 9, 1),
                           end_date=date(2026, 9, 24))
        self.cost = quota.VerifiedQuotaCost(2, 'engineering fixture only')
        self.contract = quota.DailyQuotaCostContract('fixture', **self.inputs, cost=self.cost)
        self.provider = SimpleNamespace(name='fixture', fetch_daily_bars=Mock(return_value='payload'))
        self.guard = Mock()
        self.wrapper = quota.QuotaLimitedDailyProvider(self.provider, self.guard, self.contract)

    def test_exact_request_reserves_and_preserves_response(self):
        self.assertEqual(self.wrapper.fetch_daily_bars(**self.inputs), 'payload')
        self.guard.admit.assert_called_once_with(self.cost)
        self.provider.fetch_daily_bars.assert_called_once_with(**self.inputs)

    def test_each_scope_mismatch_rejects_before_reservation_or_transport(self):
        for field, value in [('symbol', 'OTHER'), ('symbol', 'code'),
                             ('start_date', date(2025, 8, 31)),
                             ('end_date', date(2026, 9, 25)),
                             ('start_date', datetime(2025, 9, 1)),
                             ('end_date', '2026-09-24')]:
            with self.subTest(field=field, value=value), self.assertRaises(quota.QuotaRejected):
                self.wrapper.fetch_daily_bars(**dict(self.inputs, **{field: value}))
        self.provider.name = 'other'
        with self.assertRaises(quota.QuotaRejected):
            self.wrapper.fetch_daily_bars(**self.inputs)
        self.guard.admit.assert_not_called()
        self.provider.fetch_daily_bars.assert_not_called()

    def test_malformed_contracts_rejected_at_construction(self):
        cases = [('provider_name', v) for v in (None, '', ' FIXTURE ', 'canonical', True)]
        cases += [('symbol', v) for v in (None, '', ' CODE ', True)]
        cases += [('start_date', v) for v in (None, '2025-09-01', datetime(2025, 9, 1), date(2027, 1, 1))]
        cases += [('cost', quota.VerifiedQuotaCost(v, 'fixture')) for v in (True, 0, -1, '2')]
        cases += [('cost', quota.VerifiedQuotaCost(2, v)) for v in (None, '', ' ', True)]
        for field, value in cases:
            with self.subTest(field=field, value=value), self.assertRaises(quota.QuotaRejected):
                replace(self.contract, **{field: value})

    def test_source_change_during_reservation_still_prevents_transport(self):
        self.guard.admit.side_effect = lambda cost: setattr(self.provider, 'name', 'other')
        with self.assertRaises(quota.QuotaRejected):
            self.wrapper.fetch_daily_bars(**self.inputs)
        self.guard.admit.assert_called_once_with(self.cost)
        self.provider.fetch_daily_bars.assert_not_called()

    def test_real_ledger_charges_failed_and_repeated_attempts(self):
        # Reuse the existing isolated real SQLite setup, not a mocked ledger.
        fixture = ledger_tests.QuotaLedgerContractTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        wrapper = quota.QuotaLimitedDailyProvider(self.provider, fixture.guard, self.contract)
        self.provider.fetch_daily_bars.side_effect = RuntimeError('private transport detail')
        for _ in range(7):
            with self.assertRaisesRegex(RuntimeError, '^automatic provider attempt failed$'):
                wrapper.fetch_daily_bars(**self.inputs)
        with self.assertRaises(quota.QuotaRejected):
            wrapper.fetch_daily_bars(**self.inputs)
        self.assertEqual(self.provider.fetch_daily_bars.call_count, 7)
        with fixture.db.connect() as con:
            self.assertEqual(con.execute('SELECT used_units FROM automatic_quota WHERE id=1').fetchone()[0], 14)
