"""anthropics/financial-services is recorded as fail-closed research methodology only."""
import unittest

from app.financial_services_status import financial_services_status
from app.ui.product import product_state, render_product

OPERATIONAL = {'configured': False, 'available': False, 'status': 'UNAVAILABLE',
               'symbols': [], 'observed_at': None}


class FinancialServicesStatusTest(unittest.TestCase):
    def test_pinned_upstream_and_no_execution_authority(self):
        status = financial_services_status()
        self.assertEqual(status['upstream']['commit'], '574ed3624aebd0418c7e96cd101262f30210ab26')
        self.assertEqual(status['upstream']['recheck_result'], 'UPSTREAM_HEAD_UNCHANGED')
        self.assertEqual(status['upstream']['rechecked_on'], '2026-09-29')
        self.assertEqual(status['execution_authority'], 'NONE')
        self.assertEqual(status['layer'], 'RESEARCH_ONLY')
        self.assertEqual(status['status'], 'METHODOLOGY_REFERENCE_ONLY')

    def test_every_connector_fails_closed_without_credentials(self):
        connectors = financial_services_status()['connectors']
        self.assertTrue(connectors)
        for row in connectors:
            self.assertEqual(row['status'], 'FAIL_CLOSED')
            self.assertFalse(row['enabled'])
            self.assertFalse(row['credentials_configured'])

    def test_evaluated_plugins_recorded_with_versions(self):
        plugins = {row['name']: row for row in financial_services_status()['plugins']}
        self.assertEqual(set(plugins), {'financial-analysis', 'equity-research', 'market-researcher'})
        self.assertEqual(plugins['financial-analysis']['mcp_config'], 'INVALID_JSON_UPSTREAM')
        for row in plugins.values():
            self.assertFalse(row['installed'])
            self.assertTrue(row['version'])

    def test_status_is_a_copy(self):
        first = financial_services_status()
        first['connectors'][0]['enabled'] = True
        self.assertFalse(financial_services_status()['connectors'][0]['enabled'])

    def test_research_section_exposes_status_and_renders_it(self):
        state = product_state(OPERATIONAL, 'EGX', 'RESEARCH')
        self.assertEqual(state['research']['financial_services']['execution_authority'], 'NONE')
        self.assertEqual(state['research']['notes'], None)
        page = render_product(state)
        self.assertIn('Financial Services integration: METHODOLOGY_REFERENCE_ONLY', page)
        self.assertIn('574ed36', page)
        self.assertIn('no trade execution authority', page)
        self.assertIn('Sourced research notes: UNKNOWN', page)

    def test_other_sections_do_not_carry_research_payload(self):
        self.assertNotIn('research', product_state(OPERATIONAL, 'EGX', 'TODAY'))


if __name__ == '__main__':
    unittest.main()
