"""Identity admission fixtures, never market membership evidence."""
import unittest
from uuid import UUID

from app.egx_scope import require_equity_identity, valid_scope_symbol


class EquityIdentityTests(unittest.TestCase):
    def master(self):
        return dict(instrument_id=str(UUID(int=1)), canonical_ticker='COMI',
                    instrument_type='EQUITY')

    def admit(self, resolved, **overrides):
        require_equity_identity(resolved, **(dict(symbol='COMI', instrument_id=UUID(int=1))
                                           | overrides))

    def test_matching_equity_accepts_uuid_and_stored_text(self):
        self.admit(self.master())
        self.admit(self.master(), instrument_id=str(UUID(int=1)))

    def test_non_equity_or_missing_classification_rejected(self):
        for classification in ('INDEX', 'UNKNOWN', None, '', 'equity', ' EQUITY', True):
            with self.subTest(classification=classification):
                with self.assertRaisesRegex(ValueError, 'equity classification required'):
                    self.admit(self.master() | {'instrument_type': classification})
        row = self.master()
        del row['instrument_type']
        with self.assertRaisesRegex(ValueError, 'equity classification required'):
            self.admit(row)

    def test_matching_malformed_identifiers_are_not_identity_evidence(self):
        for identifier in ('', 'not-a-uuid', '42', 42, True, [], {}):
            with self.subTest(identifier=identifier), self.assertRaisesRegex(
                    ValueError, 'identity mismatch'):
                self.admit(self.master() | {'instrument_id': identifier},
                           instrument_id=identifier)

    def test_stringifiable_objects_cannot_impersonate_uuid(self):
        class Identifier:
            def __str__(self):
                return str(UUID(int=1))

        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            self.admit(self.master(), instrument_id=Identifier())
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            self.admit(self.master() | {'instrument_id': Identifier()})

    def test_wrong_or_missing_identity_rejected(self):
        cases = [None, [], {}, self.master() | {'instrument_id': str(UUID(int=2))},
                 self.master() | {'canonical_ticker': 'EAST'},
                 self.master() | {'canonical_ticker': ' COMI'}]
        for key in ('instrument_id', 'canonical_ticker'):
            row = self.master()
            del row[key]
            cases.append(row)
        for row in cases:
            with self.subTest(row=row), self.assertRaisesRegex(ValueError, 'identity mismatch'):
                self.admit(row)

    def test_missing_requested_identity_does_not_match_missing_master_value(self):
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            self.admit(self.master() | {'instrument_id': None}, instrument_id=None)
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            self.admit(self.master() | {'canonical_ticker': 'comi'}, symbol='comi')


class ScopeSymbolTests(unittest.TestCase):
    def test_broad_spelling_without_membership_inference(self):
        for symbol in ('FIXTURE', 'OTHER.A', 'TEST_1', 'A-B', 'A' * 64):
            with self.subTest(symbol=symbol):
                self.assertTrue(valid_scope_symbol(symbol))
        for symbol in ('comi', ' COMI', 'COMI\n', 'A/B', '', 'A' * 65, 1, None):
            with self.subTest(symbol=symbol):
                self.assertFalse(valid_scope_symbol(symbol))
