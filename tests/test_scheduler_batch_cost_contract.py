"""Offline scheduler composition; engineering fixtures are not market evidence."""
import ast
from datetime import date
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock

from tests.test_quota_source_contract import quota
from tests import test_quota_ledger_contract as ledger_tests
from tests.test_daily_refresh_result_contract import job_module
from tests.test_daily_refresh_execution_contract import execution

ROOT = Path(__file__).parents[1]


def load_context():
    path = ROOT / 'app/core/scheduler_execution_context.py'
    tree = ast.parse(path.read_text())
    tree.body = [n for n in tree.body if not (
        isinstance(n, ast.ImportFrom) and n.module.startswith('app.'))]
    module = ModuleType('offline_batch_context')
    sys.modules[module.__name__] = module
    module.DailyQuotaCostContracts = quota.DailyQuotaCostContracts
    module.read_runtime_secret = Mock()
    module.build_daily_refresh_runtime = Mock()
    module.DailyRefreshDispatcher = lambda **kw: SimpleNamespace(**kw)
    exec(compile(tree, str(path), 'exec'), module.__dict__)
    return module


context = load_context()


class SchedulerBatchCostTests(unittest.TestCase):
    def setUp(self):
        fixture = ledger_tests.QuotaLedgerContractTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.db, self.guard = fixture.db, fixture.guard
        self.start, self.end = date(2026, 1, 1), date(2026, 1, 3)
        self.targets = tuple(job_module.DailyRefreshTarget(s, s + '-CODE') for s in ('AAA', 'BBB'))
        self.contracts = quota.DailyQuotaCostContracts(tuple(
            quota.DailyQuotaCostContract('fixture', t.provider_symbol, self.start, self.end,
                                        quota.VerifiedQuotaCost(i + 2, 'fixture only'))
            for i, t in enumerate(self.targets)))
        self.transport = SimpleNamespace(name='fixture', fetch_daily_bars=Mock(return_value=None))
        self.aliases = Mock()
        self.repo = Mock()
        self.repo.successful_checkpoints.return_value = set()
        self.repo.claim_job.return_value = True
        self.secret = Mock(side_effect=AssertionError('secret access forbidden'))

    def used(self):
        with self.db.connect() as con:
            return con.execute('SELECT used_units FROM automatic_quota').fetchone()[0]

    def runtime(self, **kwargs):
        # Execute the real runtime composition with storage/pipeline fixtures.
        path = ROOT / 'app/core/daily_refresh_runtime.py'
        tree = ast.parse(path.read_text())
        tree.body = [n for n in tree.body if not (
            isinstance(n, ast.ImportFrom) and n.module.startswith('app.'))]
        module = ModuleType('offline_batch_runtime')
        sys.modules[module.__name__] = module
        module.__dict__.update(
            QuotaPolicy=quota.QuotaPolicy, QuotaGuard=lambda *a, **k: self.guard,
            QuotaLimitedDailyProvider=quota.QuotaLimitedDailyProvider,
            DailyQuotaCostContracts=quota.DailyQuotaCostContracts,
            DailyRefreshTarget=job_module.DailyRefreshTarget,
            DailyRefreshJob=job_module.DailyRefreshJob,
            DailyRefreshExecutionAdapter=execution.DailyRefreshExecutionAdapter,
            select_refresh_targets=lambda **kw: kw['targets'],
            require_refresh_targets=lambda resolver, **kw: self.aliases(**kw))
        ingestor = Mock()
        def ingest(**kw):
            kw['provider'].fetch_daily_bars(symbol=kw['provider_symbol'],
                                           start_date=kw['start_date'], end_date=kw['end_date'])
            return SimpleNamespace(provider=kw['provider'].name,
                canonical_symbol=kw['canonical_symbol'], provider_symbol=kw['provider_symbol'],
                requested_start_date=kw['start_date'], requested_end_date=kw['end_date'],
                snapshot_date=kw['snapshot_date'], record_count=260,
                manifest=SimpleNamespace(ingestion_id='fixture'))
        ingestor.ingest.side_effect = ingest
        pipeline = Mock()
        pipeline.finalize_ingestion.return_value = SimpleNamespace(artifact_id='fixture',
            canonical_manifest=SimpleNamespace(valid_bar_count=260, quarantined_bar_count=0))
        for name in ('ImmutableRawStore', 'DailyRefreshAdmissionPolicy', 'SecurityMasterRepository',
                     'DataIngestionRepository', 'DailyCanonicalStore', 'DailyCanonicalArtifactRepository'):
            setattr(module, name, Mock())
        module.DailyBarIngestor = Mock(return_value=ingestor)
        module.DailyCanonicalPipeline = Mock(return_value=pipeline)
        exec(compile(tree, str(path), 'exec'), module.__dict__)
        return module.build_daily_refresh_runtime(**kwargs)

    def build(self, **overrides):
        args = dict(mode='paper_refresh', database=self.db, scheduler_repository=self.repo,
                    db_path='/tmp/fixture.db', secret_path='/tmp/unused', secret_reader=self.secret,
                    runtime_builder=self.runtime, provider=self.transport, targets=self.targets,
                    quota_cost_contract=self.contracts, lookback_days=2)
        return context.build_scheduler_execution_context(**(args | overrides))

    def execute(self, ctx, fallback=False, day=None):
        checkpoint = (execution.CheckpointName.AFTER_SESSION_FALLBACK if fallback
                      else execution.CheckpointName.AFTER_SESSION_PRIMARY)
        return ctx.dispatcher.execution_adapter.execute(
            market_date=day or self.end, checkpoint_name=checkpoint, provider=ctx.provider)

    def test_multi_target_composition_charges_exact_costs_and_keeps_alias_gate(self):
        ctx = self.build(targets=iter(self.targets))
        self.assertEqual(self.execute(ctx).item_count, 2)
        self.assertEqual(self.used(), 5)
        self.assertEqual(self.transport.fetch_daily_bars.call_count, 2)
        self.aliases.assert_called_once_with(provider_name='fixture', targets=self.targets)
        self.secret.assert_not_called()

    def test_missing_second_cost_preflights_before_first_fetch(self):
        ctx = self.build(quota_cost_contract=quota.DailyQuotaCostContracts(self.contracts.contracts[:1]))
        with self.assertRaises(quota.QuotaRejected):
            self.execute(ctx)
        self.assertEqual(self.used(), 0)
        self.transport.fetch_daily_bars.assert_not_called()
        self.repo.mark_failed.assert_called_once()
        self.repo.mark_succeeded.assert_not_called()

    def test_alias_failure_precedes_cost_preflight_and_fetch(self):
        self.aliases.side_effect = ValueError('fixture alias rejected')
        with self.assertRaises(ValueError):
            self.execute(self.build())
        self.assertEqual(self.used(), 0)
        self.transport.fetch_daily_bars.assert_not_called()

    def test_later_session_and_other_provider_do_not_reuse_costs(self):
        for overrides, day in (({}, date(2026, 1, 4)),
                               ({'provider': SimpleNamespace(name='other')}, self.end)):
            with self.subTest(day=day), self.assertRaises(quota.QuotaRejected):
                self.execute(self.build(**overrides), day=day)
        self.assertEqual(self.used(), 0)

    def test_failed_partial_batch_and_fallback_charge_each_attempt(self):
        self.transport.fetch_daily_bars.side_effect = [None, RuntimeError('private'), None, None]
        ctx = self.build()
        with self.assertRaises(job_module.DailyRefreshJobError) as caught:
            self.execute(ctx)
        self.assertEqual(len(caught.exception.completed), 1)
        self.assertEqual(self.used(), 5)
        self.assertEqual(self.execute(ctx, fallback=True).item_count, 2)
        self.assertEqual(self.used(), 10)

    def test_budget_exhaustion_keeps_completed_attempt_charges(self):
        ctx = self.build()
        self.execute(ctx)
        self.execute(ctx, fallback=True)
        self.execute(ctx, fallback=True)
        with self.assertRaises(job_module.DailyRefreshJobError):
            self.execute(ctx, fallback=True)
        self.assertEqual(self.used(), 15)
        self.assertEqual(self.transport.fetch_daily_bars.call_count, 6)

    def test_observe_ignores_acquisition_without_secret_or_runtime(self):
        builder = Mock(side_effect=AssertionError('runtime forbidden'))
        ctx = self.build(mode=' OBSERVE ', runtime_builder=builder)
        self.assertFalse(ctx.execution_enabled)
        builder.assert_not_called()
        self.secret.assert_not_called()

    def test_incomplete_explicit_composition_rejected(self):
        builder = Mock()
        for kw in ({'targets': None}, {'targets': ()}, {'quota_cost_contract': None},
                   {'quota_cost_contract': lambda **kw: quota.VerifiedQuotaCost(1, 'fixture')},
                   {'provider': None}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                self.build(runtime_builder=builder, **kw)
        builder.assert_not_called()
        self.secret.assert_not_called()

    def test_successful_primary_suppresses_fallback_without_charging(self):
        ctx = self.build()
        self.execute(ctx)
        self.repo.successful_checkpoints.return_value = {
            execution.CheckpointName.AFTER_SESSION_PRIMARY}
        self.assertFalse(self.execute(ctx, fallback=True).claimed)
        self.assertEqual(self.used(), 5)
        self.assertEqual(self.transport.fetch_daily_bars.call_count, 2)

    def test_legacy_token_composition_remains_available_without_inferred_cost(self):
        secret = Mock(return_value='fixture-token')
        builder = Mock(return_value=SimpleNamespace(provider=object(), execution_adapter=object()))
        self.build(provider=None, targets=None, quota_cost_contract=None,
                   secret_reader=secret, runtime_builder=builder)
        secret.assert_called_once_with('/tmp/unused')
        self.assertEqual(builder.call_args.kwargs['api_token'], 'fixture-token')
        self.assertNotIn('quota_cost_contract', builder.call_args.kwargs)

    def test_contract_collection_freezes_input_and_rejects_ambiguity(self):
        original = list(self.contracts.contracts)
        batch = quota.DailyQuotaCostContracts(original)
        original.clear()
        self.assertEqual(len(batch.contracts), 2)
        for values in ((), (object(),), (batch.contracts[0],) * 2):
            with self.subTest(values=values), self.assertRaises(quota.QuotaRejected):
                quota.DailyQuotaCostContracts(values)
