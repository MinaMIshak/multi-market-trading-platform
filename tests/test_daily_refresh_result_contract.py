"""Offline job-result checks using engineering fixtures, never market evidence."""
import importlib.util
from datetime import date
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock

# Load the actual dependency-free job without app.data's optional runtime imports.
SPEC = importlib.util.spec_from_file_location(
    'refresh_job_contract', Path(__file__).parents[1] / 'app/data/daily_refresh_job.py')
job_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = job_module
SPEC.loader.exec_module(job_module)


class RefreshResultContractTests(unittest.TestCase):
    def run_counts(self, counts, *, second_counts=None, identity_changes=None, provider_name="fixture", target_admission=None):
        ingestor, pipeline = Mock(), Mock()
        self.ingestor = ingestor
        self.pipeline = pipeline
        values = [counts] if second_counts is None else [counts, second_counts]
        ingestor.ingest.side_effect = [
            SimpleNamespace(**(dict(
                provider='fixture', canonical_symbol=f'FIXTURE{i}', provider_symbol=f'CODE{i}',
                requested_start_date=date(2026, 1, 1),
                requested_end_date=date(2026, 1, 2), snapshot_date=date(2026, 1, 2),
                record_count=value[0], manifest=SimpleNamespace(ingestion_id='fixture'))
                | ((identity_changes or {}) if i == len(values) - 1 else {})))
            for i, value in enumerate(values)]
        pipeline.finalize_ingestion.side_effect = [
            SimpleNamespace(artifact_id='fixture', canonical_manifest=SimpleNamespace(
                valid_bar_count=value[1], quarantined_bar_count=value[2])) for value in values]
        job = job_module.DailyRefreshJob(
            ingestor=ingestor, pipeline=pipeline, canonical_store=object(),
            target_admission=target_admission,
            artifact_repository=object(), targets=tuple(
                job_module.DailyRefreshTarget(f'FIXTURE{i}', f'CODE{i}')
                for i in range(len(values))))
        return job.run(provider=SimpleNamespace(name=provider_name), start_date=date(2026, 1, 1),
                       end_date=date(2026, 1, 2), snapshot_date=date(2026, 1, 2))

    def test_scope_rejection_precedes_all_ingestion_and_promotion(self):
        admission = Mock(side_effect=ValueError('unbound later target'))
        with self.assertRaisesRegex(ValueError, 'unbound later target'):
            self.run_counts((3, 3, 0), second_counts=(3, 3, 0), target_admission=admission)
        self.ingestor.ingest.assert_not_called()
        self.pipeline.finalize_ingestion.assert_not_called()
        self.assertEqual(admission.call_args.kwargs['provider_name'], 'fixture')
        self.assertEqual(len(admission.call_args.kwargs['targets']), 2)

    def test_real_scope_gate_rejects_later_unbound_alias_before_first_fetch(self):
        from functools import partial
        from uuid import UUID
        from app.egx_refresh_mapping import require_refresh_targets

        def resolve(symbol, *, provider):
            index = 0 if symbol in ('FIXTURE0', 'CODE0') else 1
            if provider == 'fixture' and index == 1:
                raise KeyError('unregistered second alias')
            return dict(instrument_id=str(UUID(int=index + 1)),
                        canonical_ticker=f'FIXTURE{index}', instrument_type='EQUITY',
                        matched_provider=provider, matched_alias_value=symbol)

        with self.assertRaisesRegex(ValueError, 'alias unavailable'):
            self.run_counts((3, 3, 0), second_counts=(3, 3, 0),
                            target_admission=partial(require_refresh_targets,
                                                     SimpleNamespace(resolve=resolve)))
        self.ingestor.ingest.assert_not_called()
        self.pipeline.finalize_ingestion.assert_not_called()

    def test_admitted_scope_runs_normally(self):
        admission = Mock()
        result = self.run_counts((3, 3, 0), target_admission=admission)
        admission.assert_called_once()
        self.assertEqual(len(result.items), 1)

    def test_valid_and_quarantined_counts_are_preserved(self):
        result = self.run_counts((3, 2, 1))
        item = result.items[0]
        self.assertEqual((item.record_count, item.valid_bar_count, item.quarantined_bar_count), (3, 2, 1))

    def test_mismatched_or_missing_identity_never_promotes(self):
        for field, wrong in (
            ('provider', 'other'), ('canonical_symbol', 'OTHER'), ('provider_symbol', 'OTHER-CODE'),
            ('requested_start_date', date(2025, 1, 1)),
            ('requested_end_date', date(2026, 1, 1)),
            ('snapshot_date', date(2026, 1, 3)),
        ):
            for value in (wrong, None):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(job_module.DailyRefreshJobError) as caught:
                        self.run_counts((3, 3, 0), identity_changes={field: value})
                    self.assertEqual(caught.exception.completed, ())
                    self.assertEqual(caught.exception.cause_type, 'ValueError')
                    self.pipeline.finalize_ingestion.assert_not_called()

    def test_invalid_provider_identity_rejected_before_ingestion(self):
        for value in (None, '', ' ', ' fixture', 'FIXTURE', True, 42):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.run_counts((3, 3, 0), provider_name=value)
            self.ingestor.ingest.assert_not_called()
            self.pipeline.finalize_ingestion.assert_not_called()

    def test_later_source_mismatch_retains_only_prior_promotion(self):
        with self.assertRaises(job_module.DailyRefreshJobError) as caught:
            self.run_counts((3, 3, 0), second_counts=(3, 3, 0),
                            identity_changes={'provider': 'other'})
        self.assertEqual([item.canonical_symbol for item in caught.exception.completed], ['FIXTURE0'])
        self.assertEqual(self.pipeline.finalize_ingestion.call_count, 1)

    def test_later_identity_mismatch_retains_only_prior_promotion(self):
        with self.assertRaises(job_module.DailyRefreshJobError) as caught:
            self.run_counts((3, 3, 0), second_counts=(3, 3, 0),
                            identity_changes={'canonical_symbol': 'FIXTURE0'})
        self.assertEqual(caught.exception.canonical_symbol, 'FIXTURE1')
        self.assertEqual([item.canonical_symbol for item in caught.exception.completed], ['FIXTURE0'])
        self.assertEqual(self.pipeline.finalize_ingestion.call_count, 1)

    def test_malformed_counts_never_become_success(self):
        for position in range(3):
            for value in (True, False, 1.0, 1.9, '1', None, -1, float('nan'), float('inf')):
                counts = [2, 1, 1]
                counts[position] = value
                with self.subTest(position=position, value=value):
                    with self.assertRaises(job_module.DailyRefreshJobError) as caught:
                        self.run_counts(tuple(counts))
                    self.assertEqual(caught.exception.completed, ())
                    self.assertEqual(caught.exception.cause_type, 'ValueError')

    def test_inconsistent_totals_never_become_success(self):
        for counts in ((3, 1, 1), (1, 1, 1), (0, 1, 0)):
            with self.subTest(counts=counts), self.assertRaises(job_module.DailyRefreshJobError):
                self.run_counts(counts)

    def test_invalid_ingestion_count_cannot_promote_artifact(self):
        for count in (True, False, 1.0, 1.9, '1', None, -1, float('nan'), float('inf')):
            with self.subTest(count=count):
                with self.assertRaises(job_module.DailyRefreshJobError):
                    self.run_counts((count, 1, 0))
                self.pipeline.finalize_ingestion.assert_not_called()

    def test_invalid_later_ingestion_preserves_only_prior_promotion(self):
        with self.assertRaises(job_module.DailyRefreshJobError) as caught:
            self.run_counts((3, 2, 1), second_counts=(True, 1, 0))
        self.assertEqual(self.ingestor.ingest.call_count, 2)
        self.assertEqual(self.pipeline.finalize_ingestion.call_count, 1)
        self.assertEqual([item.canonical_symbol for item in caught.exception.completed], ['FIXTURE0'])

    def test_failure_retains_only_preceding_valid_results(self):
        with self.assertRaises(job_module.DailyRefreshJobError) as caught:
            self.run_counts((3, 3, 0), second_counts=(3, 2.9, 0))
        self.assertEqual(caught.exception.canonical_symbol, 'FIXTURE1')
        self.assertEqual([item.canonical_symbol for item in caught.exception.completed], ['FIXTURE0'])


if __name__ == '__main__':
    unittest.main()
