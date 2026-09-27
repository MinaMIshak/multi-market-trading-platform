"""Offline launch composition checks; no acquisition or runtime evidence."""
import ast
from datetime import date
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from app.egx_refresh_mapping import select_refresh_targets
from tests.test_daily_refresh_result_contract import job_module
from tests.test_quota_source_contract import quota


ROOT = Path(__file__).parents[1]


class RefreshLaunchSelectionTests(unittest.TestCase):
    def setUp(self):
        namespace = {'VerifiedQuotaCost': quota.VerifiedQuotaCost,
                     'DailyQuotaCostContract': quota.DailyQuotaCostContract}
        self.cost = namespace['VerifiedQuotaCost'](1, 'engineering fixture')
        self.mapping = Mock()
        self.runtime = Mock()
        self.runtime.return_value.refresh_job.run.return_value.items = [SimpleNamespace(
            ingestion_id='fixture', artifact_id='fixture', valid_bar_count=260)]

        def blocked(reason):
            raise ValueError(reason)

        namespace.update(
            _now=lambda: None, admit_calendar=lambda source, at: source,
            _identity=Mock(), _blocked=blocked, select_refresh_targets=select_refresh_targets,
            DEFAULT_EODHD_TARGETS=(job_module.DailyRefreshTarget('FIXTURE', 'LEGACY.EGX'),),
            DailyRefreshTarget=job_module.DailyRefreshTarget,
            require_refresh_mapping=self.mapping, SecurityMasterRepository=Mock(),
            SchedulerRepository=Mock(), build_daily_refresh_runtime=self.runtime)
        tree = ast.parse((ROOT / 'app/paper/swing_launch.py').read_text())
        node = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == 'refresh_once')
        exec(compile(ast.Module(body=[node], type_ignores=[]),
                     'app/paper/swing_launch.py', 'exec'), namespace)
        self.refresh = namespace['refresh_once']
        self.source = SimpleNamespace(symbol='FIXTURE', instrument_id='fixture',
                                      history_start=date(2025, 9, 1),
                                      signal_session=SimpleNamespace(market_date=date(2026, 9, 24)))

    def run_refresh(self, **kwargs):
        return self.refresh(object(), 'unused', self.source, cost=self.cost, **kwargs)

    def test_non_eodhd_default_rejected_even_when_alias_gate_would_accept(self):
        for name in ('fixture', 'tradingview_tvdatafeed_egx', None, 'canonical'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'explicit refresh targets'):
                self.run_refresh(provider=SimpleNamespace(name=name))
        self.mapping.assert_not_called()
        self.runtime.assert_not_called()

    def test_eodhd_defaults_preserved_for_injected_and_token_paths(self):
        for provider in (None, SimpleNamespace(name='eodhd')):
            with self.subTest(provider=provider):
                self.run_refresh(provider=provider)
                self.assertEqual(self.mapping.call_args.kwargs['provider_name'], 'eodhd')
                self.assertEqual(self.runtime.call_args.kwargs['targets'][0].provider_symbol,
                                 'LEGACY.EGX')

    def test_explicit_alternate_target_reaches_alias_admission_unchanged(self):
        provider = SimpleNamespace(name='fixture')
        target = job_module.DailyRefreshTarget('FIXTURE', 'EXPLICIT-CODE')
        result = self.run_refresh(provider=provider, target=target)
        self.assertEqual(result['operation'], 'REFRESH_COMPLETED_SIGNAL_NOT_RUN')
        self.assertEqual(self.mapping.call_args.kwargs['provider_name'], 'fixture')
        self.assertEqual(self.mapping.call_args.kwargs['provider_symbol'], 'EXPLICIT-CODE')
        self.assertEqual(self.runtime.call_args.kwargs['targets'], (target,))

    def test_explicit_alias_failure_still_prevents_runtime(self):
        self.mapping.side_effect = ValueError('alias rejected')
        with self.assertRaisesRegex(ValueError, 'alias rejected'):
            self.run_refresh(provider=SimpleNamespace(name='fixture'),
                             target=job_module.DailyRefreshTarget('FIXTURE', 'EXPLICIT-CODE'))
        self.runtime.assert_not_called()

    def test_launch_cost_is_bound_to_exact_alias_source_and_window(self):
        self.run_refresh(provider=SimpleNamespace(name='fixture'),
                         target=job_module.DailyRefreshTarget('FIXTURE', 'EXPLICIT-CODE'))
        contract = self.runtime.call_args.kwargs['quota_cost_contract']
        inputs = dict(provider_name='fixture', symbol='EXPLICIT-CODE',
                      start_date=self.source.history_start,
                      end_date=self.source.signal_session.market_date)
        self.assertIs(contract.resolve(**inputs), self.cost)
        for field, value in [('provider_name', 'eodhd'), ('symbol', 'FIXTURE'),
                             ('end_date', date(2026, 9, 25))]:
            with self.subTest(field=field), self.assertRaises(quota.QuotaRejected):
                contract.resolve(**dict(inputs, **{field: value}))

    def test_malformed_cost_evidence_blocks_before_composition(self):
        for evidence in (None, True, '', ' '):
            self.cost = quota.VerifiedQuotaCost(1, evidence)
            with self.subTest(evidence=evidence), self.assertRaisesRegex(ValueError, 'verified quota cost'):
                self.run_refresh()
        self.mapping.assert_not_called()
        self.runtime.assert_not_called()
