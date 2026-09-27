"""Offline window admission checks; engineering rows are not market evidence."""
import ast
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).parents[1]


def load_classes(path, names, namespace):
    tree = ast.parse((ROOT / path).read_text())
    nodes = [ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]
    nodes.extend(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name in names)
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), path, 'exec'), namespace)
    return namespace


class RefreshWindowTests(unittest.TestCase):
    def setUp(self):
        namespace = load_classes('app/data/daily_refresh_admission.py', {
            'DailyRefreshAdmissionError', 'DailyRefreshAdmissionSummary',
            'DailyRefreshAdmissionPolicy'}, dict(
                dataclass=dataclass, date=date,
                DailyBarSemanticClass=SimpleNamespace(VALID_EXECUTABLE='valid')))
        self.policy = namespace['DailyRefreshAdmissionPolicy'](minimum_valid_bars=2)
        self.error = namespace['DailyRefreshAdmissionError']
        self.end = date(2026, 9, 24)
        self.start = self.end - timedelta(days=1)

    def validate(self, dates, **kwargs):
        return self.policy.validate_rows(tuple(SimpleNamespace(
            market_date=d, semantic_class='valid') for d in dates),
            expected_market_date=self.end, **kwargs)

    def test_inclusive_window_accepts_unordered_rows(self):
        result = self.validate([self.end, self.start], requested_start_date=self.start)
        self.assertEqual(result.valid_bar_count, 2)

    def test_older_bars_cannot_satisfy_minimum_history(self):
        with self.assertRaisesRegex(self.error, 'outside requested window'):
            self.validate([self.start - timedelta(days=1), self.end],
                          requested_start_date=self.start)

    def test_future_rows_still_fail(self):
        with self.assertRaises(self.error):
            self.validate([self.start, self.end, self.end + timedelta(days=1)],
                          requested_start_date=self.start)

    def test_invalid_start_bound_fails_closed(self):
        for value in (True, '2026-09-23', datetime(2026, 9, 23), self.end + timedelta(days=1)):
            with self.subTest(value=value), self.assertRaisesRegex(self.error, 'invalid requested'):
                self.validate([self.start, self.end], requested_start_date=value)

    def test_existing_callers_without_window_remain_supported(self):
        self.assertEqual(self.validate([self.start, self.end]).valid_bar_count, 2)

    def test_ingestion_passes_window_and_does_not_persist_rejected_response(self):
        namespace = load_classes('app/data/daily_ingestion.py', {'DailyBarIngestor'}, {})
        policy, store, repository = Mock(), Mock(), Mock()
        policy.validate_provider_response.side_effect = self.error('outside requested window')
        ingestor = namespace['DailyBarIngestor'](
            raw_store=store, repository=repository, admission_policy=policy,
            resolver=Mock(resolve=Mock(return_value={
                'canonical_ticker': 'FIXTURE', 'instrument_id': 'fixture',
                'matched_provider': 'fixture', 'matched_alias_value': 'CODE'})))
        provider = Mock()
        provider.name = 'fixture'
        with self.assertRaises(self.error):
            ingestor.ingest(provider=provider, canonical_symbol='FIXTURE',
                            provider_symbol='CODE', start_date=self.start,
                            end_date=self.end, snapshot_date=self.end)
        self.assertEqual(policy.validate_provider_response.call_args.kwargs[
            'requested_start_date'], self.start)
        store.store_bytes.assert_not_called()
        repository.save_manifest.assert_not_called()
