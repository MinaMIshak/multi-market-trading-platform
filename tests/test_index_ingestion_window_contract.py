"""Offline index request admission; fixtures are not market evidence."""
from datetime import date, datetime
import unittest
from unittest.mock import Mock

from tests.test_daily_refresh_window_contract import load_classes


class IndexIngestionWindowTests(unittest.TestCase):
    def setUp(self):
        namespace = load_classes('app/data/index_ingestion.py',
                                 {'IndexHistoryIngestor'}, {'date': date})
        self.store, self.repository, self.provider = Mock(), Mock(), Mock()
        self.provider.name = "fixture_index_source"
        self.ingestor = namespace['IndexHistoryIngestor'](
            raw_store=self.store, repository=self.repository)
        self.window = dict(start_date=date(2026, 9, 1), end_date=date(2026, 9, 24),
                           snapshot_date=date(2026, 9, 24))

    def test_invalid_windows_have_no_external_effects(self):
        cases = [{**self.window, field: value} for field in self.window
                 for value in (None, True, '2026-09-24', datetime(2026, 9, 24))]
        cases.extend([dict(self.window, start_date=date(2026, 9, 25)),
                      dict(self.window, snapshot_date=date(2026, 9, 23))])
        for window in cases:
            with self.subTest(window=window), self.assertRaises(ValueError):
                self.ingestor.ingest(provider=self.provider, index_name='CASE30', **window)
            self.provider.fetch_index_bars.assert_not_called()
            self.store.store_bytes.assert_not_called()
            self.repository.save_manifest.assert_not_called()

    def test_valid_windows_reach_exact_fetch(self):
        for start, snapshot in [(date(2026, 9, 1), date(2026, 9, 24)),
                                (date(2026, 9, 24), date(2026, 9, 25))]:
            with self.subTest(start=start, snapshot=snapshot):
                self.provider.reset_mock()
                self.provider.fetch_index_bars.side_effect = RuntimeError('fetch sentinel')
                with self.assertRaisesRegex(RuntimeError, 'fetch sentinel'):
                    self.ingestor.ingest(
                        provider=self.provider, index_name='CASE30',
                        **dict(self.window, start_date=start, snapshot_date=snapshot))
                self.provider.fetch_index_bars.assert_called_once_with(
                    index_name='CASE30', start_date=start,
                    end_date=self.window['end_date'], page_size=1000)
                self.store.store_bytes.assert_not_called()
                self.repository.save_manifest.assert_not_called()


class IndexIngestionCountTests(unittest.TestCase):
    def setUp(self):
        from dataclasses import dataclass
        from types import SimpleNamespace
        self.ns = SimpleNamespace
        namespace = load_classes('app/data/index_ingestion.py',
                                 {'IndexHistoryIngestor', 'IndexIngestionResult'},
                                 {'date': date, 'dataclass': dataclass,
                                  'DataAssetType': SimpleNamespace(INDEX_BARS='index'),
                                  'BarGranularity': SimpleNamespace(D1='daily'),
                                  'IngestionStatus': SimpleNamespace(RECEIVED='received')})
        self.store, self.repository, self.provider = Mock(), Mock(), Mock()
        self.provider.name = "fixture_index_source"
        self.ingestor = namespace['IndexHistoryIngestor'](
            raw_store=self.store, repository=self.repository)

    def ingest(self, total, counts):
        self.provider.fetch_index_bars.return_value = self.ns(
            record_count=total, metadata={}, responses=tuple(self.ns(
                record_count=count, payload=b'fixture', filename='fixture.json',
                source_uri=None, metadata={}) for count in counts))
        return self.ingestor.ingest(
            provider=self.provider, index_name='CASE30',
            start_date=date(2026, 9, 1), end_date=date(2026, 9, 24),
            snapshot_date=date(2026, 9, 24))

    def test_invalid_batch_counts_never_persist(self):
        for total in (None, True, False, -1, 1.0, '1'):
            with self.subTest(total=total), self.assertRaises(ValueError):
                self.ingest(total, [total])
            self.store.store_bytes.assert_not_called()
            self.repository.save_manifest.assert_not_called()

    def test_invalid_later_page_never_persists_earlier_page(self):
        for total, counts in ((1, [1, None]), (2, [1, True]), (1, [1, False]),
                              (2, [1, 1.0]), (0, [1, -1]), (2, [1, '1'])):
            with self.subTest(counts=counts), self.assertRaises(ValueError):
                self.ingest(total, counts)
            self.store.store_bytes.assert_not_called()
            self.repository.save_manifest.assert_not_called()

    def test_mismatch_never_persists(self):
        with self.assertRaisesRegex(ValueError, 'count mismatch'):
            self.ingest(3, [1, 1])
        self.store.store_bytes.assert_not_called()
        self.repository.save_manifest.assert_not_called()

    def test_exact_nonnegative_counts_preserved(self):
        for counts in ([], [0], [2, 0, 1]):
            with self.subTest(counts=counts):
                self.store.reset_mock()
                self.repository.reset_mock()
                result = self.ingest(sum(counts), counts)
                self.assertEqual(result.record_count, sum(counts))
                self.assertEqual(len(result.manifests), len(counts))
                self.assertEqual(self.repository.save_manifest.call_count, len(counts))
                self.assertEqual([c.kwargs['record_count'] for c in
                                  self.store.store_bytes.call_args_list], counts)


class IndexIngestionIdentityTests(IndexIngestionCountTests):
    def test_invalid_source_identity_rejected_before_fetch(self):
        for name in (None, True, 1, '', ' source', 'source ', 'SOURCE', 'canonical'):
            with self.subTest(name=name):
                self.provider.name = name
                with self.assertRaisesRegex(ValueError, 'provider name'):
                    self.ingest(1, [1])
                self.provider.fetch_index_bars.assert_not_called()
                self.store.store_bytes.assert_not_called()
                self.repository.save_manifest.assert_not_called()

    def test_identity_change_during_fetch_rejected_before_persistence(self):
        def fetch(**kwargs):
            self.provider.name = 'other_source'
            return self.provider.fetch_index_bars.return_value
        self.provider.fetch_index_bars.side_effect = fetch
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            self.ingest(2, [1, 1])
        self.store.store_bytes.assert_not_called()
        self.repository.save_manifest.assert_not_called()

    def test_storage_callback_cannot_relabel_later_pages_or_result(self):
        original = self.provider.name
        def save(manifest, **kwargs):
            self.provider.name = 'other_source'
            return manifest
        self.repository.save_manifest.side_effect = save
        result = self.ingest(2, [1, 1])
        self.assertEqual(result.provider, original)
        self.assertEqual([call.kwargs['provider'] for call in
                          self.store.store_bytes.call_args_list], [original, original])
