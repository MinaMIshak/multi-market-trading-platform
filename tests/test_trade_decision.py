from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.strategies.contracts import Candidate
from app.strategies.trade_decision import DecisionSource, DecisionTerms, TradeDecision, trade_card


AT = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)


def fixture(market='EGX'):
    candidate = Candidate(strategy_id='fixture-challenger', strategy_version='1', config_id='fixture',
                          symbol='TEST', decision_time=AT, data_cutoff=AT-timedelta(minutes=1),
                          state='READY', entry_reference=None, stop_reference=None,
                          evidence_json='{"fixture":true}')
    source = DecisionSource(market=market, symbol='TEST', instrument_id=UUID(int=1),
                            source_id='fixture', artifact_sha256='a'*64, evidence_ids=('fixture-only',),
                            data_as_of=candidate.data_cutoff, available_at=AT,
                            fresh_until=AT+timedelta(hours=1), completed_bar=True)
    terms = DecisionTerms(entry_trigger='LONG_ENTRY_ZONE_TOUCH_V1', entry_low=Decimal('10'),
                          entry_high=Decimal('11'), entry_reference=Decimal('10.5'),
                          stop=Decimal('9'), target=Decimal('13'),
                          valid_until=AT+timedelta(days=1), max_holding_sessions=2)
    return dict(candidate=candidate, source=source, terms=terms)


def test_deterministic_immutable_serialization_and_market_separation():
    d = TradeDecision(**fixture())
    assert d.identity == TradeDecision(**fixture()).identity
    assert d == TradeDecision.model_validate_json(d.model_dump_json())
    assert d.identity != TradeDecision(**fixture('US')).identity
    for obj, key, value in ((d, 'live_money', True), (d.terms, 'stop', Decimal('8')),
                            (d.source, 'market', 'US'), (d.candidate, 'state', 'INVALIDATED')):
        with pytest.raises(ValidationError):
            setattr(obj, key, value)
    assert TradeDecision.model_json_schema()['properties']['live_money']['const'] is False


@pytest.mark.parametrize('section,key,value', [
    ('candidate', 'state', 'INVALIDATED'), ('candidate', 'state', 'WATCH'),
    ('candidate', 'execution_allowed', True), ('candidate', 'data_cutoff', AT+timedelta(seconds=1)),
    ('source', 'symbol', 'OTHER'), ('source', 'completed_bar', False),
    ('source', 'available_at', AT+timedelta(seconds=1)),
    ('source', 'fresh_until', AT), ('source', 'data_as_of', AT),
    ('source', 'artifact_sha256', ''), ('source', 'evidence_ids', ('',)),
    ('terms', 'stop', Decimal('12')), ('terms', 'target', Decimal('9')),
    ('terms', 'entry_low', Decimal('12')), ('terms', 'entry_reference', Decimal('NaN')),
    ('terms', 'valid_until', AT), ('terms', 'valid_until', AT.replace(tzinfo=None)),
    ('terms', 'max_holding_sessions', 0), ('terms', 'flat_by', AT),
])
def test_fails_closed_even_for_unchecked_nested_copies(section, key, value):
    args = fixture()
    args[section] = args[section].model_copy(update={key: value})
    with pytest.raises(ValidationError):
        TradeDecision(**args)


def test_card_labels_provenance_expiry_and_no_execution():
    d = TradeDecision(**fixture())
    before = d.model_dump_json()
    card = trade_card(d, observed_at=AT)
    assert card['observed_fact']['provenance']['artifact_sha256'] == 'a'*64
    assert card['strategy_inference']['setup'] == 'fixture-challenger'
    assert card['risk_decision'] == dict(status='NOT_EVALUATED', risk_per_share='1.5', event_risk='UNKNOWN')
    assert card['research_evidence']['confidence'] == 'UNVALIDATED'
    assert card['execution_allowed'] is card['live_money'] is False
    assert card['status'] == 'RESEARCH_CANDIDATE'
    assert trade_card(d, observed_at=AT+timedelta(hours=1))['invalidation_reason'] == 'DATA_STALE'
    assert trade_card(d, observed_at=AT+timedelta(days=1))['invalidation_reason'] == 'PLAN_EXPIRED'
    assert d.model_dump_json() == before
    with pytest.raises(ValueError):
        trade_card(d, observed_at=AT-timedelta(seconds=1))
    with pytest.raises(ValidationError):
        trade_card(d.model_copy(update={'live_money': True}), observed_at=AT)


def test_flat_by_and_required_fields():
    args = fixture()
    args['terms'] = args['terms'].model_copy(update={'flat_by': AT+timedelta(minutes=30)})
    d = TradeDecision(**args)
    assert trade_card(d, observed_at=AT+timedelta(minutes=30))['invalidation_reason'] == 'PLAN_EXPIRED'
    for field in ('source', 'terms', 'candidate'):
        with pytest.raises(ValidationError):
            TradeDecision(**{k:v for k,v in args.items() if k != field})


def test_card_renderer_escapes_untrusted_evidence_and_labels_sections():
    from app.ui.trade_card import render_trade_card
    args = fixture()
    args['candidate'] = args['candidate'].model_copy(update={'evidence_json': '{"note":"<script>bad</script>"}'})
    html = render_trade_card(TradeDecision(**args), observed_at=AT)
    assert '<script>' not in html
    assert '&lt;script&gt;' in html
    for label in ('Observed fact', 'Strategy inference', 'Risk decision', 'Research evidence',
                  'WHY FOUND', 'SETUP', 'DATA AS OF', 'TRIGGER', 'ENTRY', 'STOP', 'TARGET',
                  'RISK', 'EVIDENCE', 'CONFIDENCE', 'EVENT RISK', 'PAPER/SHADOW STATUS',
                  'INVALIDATION REASON', 'LIVE_MONEY=DISABLED'):
        assert label in html
