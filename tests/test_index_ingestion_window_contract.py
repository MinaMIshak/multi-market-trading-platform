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
