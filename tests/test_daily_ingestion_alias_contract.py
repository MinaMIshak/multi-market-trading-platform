"""Offline direct-ingestion alias boundary tests; no market observations."""
from dataclasses import dataclass
from datetime import date, datetime
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tests.test_daily_refresh_window_contract import load_classes


class DailyIngestionAliasTests(unittest.TestCase):
    def setUp(self):
        namespace = load_classes('app/data/daily_ingestion.py', {'DailyBarIngestor', 'DailyBarIngestionResult'},
                                 {'date': date, 'dataclass': dataclass,
                                  'DataAssetType': SimpleNamespace(DAILY_BARS='daily'),
                                  'BarGranularity': SimpleNamespace(D1='daily'),
                                  'IngestionStatus': SimpleNamespace(RECEIVED='received')})
        self.identity = dict(instrument_id='fixture-id', canonical_ticker='FIXTURE')
        self.alias = dict(self.identity, matched_provider='free_fixture', matched_alias_value='CODE')
        self.resolver = Mock()
        self.store, self.repository, self.provider = Mock(), Mock(), Mock()
        self.provider.name = 'free_fixture'
        self.ingestor = namespace['DailyBarIngestor'](
            raw_store=self.store, repository=self.repository, resolver=self.resolver)

    def run_ingestion(self):
        self.resolver.resolve.side_effect = [self.identity, self.alias]
        return self.ingestor.ingest(
            provider=self.provider, canonical_symbol='FIXTURE', provider_symbol='CODE',
            start_date=date(2026, 9, 1), end_date=date(2026, 9, 24),
            snapshot_date=date(2026, 9, 24))

    def test_bad_aliases_fail_before_fetch_or_storage(self):
        cases = [None, {}, KeyError('absent'), ValueError('ambiguous')]
        for field, value in [('instrument_id', 'other-id'), ('canonical_ticker', 'OTHER'),
                             ('matched_provider', 'other_source'), ('matched_alias_value', 'OTHER')]:
            cases.append(dict(self.alias, **{field: value}))
        for alias in cases:
            with self.subTest(alias=alias):
                self.alias = alias
                with self.assertRaisesRegex(ValueError, 'daily provider alias'):
                    self.run_ingestion()
                self.provider.fetch_daily_bars.assert_not_called()
                self.store.store_bytes.assert_not_called()
                self.repository.save_manifest.assert_not_called()

    def test_matching_alias_reaches_exact_provider_request(self):
        self.provider.fetch_daily_bars.side_effect = RuntimeError('fetch sentinel')
        with self.assertRaisesRegex(RuntimeError, 'fetch sentinel'):
            self.run_ingestion()
        self.assertEqual(self.resolver.resolve.call_args_list[-1].kwargs, {'provider': 'free_fixture'})
        self.provider.fetch_daily_bars.assert_called_once_with(
            symbol='CODE', start_date=date(2026, 9, 1), end_date=date(2026, 9, 24))
        self.store.store_bytes.assert_not_called()

    def test_invalid_provider_namespace_fails_before_alias_lookup(self):
        for name in ('canonical', '', ' FREE ', None):
            with self.subTest(name=name):
                self.resolver.reset_mock()
                self.provider.name = name
                with self.assertRaisesRegex(ValueError, 'provider name'):
                    self.run_ingestion()
                self.resolver.resolve.assert_called_once_with('FIXTURE', provider='canonical')
                self.provider.fetch_daily_bars.assert_not_called()

    def test_invalid_windows_fail_before_any_external_effect(self):
        valid = dict(start_date=date(2026, 9, 1), end_date=date(2026, 9, 24),
                     snapshot_date=date(2026, 9, 24))
        cases = [{**valid, field: value} for field in valid
                 for value in (None, True, '2026-09-24', datetime(2026, 9, 24))]
        cases.extend([
            dict(valid, start_date=date(2026, 9, 25)),
            dict(valid, snapshot_date=date(2026, 9, 23)),
        ])
        for window in cases:
            with self.subTest(window=window), self.assertRaises(ValueError):
                self.ingestor.ingest(provider=self.provider, canonical_symbol='FIXTURE',
                                     provider_symbol='CODE', **window)
            self.resolver.resolve.assert_not_called()
            self.provider.fetch_daily_bars.assert_not_called()
            self.store.store_bytes.assert_not_called()
            self.repository.save_manifest.assert_not_called()

    def test_single_day_window_with_later_snapshot_reaches_fetch(self):
        self.resolver.resolve.side_effect = [self.identity, self.alias]
        self.provider.fetch_daily_bars.side_effect = RuntimeError('fetch sentinel')
        with self.assertRaisesRegex(RuntimeError, 'fetch sentinel'):
            self.ingestor.ingest(provider=self.provider, canonical_symbol='FIXTURE',
                                 provider_symbol='CODE', start_date=date(2026, 9, 24),
                                 end_date=date(2026, 9, 24), snapshot_date=date(2026, 9, 25))
        self.provider.fetch_daily_bars.assert_called_once_with(
            symbol='CODE', start_date=date(2026, 9, 24), end_date=date(2026, 9, 24))

    def test_malformed_counts_fail_before_admission_or_persistence(self):
        self.ingestor.admission_policy = Mock()
        for count in (None, True, False, -1, 1.5, 1.0, '1'):
            with self.subTest(count=count):
                self.provider.fetch_daily_bars.return_value = SimpleNamespace(record_count=count)
                with self.assertRaisesRegex(ValueError, 'record_count'):
                    self.run_ingestion()
                self.ingestor.admission_policy.validate_provider_response.assert_not_called()
                self.store.store_bytes.assert_not_called()
                self.repository.save_manifest.assert_not_called()

    def test_nonnegative_integer_counts_preserved_without_claiming_readiness(self):
        for count in (0, 2):
            with self.subTest(count=count):
                self.store.reset_mock()
                self.repository.reset_mock()
                self.provider.fetch_daily_bars.return_value = SimpleNamespace(
                    record_count=count, payload=b'fixture', filename='fixture.json',
                    metadata={}, source_uri=None)
                result = self.run_ingestion()
                self.assertEqual(result.record_count, count)
                self.assertEqual(self.store.store_bytes.call_args.kwargs['record_count'], count)
                self.repository.save_manifest.assert_called_once()

    def test_valid_count_does_not_bypass_semantic_admission(self):
        self.provider.fetch_daily_bars.return_value = SimpleNamespace(record_count=2)
        self.ingestor.admission_policy = Mock()
        self.ingestor.admission_policy.validate_provider_response.side_effect = ValueError('semantic rejection')
        with self.assertRaisesRegex(ValueError, 'semantic rejection'):
            self.run_ingestion()
        self.store.store_bytes.assert_not_called()
        self.repository.save_manifest.assert_not_called()
