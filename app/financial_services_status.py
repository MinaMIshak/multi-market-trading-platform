"""Recorded evaluation of anthropics/financial-services; research methodology only.

This is a static record of a read-only review of a pinned upstream copy, not a
live connector check. No plugin is installed and no connector is enabled: every
upstream MCP connector is a credentialed commercial service and no credentials
or entitlements exist for this platform. Nothing here can trigger execution.
"""
import copy

_CONNECTORS = ('daloopa', 'morningstar', 'sp-global', 'factset', 'moodys', 'mtnewswire',
               'aiera', 'lseg', 'pitchbook', 'chronograph', 'egnyte', 'box')

_STATUS = {
    'status': 'METHODOLOGY_REFERENCE_ONLY',
    'layer': 'RESEARCH_ONLY',
    'execution_authority': 'NONE',
    'upstream': {
        'repository': 'https://github.com/anthropics/financial-services',
        'commit': '574ed3624aebd0418c7e96cd101262f30210ab26',
        'committed_at': '2026-09-21T22:10:41+01:00',
        'license': 'Apache-2.0',
        'evaluated_on': '2026-09-28',
        # Re-check only: upstream HEAD still equals the pinned commit and the
        # recorded plugin versions, invalid MCP JSON and connector list match.
        'rechecked_on': '2026-09-29',
        'recheck_result': 'UPSTREAM_HEAD_UNCHANGED',
    },
    'plugins': [
        {'name': 'financial-analysis', 'version': '0.1.1', 'installed': False,
         'mcp_config': 'INVALID_JSON_UPSTREAM', 'hooks': 'EMPTY'},
        {'name': 'equity-research', 'version': '0.1.2', 'installed': False,
         'mcp_config': 'NONE', 'hooks': 'EMPTY'},
        {'name': 'market-researcher', 'version': '0.1.1', 'installed': False,
         'mcp_config': 'NONE', 'hooks': 'NONE'},
    ],
    'connectors': [
        {'name': name, 'status': 'FAIL_CLOSED', 'enabled': False,
         'credentials_configured': False,
         'reason': 'credentialed commercial service; no entitlement configured'}
        for name in _CONNECTORS
    ],
    'egx_coverage': 'UNKNOWN',
    'data_policy': 'Platform-owned EGX data and provenance take precedence; '
                   'upstream skills are prompt methodology, not data sources.',
}


def financial_services_status():
    return copy.deepcopy(_STATUS)
