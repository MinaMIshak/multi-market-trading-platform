"""Unified read-only product views over operational receipts, never legacy signals."""
from html import escape
from urllib.parse import urlencode

from app.ui.operational import render_operational

SECTIONS = ('TODAY', 'LIVE', 'PRE-SURGE', 'SWING', 'PERFORMANCE', 'RESEARCH', 'SYSTEM')
MARKETS = ('EGX', 'US', 'ALL')


def product_state(operational, market='ALL', section='TODAY'):
    if market not in MARKETS or section not in SECTIONS:
        raise ValueError('unknown product view')
    # Only EGX has a connected operational receipt reader. Do not imply US coverage.
    egx = {key: operational[key] for key in ('configured', 'available', 'status')}
    egx['symbols'] = [dict(item) for item in operational['symbols']
                      if item.get('market') == 'EGX'] if egx['available'] else []
    us = {'configured': False, 'available': False, 'status': 'UNKNOWN', 'symbols': []}
    markets = {'EGX': egx, 'US': us}
    selected = MARKETS[:2] if market == 'ALL' else (market,)
    return {
        'market': market, 'section': section, 'mode': 'PAPER/SHADOW ONLY',
        'live': 'DISABLED', 'markets': {key: markets[key] for key in selected},
        'coverage': {key: {'universe': None, 'data_ready': None, 'scanned': None,
                           'candidates': None,
                           'observed_symbols': len(markets[key]['symbols'])
                           if markets[key]['available'] else None}
                     for key in selected},
        'performance': None,
    }


def render_product(state):
    market, section = state['market'], state['section']
    def link(label, selected_market, selected_section):
        query = escape(urlencode({'market': selected_market, 'section': selected_section}), quote=True)
        current = ' aria-current="page"' if (selected_market, selected_section) == (market, section) else ''
        return f'<a href="/?{query}"{current}>{label}</a>'
    nav = '<nav aria-label="Product sections">' + ' '.join(link(s, market, s) for s in SECTIONS) + '</nav>'
    nav += '<nav aria-label="Markets">' + ' '.join(link(m, m, section) for m in MARKETS) + '</nav>'
    content = f'<h1>{section} · {market}</h1><p>LIVE MONEY DISABLED · Candidate != fill</p>'
    for key, value in state['markets'].items():
        observed = state['coverage'][key]['observed_symbols']
        content += (f'<article><h2>{key}</h2><p>Status: {escape(str(value["status"]))}</p>'
                    f'<p>Observed symbols: {observed if observed is not None else "UNKNOWN"}. '
                    'Universe / data-ready / scanned / candidates: UNKNOWN.</p></article>')
    if section in ('TODAY', 'SWING'):
        content += '<p>Verified operational receipts only. Observed symbols do not establish scan coverage.</p>'
        for value in state['markets'].values():
            content += render_operational(value, fragment=True)
    else:
        messages = {
            'LIVE': 'Live scanner activity UNKNOWN. No scheduler execution evidence is connected to this view.',
            'PRE-SURGE': 'Pre-surge opportunities UNKNOWN. No validated operational feed is connected.',
            'PERFORMANCE': 'Performance UNKNOWN. Authentic Paper/Shadow lifecycle evidence is required.',
            'RESEARCH': 'Research results do not authorize operational use.',
            'SYSTEM': 'Open SYSTEM for the detailed operator checkpoint and capability blockers.',
        }
        content += '<p>' + messages[section] + '</p>'
    content += ('<footer><a href="/system">SYSTEM details</a> · '
                '<a href="/shadow">Audited collection</a> · '
                '<a href="/performance">Performance evidence</a></footer>')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>EGX + US Paper/Shadow</title><style>'
            'body{background:#071019;color:#e9f0f5;font:16px Arial,sans-serif;max-width:1100px;margin:auto;padding:24px}'
            'nav{display:flex;flex-wrap:wrap;gap:16px;margin:20px 0}a{color:#8bd5b0}'
            '[aria-current]{font-weight:bold;text-decoration-thickness:3px}'
            'article{background:#0d1b26;border:1px solid #1e3241;border-radius:10px;padding:20px;margin:20px 0}'
            'p{line-height:1.6}footer{margin-top:32px}</style></head><body>'
            + nav + '<main>' + content + '</main></body></html>')
