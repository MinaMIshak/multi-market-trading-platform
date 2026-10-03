"""Offline artificial UI contracts; no market or runtime evidence."""
import ast
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from app.ui.operational import load_operational_state
from app.ui.product import MARKETS, SECTIONS, product_state, render_product, RECEIPT_STATUSES


class ProductShellContracts(unittest.TestCase):
    def setUp(self):
        self.source = {'configured': True, 'available': True, 'status': 'OPERATIONAL',
                       'symbols': [{'market': 'EGX', 'symbol': '<fixture>',
                                    'status': 'WATCH', 'trade_plan': {
                                        'entry_low': 1, 'entry_high': 2,
                                        'stop_price': 0.5, 'target_1': 3}}]}
        self.identities = {'total_instruments': 319,
                           'by_type': {'EQUITY': 312, 'INDEX': 7},
                           'source_providers': ['egid'],
                           'latest_snapshot_updated_at': '2026-09-26T07:59:16+00:00',
                           'latest_source_market_date': None}

    DAILY = [{'canonical_symbol': 'COMI', 'provider': 'tradingview_tvdatafeed',
              'source_snapshot_date': '2026-09-24', 'oldest_market_date': '2025-01-27',
              'newest_market_date': '2026-09-24', 'valid_bar_count': 400,
              'quarantined_bar_count': 0, 'freshness': 'UNKNOWN'}]
    BLOCKED = [dict(DAILY[0], source_status='EVIDENCE_BLOCKED',
                    source_reason='source entitlement not established',
                    source_delay='UNKNOWN')]

    def test_daily_observations_are_egx_only_and_never_coverage(self):
        state = product_state(self.source, daily_observations=self.DAILY)
        self.assertEqual(state['daily_observations'], {'EGX': self.BLOCKED, 'US': None})
        for counts in state['coverage'].values():
            for key in ('universe', 'data_ready', 'scanned', 'candidates'):
                self.assertIsNone(counts[key])
        us = product_state(self.source, 'US', daily_observations=self.DAILY)
        self.assertEqual(us['daily_observations'], {'US': None})
        self.assertNotIn('tradingview', render_product(us))

    def test_daily_observations_render_dated_with_freshness_not_established(self):
        for section in ('TODAY', 'SWING'):
            with self.subTest(section=section):
                page = render_product(product_state(
                    self.source, section=section, daily_observations=self.DAILY))
                self.assertIn('EGX validated daily observations', page)
                self.assertIn('<td>COMI</td>', page)
                self.assertIn('tradingview_tvdatafeed', page)
                self.assertIn('2025-01-27 to 2026-09-24', page)
                self.assertIn('<td>400</td>', page)
                self.assertIn('<td>UNKNOWN</td>', page)
                self.assertIn('only from VERIFIED exchange sessions', page)
                self.assertIn('UNKNOWN means freshness NOT ESTABLISHED', page)
                self.assertIn('usage rights NOT ESTABLISHED', page)
                self.assertIn('<td>EVIDENCE_BLOCKED</td>', page)
                self.assertIn('<td>source entitlement not established</td>', page)
                self.assertNotIn('<td>ADMITTED</td>', page)
                self.assertIn('Not a signal, candidate or fill', page)
                self.assertIn('US validated daily observations: UNKNOWN', page)
        live = render_product(product_state(
            self.source, section='LIVE', daily_observations=self.DAILY))
        # LIVE never presents daily observations as real-time or as the TODAY table.
        self.assertNotIn('EGX validated daily observations', live)
        self.assertIn('EGX near-current feed: UNAVAILABLE', live)
        self.assertIn('Nothing below is real-time', live)
        self.assertLess(live.index('Nothing below is real-time'),
                        live.index('tradingview_tvdatafeed'))

    def test_daily_observations_unknown_and_empty_are_distinct(self):
        unknown = render_product(product_state(self.source, market='EGX'))
        self.assertIn('EGX validated daily observations: UNKNOWN', unknown)
        empty = render_product(product_state(self.source, market='EGX', daily_observations=[]))
        self.assertIn('EGX validated daily observations: none recorded', empty)
        self.assertNotIn('EGX validated daily observations: UNKNOWN', empty)

    def test_daily_observation_source_status_comes_only_from_registry(self):
        claims = [dict(self.DAILY[0], source_status='ADMITTED', source_reason='trust me'),
                  dict(self.DAILY[0], canonical_symbol='SWDY', provider='unknown_feed'),
                  dict(self.DAILY[0], canonical_symbol='EAST', provider='eodhd')]
        rows = product_state(self.source, daily_observations=claims)['daily_observations']['EGX']
        self.assertEqual([r['source_status'] for r in rows], ['EVIDENCE_BLOCKED'] * 3)
        self.assertEqual([r['source_reason'] for r in rows],
                         ['source entitlement not established',
                          'undeclared daily source for market',
                          'paid subscription source not admissible'])
        page = render_product(product_state(self.source, daily_observations=claims))
        self.assertNotIn('ADMITTED</td>', page.replace('EVIDENCE_BLOCKED', ''))
        self.assertNotIn('trust me', page)
        # Caller-supplied rows are not mutated.
        self.assertEqual(claims[0]['source_status'], 'ADMITTED')

    def test_daily_observation_render_fails_closed_without_source_status(self):
        from app.ui.product import render_daily_observations
        page = render_daily_observations({'EGX': self.DAILY})
        self.assertIn('<td>EVIDENCE_BLOCKED</td>', page)
        self.assertIn('<td>source admission unavailable</td>', page)

    def test_daily_observation_text_is_escaped(self):
        hostile = [dict(self.DAILY[0], canonical_symbol='<b>X</b>', provider='<i>p</i>')]
        page = render_product(product_state(self.source, daily_observations=hostile))
        self.assertNotIn('<b>X</b>', page)
        self.assertNotIn('<i>p</i>', page)
        self.assertIn('&lt;b&gt;X&lt;/b&gt;', page)

    def test_egx_identity_summary_is_egx_only_and_never_coverage(self):
        state = product_state(self.source, security_master=self.identities)
        self.assertEqual(state['identities'], {'EGX': self.identities, 'US': None})
        for counts in state['coverage'].values():
            for key in ('universe', 'data_ready', 'scanned', 'candidates'):
                self.assertIsNone(counts[key])
        us = product_state(self.source, 'US', security_master=self.identities)
        self.assertEqual(us['identities'], {'US': None})
        self.assertNotIn('EQUITY: 312', render_product(us))

    def test_identity_summary_renders_in_today_and_swing_with_disclaimer(self):
        for section in ('TODAY', 'SWING'):
            with self.subTest(section=section):
                page = render_product(product_state(
                    self.source, section=section, security_master=self.identities))
                self.assertIn('EGX security-master identities', page)
                self.assertIn('319 identity records', page)
                self.assertIn('EQUITY: 312', page)
                self.assertIn('INDEX: 7', page)
                self.assertIn('egid', page)
                self.assertIn('NOT price', page)
                self.assertIn('NOT a trading signal', page)
                self.assertIn('NOT dated exchange membership', page)
                self.assertIn('Latest source market date: UNKNOWN', page)
                self.assertNotIn('None', page)
                self.assertIn('US security-master identities: UNKNOWN', page)
        page = render_product(product_state(
            self.source, section='LIVE', security_master=self.identities))
        self.assertNotIn('EQUITY: 312', page)

    def test_missing_identity_summary_is_unknown_not_zero(self):
        page = render_product(product_state(self.source, market='EGX'))
        self.assertIn('EGX security-master identities: UNKNOWN', page)
        self.assertNotIn('0 identity records', page)

    def test_identity_summary_text_is_escaped(self):
        hostile = dict(self.identities, by_type={'<b>EQUITY</b>': 1},
                       source_providers=['<i>egid</i>'],
                       latest_snapshot_updated_at='<s>t</s>')
        page = render_product(product_state(self.source, security_master=hostile))
        for raw in ('<b>EQUITY</b>', '<i>egid</i>', '<s>t</s>'):
            self.assertNotIn(raw, page)
        self.assertIn('&lt;b&gt;EQUITY&lt;/b&gt;', page)

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
                    self.assertIn('A candidate is not a fill', body)
                    self.assertIn('href="/system"', body)
                    self.assertEqual('Entry band:' in body,
                                     section in ('TODAY', 'SWING') and market != 'US')

    def test_operational_status_blocks_are_labelled_by_market(self):
        # ALL renders one status block per market; an unlabelled NOT_READY next to
        # an unlabelled UNKNOWN cannot be attributed to EGX or US by the reader.
        for section in ('TODAY', 'SWING'):
            with self.subTest(section=section):
                body = render_product(product_state(self.source, 'ALL', section))
                self.assertEqual(body.count('<h2>EGX operational Paper/Shadow</h2>'), 1)
                self.assertEqual(body.count('<h2>US operational Paper/Shadow</h2>'), 1)
                self.assertNotIn('<h2>Operational Paper/Shadow</h2>', body)
                us = render_product(product_state(self.source, 'US', section))
                self.assertIn('<h2>US operational Paper/Shadow</h2>', us)
                self.assertNotIn('EGX operational', us)

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

    def test_mixed_receipt_counts_are_scoped_and_do_not_infer_candidates(self):
        self.source['symbols'] = [dict(market='EGX', symbol=str(i), status=status)
                                  for i, status in enumerate(RECEIPT_STATUSES)]
        before = deepcopy(self.source)
        state = product_state(self.source)
        counts = state['coverage']['EGX']
        self.assertEqual(counts['observed_symbols'], 6)
        self.assertEqual(counts['status_counts'], dict.fromkeys(RECEIPT_STATUSES, 1))
        self.assertIsNone(counts['candidates'])
        self.assertIsNone(counts['scanned'])
        self.assertIsNone(counts['data_ready'])
        self.assertIsNone(state['coverage']['US']['status_counts'])
        for section in SECTIONS:
            body = render_product(product_state(self.source, section=section))
            self.assertIn('EGX observed receipt statuses', body)
            self.assertNotIn('US observed receipt statuses', body)
            self.assertIn('not scan coverage', body)
        self.assertEqual(self.source, before)

    def test_empty_available_and_unavailable_are_distinct(self):
        self.source['symbols'] = []
        counts = product_state(self.source)['coverage']['EGX']
        self.assertEqual(counts['observed_symbols'], 0)
        self.assertEqual(counts['status_counts'], dict.fromkeys(RECEIPT_STATUSES, 0))
        self.source['available'] = False
        counts = product_state(self.source)['coverage']['EGX']
        self.assertIsNone(counts['status_counts'])
        self.assertIsNone(counts['observed_symbols'])

    def test_duplicate_symbol_is_counted_once_as_blocked_in_any_order(self):
        rows = [dict(market='EGX', symbol='FIXTURE', status=status)
                for status in ('WATCH', 'READY_NO_SIGNAL', 'WATCH')]
        for order in (rows, list(reversed(rows))):
            self.source['symbols'] = order
            counts = product_state(self.source)['coverage']['EGX']
            self.assertEqual(counts['observed_symbols'], 1)
            self.assertEqual(counts['status_counts']['EVIDENCE_BLOCKED'], 1)
            self.assertEqual(counts['status_counts']['WATCH'], 0)

    def test_unknown_status_and_missing_identity_do_not_create_readiness(self):
        self.source['symbols'][0]['status'] = 'CANDIDATE'
        counts = product_state(self.source)['coverage']['EGX']
        self.assertEqual(counts['status_counts']['UNKNOWN'], 1)
        self.assertIsNone(counts['candidates'])
        for symbol in (None, '', ' '):
            self.source['symbols'][0]['symbol'] = symbol
            counts = product_state(self.source)['coverage']['EGX']
            self.assertIsNone(counts['observed_symbols'])
            self.assertIsNone(counts['status_counts'])

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
                     'load_scan_history': lambda: None,
                     'load_security_master_summary': lambda: self.identities,
                     'load_validated_daily_observations': lambda: self.DAILY,
                     'load_heartbeat': lambda: {'status': 'UNKNOWN'},
                     'load_ranking': lambda: None, 'load_context': lambda: None,
                     'load_macro': lambda: None, 'load_us_ranking': lambda: None,
                     'load_experiment': lambda: None,
                     'product_state': product_state, 'render_product': render_product}
        exec(compile(ast.Module(body=functions, type_ignores=[]), 'routes', 'exec'), namespace)
        self.assertIn('Entry band:', namespace['root']())
        self.assertNotIn('fixture', namespace['root']('US'))
        # Identity summary is routed to EGX only; the US view never reads it.
        self.assertIn('EQUITY: 312', namespace['root']())
        self.assertNotIn('EQUITY: 312', namespace['root']('US'))
        self.assertEqual(namespace['product']('ALL')['identities']['EGX'], self.identities)
        self.assertIn('tradingview_tvdatafeed', namespace['root']())
        self.assertNotIn('tradingview_tvdatafeed', namespace['root']('US'))
        self.assertEqual(namespace['product']('ALL')['daily_observations']['EGX'], self.BLOCKED)
        readiness = namespace['product']('ALL')['readiness']
        self.assertEqual(readiness['EGX']['scan_readiness'], 'EVIDENCE_BLOCKED')
        self.assertIsNone(readiness['US'])
        self.assertNotIn('readiness', str(namespace['product']('US')['readiness']['US']))
        with self.assertRaises(HttpError) as error:
            namespace['product']('invalid')
        self.assertEqual(error.exception.status_code, 422)
