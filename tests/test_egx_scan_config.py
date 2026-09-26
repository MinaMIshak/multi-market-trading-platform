"""Configuration fixtures; mocked decoding is not actual verifier evidence."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.egx_scan_config import load_scan_configuration
from app.egx_scan import scan_egx_scope


class ScanConfigurationTests(unittest.TestCase):
    def envelope(self):
        return dict(schema_version='egx-explicit-scan-v1', scope_reference='fixture',
                    symbols=['COMI', 'EAST', 'FWRY'], launch_inputs={})

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
        raw = self.envelope() | {'launch_inputs': {'COMI': {}, 'EAST': {}, 'FWRY': {}}}
        source = SimpleNamespace(symbol='COMI')
        with patch('app.egx_scan_config._read', return_value=raw), patch(
                'app.egx_scan_config._decode_launch', side_effect=[
                    source, ValueError('private content'), SimpleNamespace(symbol='COMI')]):
            config = load_scan_configuration('/tmp/fixture.json')
        self.assertEqual(config.symbols, ('COMI', 'EAST', 'FWRY'))
        self.assertEqual(config.sources, {'COMI': source})
        self.assertEqual(config.source_errors, {'EAST': 'INVALID_LAUNCH_EVIDENCE',
                                               'FWRY': 'LAUNCH_IDENTITY_MISMATCH'})
        with patch('app.egx_scan._verify', side_effect=ValueError('private content')):
            report = scan_egx_scope(symbols=config.symbols, sources=config.sources,
                                   source_errors=config.source_errors,
                                   scope_reference=config.scope_reference,
                                   database=None, data_root='/tmp')
        self.assertEqual(report['scanned'], 0)
        self.assertEqual(report['symbols'][1]['reason'], 'launch evidence failed contract validation')
        self.assertEqual(report['symbols'][2]['reason'], 'launch identity does not match requested symbol')
        self.assertNotIn('private content', str(report))

    def test_invalid_envelopes_fail_before_decode(self):
        valid = self.envelope()
        cases = [None, [], valid | {'symbols': []}, valid | {'symbols': ['COMI', 'COMI']},
                 valid | {'symbols': [' A']}, valid | {'symbols': [None]},
                 valid | {'scope_reference': ''}, valid | {'schema_version': 'future'},
                 valid | {'launch_inputs': {'OTHER': {}}}, valid | {'extra': True}]
        cases.extend(valid | {'symbols': [symbol]} for symbol in
                     ('comi', 'A/B', 'A\n', 'Ａ', 'A' * 65))
        for raw in cases:
            with self.subTest(raw=raw), patch('app.egx_scan_config._read', return_value=raw), patch(
                    'app.egx_scan_config._decode_launch') as decode:
                with self.assertRaises(ValueError):
                    load_scan_configuration('/tmp/fixture.json')
                decode.assert_not_called()

    def test_additional_symbols_reach_decoder_and_preserve_evidence_failures(self):
        raw = self.envelope() | {'symbols': ['FIXTURE', 'OTHER'],
                                 'launch_inputs': {'FIXTURE': {}, 'OTHER': {}}}
        source = SimpleNamespace(symbol='FIXTURE')
        with patch('app.egx_scan_config._read', return_value=raw), patch(
                'app.egx_scan_config._decode_launch', side_effect=[source, ValueError('invalid')]) as decode:
            config = load_scan_configuration('/tmp/fixture.json')
        self.assertEqual(decode.call_count, 2)
        self.assertEqual(config.sources, {'FIXTURE': source})
        self.assertEqual(config.source_errors, {'OTHER': 'INVALID_LAUNCH_EVIDENCE'})
        with patch('app.egx_scan._verify', side_effect=ValueError('missing evidence')) as verify:
            report = scan_egx_scope(symbols=config.symbols, sources=config.sources,
                                   source_errors=config.source_errors,
                                   scope_reference=config.scope_reference,
                                   database=None, data_root='/tmp')
        verify.assert_called_once()
        self.assertEqual(report['requested'], 2)
        self.assertEqual(report['scanned'], 0)
        self.assertEqual(report['status_counts']['EVIDENCE_BLOCKED'], 2)
        self.assertEqual(report['symbols'][1]['reason'], 'launch evidence failed contract validation')

    def test_relative_path_rejected_without_read(self):
        with patch('app.egx_scan_config._read') as read:
            with self.assertRaises(ValueError):
                load_scan_configuration('fixture.json')
            read.assert_not_called()
