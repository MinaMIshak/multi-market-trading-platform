"""Offline configuration and real context composition with fixture transport."""
import ast
import copy
import json
from pathlib import Path
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock

from tests.test_scheduler_batch_cost_contract import quota, job_module, context

ROOT = Path(__file__).parents[1]
path = ROOT / 'app/core/scheduler_acquisition_config.py'
tree = ast.parse(path.read_text())
tree.body = [n for n in tree.body if not (
    isinstance(n, ast.ImportFrom) and n.module.startswith('app.'))]
config = ModuleType('offline_acquisition_config')
config.__dict__.update(DailyRefreshTarget=job_module.DailyRefreshTarget,
    DailyQuotaCostContract=quota.DailyQuotaCostContract,
    DailyQuotaCostContracts=quota.DailyQuotaCostContracts, VerifiedQuotaCost=quota.VerifiedQuotaCost)
exec(compile(tree, str(path), 'exec'), config.__dict__)


class AcquisitionConfigTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'config.json'
        self.doc = dict(schema_version=1, provider='fixture', lookback_days=2,
            targets=[dict(canonical_symbol=s, provider_symbol=s+'-CODE') for s in ('AAA', 'BBB')],
            cost_contracts=[dict(symbol=s+'-CODE', start_date='2026-01-01',
                end_date='2026-01-03', units=2, evidence='engineering fixture only') for s in ('AAA', 'BBB')])
        self.factory = Mock(return_value=SimpleNamespace(name='fixture'))

    def load(self, doc=None):
        self.path.write_text(json.dumps(self.doc if doc is None else doc))
        return config.load_acquisition_options(self.path, provider_factory=self.factory)

    def test_composes_context_without_legacy_secret(self):
        options = self.load()
        secret = Mock(side_effect=AssertionError('legacy secret forbidden'))
        runtime = Mock(return_value=SimpleNamespace(provider=options['provider'], execution_adapter=object()))
        ctx = context.build_scheduler_execution_context(mode='paper_refresh', database=object(),
            scheduler_repository=object(), db_path='/tmp/fixture.db', secret_path='/tmp/unused',
            secret_reader=secret, runtime_builder=runtime, **options)
        self.assertTrue(ctx.execution_enabled)
        self.assertEqual(len(runtime.call_args.kwargs['targets']), 2)
        self.assertEqual(runtime.call_args.kwargs['lookback_days'], 2)
        secret.assert_not_called()

    def test_invalid_configuration_precedes_construction(self):
        variants = []
        for key, value in [('schema_version', True), ('lookback_days', True),
                           ('lookback_days', 0), ('provider', 'eodhd'), ('provider', 'Fixture'),
                           ('targets', []), ('cost_contracts', [])]:
            variants.append(self.doc | {key: value})
        variants.append(self.doc | {'unknown': 1})
        variants.append(self.doc | {'targets': self.doc['targets'] * 2})
        variants.append(self.doc | {'cost_contracts': self.doc['cost_contracts'][:1]})
        for key, value in [('symbol', 'OTHER'), ('units', True), ('evidence', ''),
                           ('start_date', '20260101'), ('end_date', '2026-01-04')]:
            doc = copy.deepcopy(self.doc)
            doc['cost_contracts'][0][key] = value
            variants.append(doc)
        for doc in variants:
            with self.subTest(doc=doc), self.assertRaises((ValueError, quota.QuotaRejected)):
                self.load(doc)
        self.factory.assert_not_called()

    def test_duplicate_json_keys_and_size_and_relative_path_rejected(self):
        for payload in ('{"provider":"a","provider":"b"}', ' ' * (config.MAX_CONFIG_BYTES + 1)):
            self.path.write_text(payload)
            with self.assertRaises(ValueError):
                config.load_acquisition_options(self.path, provider_factory=self.factory)
        with self.assertRaises(ValueError):
            config.load_acquisition_options('relative.json', provider_factory=self.factory)
        self.factory.assert_not_called()

    def test_no_default_adapter_and_wrong_factory_identity_fail_closed(self):
        self.load()
        with self.assertRaisesRegex(ValueError, 'adapter unavailable'):
            config.load_acquisition_options(self.path)
        self.factory.return_value.name = 'other'
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            self.load()

    def test_observe_does_not_read_configuration_and_opt_in_does(self):
        loader = Mock(return_value={'fixture': True})
        env = {'EGX_ACQUISITION_CONFIG_PATH': '/missing'}
        self.assertEqual(config.worker_acquisition_options('observe', env, loader=loader), {})
        self.assertEqual(config.worker_acquisition_options('paper_refresh', {}, loader=loader), {})
        loader.assert_not_called()
        self.assertEqual(config.worker_acquisition_options('paper_refresh', env, loader=loader), {'fixture': True})
        loader.assert_called_once_with('/missing')

    def test_loader_error_propagates_without_fallback(self):
        loader = Mock(side_effect=ValueError('invalid'))
        with self.assertRaises(ValueError):
            config.worker_acquisition_options('paper_refresh',
                {'EGX_ACQUISITION_CONFIG_PATH': '/missing'}, loader=loader)

    def test_worker_passes_options_before_database_and_context_creation(self):
        tree = ast.parse((ROOT / 'app/core/scheduler_worker.py').read_text())
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
        calls = [n for n in ast.walk(main) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        lookup = {n.func.id: n for n in calls}
        self.assertLess(lookup['worker_acquisition_options'].lineno, lookup['Database'].lineno)
        self.assertTrue(any(k.arg is None and isinstance(k.value, ast.Name)
            and k.value.id == 'acquisition_options'
            for k in lookup['build_scheduler_execution_context'].keywords))
