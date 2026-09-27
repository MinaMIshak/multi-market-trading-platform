"""Offline artificial UI contracts; no market or runtime evidence."""
import ast
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from app.ui.operational import load_operational_state
from app.ui.product import MARKETS, SECTIONS, product_state, render_product


class ProductShellContracts(unittest.TestCase):
    def setUp(self):
        self.source = {'configured': True, 'available': True, 'status': 'OPERATIONAL',
                       'symbols': [{'market': 'EGX', 'symbol': '<fixture>',
                                    'status': 'WATCH', 'trade_plan': {
                                        'entry_low': 1, 'entry_high': 2,
                                        'stop_price': 0.5, 'target_1': 3}}]}

    def test_all_view_preserves_market_separation_and_unknown_counts(self):
        state = product_state(self.source)
        self.assertEqual(state['markets']['EGX']['symbols'], self.source['symbols'])
        self.assertEqual(state['markets']['US']['status'], 'UNKNOWN')
        self.assertIsNone(state['coverage']['US']['observed_symbols'])
        self.assertEqual(state['coverage']['EGX']['observed_symbols'], 1)
        for counts in state['coverage'].values():
            for key in ('universe', 'data_ready', 'scanned', 'candidates'):
                self.assertIsNone(counts[key])
        self.assertIsNone(state['performance'])

    def test_market_filter_does_not_leak_egx_into_us(self):
        state = product_state(self.source, 'US')
        self.assertEqual(set(state['markets']), {'US'})
        self.assertNotIn('fixture', render_product(state))
        self.assertNotIn('Entry band:', render_product(state))

    def test_all_sections_and_markets_render_navigation_and_safety(self):
        for section in SECTIONS:
            for market in MARKETS:
                with self.subTest(section=section, market=market):
                    body = render_product(product_state(self.source, market, section))
                    self.assertEqual(body.count('<html'), 1)
                    self.assertEqual(body.count('aria-current="page"'), 2)
                    self.assertIn('LIVE MONEY DISABLED', body)
                    self.assertIn('Candidate != fill', body)
                    self.assertIn('href="/system"', body)
                    self.assertEqual('Entry band:' in body,
                                     section in ('TODAY', 'SWING') and market != 'US')

    def test_receipt_text_is_escaped(self):
        body = render_product(product_state(self.source))
        self.assertNotIn('<fixture>', body)
        self.assertIn('&lt;fixture&gt;', body)

    def test_stale_or_blocked_receipts_never_display_plan(self):
        for status in ('DATA_STALE', 'EVIDENCE_BLOCKED', 'NOT_READY'):
            self.source['symbols'][0]['status'] = status
            body = render_product(product_state(self.source))
            self.assertIn(status, body)
            self.assertNotIn('Entry band:', body)

    def test_unavailable_source_suppresses_rows(self):
        self.source['available'] = False
        state = product_state(self.source)
        self.assertEqual(state['markets']['EGX']['symbols'], [])
        self.assertIsNone(state['coverage']['EGX']['observed_symbols'])

    def test_disconnected_runtime_is_not_zero_coverage(self):
        with patch.dict('os.environ', {}, clear=True):
            state = product_state(load_operational_state())
        self.assertEqual(state['markets']['EGX']['status'], 'NOT_READY')
        self.assertIsNone(state['coverage']['EGX']['observed_symbols'])

    def test_no_input_mutation_or_foreign_market_admission(self):
        self.source['symbols'].append({'market': 'US', 'symbol': 'foreign'})
        before = deepcopy(self.source)
        state = product_state(self.source)
        render_product(state)
        self.assertEqual(self.source, before)
        self.assertEqual(len(state['markets']['EGX']['symbols']), 1)

    def test_invalid_selectors_fail_closed(self):
        for market, section in [('EU', 'TODAY'), ('ALL', '<script>'), ('egx', 'TODAY')]:
            with self.assertRaises(ValueError):
                product_state(self.source, market, section)

    def test_actual_route_functions_use_only_operational_truth(self):
        # Execute complete route bodies without importing unavailable FastAPI dependencies.
        tree = ast.parse((Path(__file__).parents[1] / 'app/main.py').read_text())
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                     and n.name in ('root', 'product')]
        for node in functions:
            node.decorator_list = []
        class HttpError(Exception):
            def __init__(self, **kwargs):
                self.status_code = kwargs['status_code']
        namespace = {'HTMLResponse': str, 'HTTPException': HttpError,
                     'load_operational_state': lambda: self.source,
                     'product_state': product_state, 'render_product': render_product}
        exec(compile(ast.Module(body=functions, type_ignores=[]), 'routes', 'exec'), namespace)
        self.assertIn('Entry band:', namespace['root']())
        self.assertNotIn('fixture', namespace['root']('US'))
        with self.assertRaises(HttpError) as error:
            namespace['product']('invalid')
        self.assertEqual(error.exception.status_code, 422)
