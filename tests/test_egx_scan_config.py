"""Configuration fixtures; mocked decoding is not actual verifier evidence."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.egx_scan_config import load_scan_configuration
from app.egx_scan import scan_egx_scope


class ScanConfigurationTests(unittest.TestCase):
    def envelope(self):
        return dict(schema_version='egx-explicit-scan-v1', scope_reference='fixture',
                    symbols=['A', 'B', 'C'], launch_inputs={})

    def test_missing_inputs_remain_in_scope_and_blocked(self):
        with patch('app.egx_scan_config._read', return_value=self.envelope()):
            config = load_scan_configuration('/tmp/fixture.json')
        with patch('app.egx_scan._verify') as verify:
            result = scan_egx_scope(symbols=config.symbols, sources=config.sources,
                                    scope_reference=config.scope_reference,
                                    database=None, data_root='/tmp')
        verify.assert_not_called()
        self.assertEqual(result['requested'], 3)
        self.assertEqual(result['scanned'], 0)
        self.assertEqual(result['status_counts']['EVIDENCE_BLOCKED'], 3)

    def test_invalid_and_mismatched_documents_do_not_drop_symbols(self):
        raw = self.envelope() | {'launch_inputs': {'A': {}, 'B': {}, 'C': {}}}
        source = SimpleNamespace(symbol='A')
        with patch('app.egx_scan_config._read', return_value=raw), patch(
                'app.egx_scan_config._decode_launch', side_effect=[
                    source, ValueError('private content'), SimpleNamespace(symbol='A')]):
            config = load_scan_configuration('/tmp/fixture.json')
        self.assertEqual(config.symbols, ('A', 'B', 'C'))
        self.assertEqual(config.sources, {'A': source})

    def test_invalid_envelopes_fail_before_decode(self):
        valid = self.envelope()
        cases = [None, [], valid | {'symbols': []}, valid | {'symbols': ['A', 'A']},
                 valid | {'symbols': [' A']}, valid | {'symbols': [None]},
                 valid | {'scope_reference': ''}, valid | {'schema_version': 'future'},
                 valid | {'launch_inputs': {'OTHER': {}}}, valid | {'extra': True}]
        for raw in cases:
            with self.subTest(raw=raw), patch('app.egx_scan_config._read', return_value=raw), patch(
                    'app.egx_scan_config._decode_launch') as decode:
                with self.assertRaises(ValueError):
                    load_scan_configuration('/tmp/fixture.json')
                decode.assert_not_called()

    def test_relative_path_rejected_without_read(self):
        with patch('app.egx_scan_config._read') as read:
            with self.assertRaises(ValueError):
                load_scan_configuration('fixture.json')
            read.assert_not_called()
