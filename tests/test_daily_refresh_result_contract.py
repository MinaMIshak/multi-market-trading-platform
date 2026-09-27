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
    def run_counts(self, counts, *, second_counts=None):
        ingestor, pipeline = Mock(), Mock()
        values = [counts] if second_counts is None else [counts, second_counts]
        ingestor.ingest.side_effect = [
            SimpleNamespace(record_count=value[0], manifest=SimpleNamespace(ingestion_id='fixture'))
            for value in values]
        pipeline.finalize_ingestion.side_effect = [
            SimpleNamespace(artifact_id='fixture', canonical_manifest=SimpleNamespace(
                valid_bar_count=value[1], quarantined_bar_count=value[2])) for value in values]
        job = job_module.DailyRefreshJob(
            ingestor=ingestor, pipeline=pipeline, canonical_store=object(),
            artifact_repository=object(), targets=tuple(
                job_module.DailyRefreshTarget(f'FIXTURE{i}', f'CODE{i}')
                for i in range(len(values))))
        return job.run(provider=object(), start_date=date(2026, 1, 1),
                       end_date=date(2026, 1, 2), snapshot_date=date(2026, 1, 2))

    def test_valid_and_quarantined_counts_are_preserved(self):
        result = self.run_counts((3, 2, 1))
        item = result.items[0]
        self.assertEqual((item.record_count, item.valid_bar_count, item.quarantined_bar_count), (3, 2, 1))

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

    def test_failure_retains_only_preceding_valid_results(self):
        with self.assertRaises(job_module.DailyRefreshJobError) as caught:
            self.run_counts((3, 3, 0), second_counts=(3, 2.9, 0))
        self.assertEqual(caught.exception.canonical_symbol, 'FIXTURE1')
        self.assertEqual([item.canonical_symbol for item in caught.exception.completed], ['FIXTURE0'])


if __name__ == '__main__':
    unittest.main()
