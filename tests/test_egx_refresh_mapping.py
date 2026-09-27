"""Engineering aliases only; no market or source-rights evidence."""
import unittest
from unittest.mock import Mock
from uuid import UUID

from app.egx_refresh_mapping import require_refresh_mapping


class RefreshMappingTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(instrument_id=str(UUID(int=1)), canonical_ticker='FIXTURE',
                        instrument_type='EQUITY', matched_provider='fixture',
                        matched_alias_value='EXPLICIT-CODE')
        self.resolver = Mock()
        self.resolver.resolve.return_value = self.row

    def admit(self, **changes):
        return require_refresh_mapping(self.resolver, **(dict(
            canonical_symbol='FIXTURE', provider_symbol='EXPLICIT-CODE',
            provider_name='fixture', symbol='FIXTURE',
            instrument_id=UUID(int=1)) | changes))

    def test_explicit_code_resolves_in_exact_provider_namespace(self):
        self.admit()
        self.resolver.resolve.assert_called_once_with('EXPLICIT-CODE', provider='fixture')

    def test_invalid_request_rejected_before_resolution(self):
        for change in ({'provider_symbol': None}, {'provider_symbol': ' code '}, {'symbol': 'OTHER'},
                       *({'provider_name': name} for name in (None, '', ' fixture', 'FIXTURE', 'canonical'))):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.admit(**change)
        self.resolver.resolve.assert_not_called()

    def test_unavailable_and_ambiguous_aliases_fail_closed(self):
        for error in (KeyError('missing'), ValueError('ambiguous')):
            self.resolver.resolve.side_effect = error
            with self.assertRaisesRegex(ValueError, 'unavailable or ambiguous'):
                self.admit()

    def test_wrong_identity_classification_or_provider_rejected(self):
        for change in ({'instrument_id': str(UUID(int=2))}, {'canonical_ticker': 'OTHER'},
                       {'instrument_type': 'INDEX'}, {'matched_provider': 'other'},
                       {'matched_alias_value': 'OTHER'}, {'matched_alias_value': None}):
            self.resolver.resolve.return_value = self.row | change
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.admit()

    def test_missing_master_identity_rejected(self):
        for row in (None, {}, []):
            self.resolver.resolve.return_value = row
            with self.assertRaises(ValueError):
                self.admit()
