"""Actual SQLite quota contracts; offline fixtures are not provider evidence."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tests.test_quota_source_contract import quota

SPEC = importlib.util.spec_from_file_location(
    'quota_ledger_database', Path(__file__).parents[1] / 'app/storage/database.py')
database_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(database_module)
Database = database_module.Database


class QuotaLedgerContractTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Database(Path(self.directory.name) / 'quota.db')
        self.db.initialize()
        self.now = datetime.fromisoformat('2026-09-27T12:00:00+00:00')
        self.guard = quota.QuotaGuard(self.db, clock=lambda: self.now)
        self.provider = SimpleNamespace(name='fixture', fetch_daily_bars=Mock(return_value='result'))
        self.inputs = dict(symbol='FIXTURE', start_date='start', end_date='end')

    def cost(self, units=3):
        return quota.VerifiedQuotaCost(units, 'offline engineering fixture')

    def ledger(self):
        with closing(self.db.connect()) as con:
            return tuple(con.execute(
                'SELECT quota_day, used_units FROM automatic_quota WHERE id = 1').fetchone())

    def execute(self, sql, args=()):
        with closing(self.db.connect()) as con, con:
            con.execute(sql, args)

    def wrapper(self, contract=None, guard=None):
        return quota.QuotaLimitedDailyProvider(
            self.provider, guard or self.guard,
            contract if contract is not None else lambda **kw: self.cost())

    def test_transport_observes_committed_reservation(self):
        self.provider.fetch_daily_bars.side_effect = lambda **kw: self.ledger()[1]
        self.assertEqual(self.wrapper().fetch_daily_bars(**self.inputs), 3)
        self.provider.fetch_daily_bars.assert_called_once_with(**self.inputs)

    def test_source_change_after_real_reservation_retains_charge(self):
        def admit(cost):
            self.guard.admit(cost)
            self.provider.name = 'changed'
        guard = SimpleNamespace(admit=admit)
        with self.assertRaisesRegex(quota.QuotaRejected, 'identity changed'):
            self.wrapper(guard=guard).fetch_daily_bars(**self.inputs)
        self.assertEqual(self.ledger()[1], 3)
        self.provider.fetch_daily_bars.assert_not_called()

    def test_source_change_during_cost_does_not_charge(self):
        def contract(**kw):
            self.provider.name = 'changed'
            return self.cost()
        with self.assertRaises(quota.QuotaRejected):
            self.wrapper(contract).fetch_daily_bars(**self.inputs)
        self.assertEqual(self.ledger()[1], 0)
        self.provider.fetch_daily_bars.assert_not_called()

    def test_failure_is_charged_and_repeated_attempts_are_bounded(self):
        self.provider.fetch_daily_bars.side_effect = ValueError('private detail')
        wrapper = self.wrapper(lambda **kw: self.cost(4))
        for expected in (4, 8, 12):
            with self.assertRaisesRegex(RuntimeError, '^automatic provider attempt failed$'):
                wrapper.fetch_daily_bars(**self.inputs)
            self.assertEqual(self.ledger()[1], expected)
        for _ in range(3):
            with self.assertRaises(quota.QuotaRejected):
                wrapper.fetch_daily_bars(**self.inputs)
        self.assertEqual(self.provider.fetch_daily_bars.call_count, 3)
        self.assertEqual(self.ledger()[1], 12)

    def test_restart_and_concurrent_guards_share_one_budget(self):
        self.guard.admit(self.cost(3))
        def attempt(_):
            restarted = Database(self.db.path)
            try:
                quota.QuotaGuard(restarted, clock=lambda: self.now).admit(self.cost())
                return True
            except quota.QuotaRejected:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(attempt, range(20))), 4)
        self.assertEqual(self.ledger()[1], 15)
        restarted = Database(self.db.path)
        restarted.initialize()
        with self.assertRaises(quota.QuotaRejected):
            quota.QuotaGuard(restarted, clock=lambda: self.now).admit(self.cost(1))

    def test_reset_uses_utc_and_rejects_clock_rollback(self):
        self.now = datetime.fromisoformat('2026-09-27T23:59:59+00:00')
        self.guard.admit(self.cost(15))
        self.now = datetime.fromisoformat('2026-09-28T02:59:59+03:00')
        with self.assertRaises(quota.QuotaRejected):
            self.guard.admit(self.cost(1))
        self.now = datetime.fromisoformat('2026-09-28T00:00:00+00:00')
        self.guard.admit(self.cost(2))
        self.assertEqual(self.ledger(), ('2026-09-28', 2))
        self.now = datetime.fromisoformat('2026-09-27T23:59:59+00:00')
        with self.assertRaisesRegex(quota.QuotaRejected, 'backwards'):
            self.guard.admit(self.cost())
        self.assertEqual(self.ledger(), ('2026-09-28', 2))

    def test_invalid_costs_and_policies_never_charge(self):
        for cost in (None, self.cost(True), self.cost(0), self.cost(-1),
                     self.cost(1.5), quota.VerifiedQuotaCost(1, ' ')):
            with self.subTest(cost=cost), self.assertRaises(quota.QuotaRejected):
                self.wrapper(lambda **kw: cost).fetch_daily_bars(**self.inputs)
        for changes in ({'automatic_budget': 16}, {'protected_reserve': 4},
                        {'consume_extra_credits': True}, {'daily_allowance': 21}):
            guard = quota.QuotaGuard(self.db, policy=replace(quota.QuotaPolicy(), **changes))
            with self.subTest(changes=changes), self.assertRaises(quota.QuotaRejected):
                self.wrapper(guard=guard).fetch_daily_bars(**self.inputs)
        self.assertEqual(self.ledger()[1], 0)
        self.provider.fetch_daily_bars.assert_not_called()

    def test_malformed_missing_and_unavailable_ledger_fail_closed(self):
        self.execute("UPDATE automatic_quota SET quota_day = ''")
        with self.assertRaisesRegex(quota.QuotaRejected, 'invalid quota ledger'):
            self.wrapper().fetch_daily_bars(**self.inputs)
        self.execute('DELETE FROM automatic_quota')
        with self.assertRaisesRegex(quota.QuotaRejected, 'unavailable'):
            self.wrapper().fetch_daily_bars(**self.inputs)
        self.execute('DROP TABLE automatic_quota')
        with self.assertRaisesRegex(quota.QuotaRejected, 'unavailable'):
            self.wrapper().fetch_daily_bars(**self.inputs)
        self.provider.fetch_daily_bars.assert_not_called()

    def test_naive_clock_leaves_ledger_unchanged(self):
        before = self.ledger()
        self.now = datetime(2026, 9, 27)
        with self.assertRaisesRegex(quota.QuotaRejected, 'aware quota clock required'):
            self.wrapper().fetch_daily_bars(**self.inputs)
        self.assertEqual(self.ledger(), before)
        self.provider.fetch_daily_bars.assert_not_called()

    def test_schema_upgrade_preserves_existing_data_and_requires_opt_in(self):
        self.execute('DROP TABLE automatic_quota')
        self.execute("UPDATE schema_meta SET value = '7' WHERE key = 'schema_version'")
        self.execute("INSERT INTO market_sessions VALUES ('2026-01-01', 'UNKNOWN', '{}', 'fixture')")
        with self.assertRaisesRegex(RuntimeError, 'upgrade required'):
            self.db.initialize()
        self.assertEqual(self.db.schema_version(), 7)
        self.db.initialize(allow_upgrade=True)
        self.assertEqual(self.db.schema_version(), database_module.SCHEMA_VERSION)
        self.assertEqual(self.ledger()[1], 0)
        with closing(self.db.connect()) as con:
            self.assertEqual(con.execute('SELECT status FROM market_sessions').fetchone()[0], 'UNKNOWN')
