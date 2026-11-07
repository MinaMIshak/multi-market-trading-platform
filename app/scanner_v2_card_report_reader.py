"""Read-side gate for a retained ``scanner-v2-egx-card-report-v1`` document.

Pure validation of exact retained bytes: no file, network or database IO, no
HTTP route and no runtime caller. A future read-only loader/route must pass
the retained bytes and the SHA-256 recorded by its trusted producer.

This gate checks integrity and internal consistency only. It recomputes
coverage arithmetic, card-state counts, card freshness/expiry labels and risk
per share from the retained fields, and binds every card to the report's
ranking artifact and retained evidence identifiers. Each card's exact
``TradeDecision`` is rebuilt from its retained fields through the existing
contract validators; its decision ID and candidate digest must match, and the
unchanged ``trade_card`` projection of the rebuilt decision must equal the
retained card. It does NOT re-run the binder, verify the underlying ranking
bytes, admit identity/calendar evidence, evaluate risk or establish edge. A
digest is an integrity check, not a signature. Nothing is admitted, filled or
promoted.
"""
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from uuid import UUID

from app.paper.shadow_candidate_admission import strategy_candidate_sha256
from app.paper.system_candidates import CANDIDATE_CLASSES
from app.scanner_v2_card_report import CARD_STATES, FEATURE_STATUS, LIMITATIONS, SCHEMA_VERSION
from app.scanner_v2_egx_rank import (
    _EXCLUSION_BY_CLASS, ADAPTER_VERSION, CONFIG_ID, EXCLUSIONS, TRIGGER)
from app.scanner_v2_retained_binding import FRESHNESS_POLICY
from app.strategies import egx_ranking
from app.strategies.contracts import Candidate
from app.strategies.trade_decision import DecisionSource, DecisionTerms, TradeDecision, trade_card

MAX_BYTES = 8 * 1024 * 1024
_HEX64 = re.compile(r'[0-9a-f]{64}')
_CONFIRMED_STATES = ('READY', 'CONTINUATION_CONFIRMED', 'REVERSAL_CONFIRMED')
_UNBOUND_OR_INVALID = ('IDENTITY_UNBOUND', 'TERMS_INVALID')

_TOP_KEYS = frozenset((
    'schema_version', 'market', 'feature_status', 'observed_at', 'coverage',
    'provenance', 'cards', 'card_state_counts', 'lifecycle_evidence', 'limitations',
    'research_status', 'risk_status', 'mode', 'live_money', 'execution_allowed',
    'runtime_activation', 'promotion'))
_COVERAGE_KEYS = frozenset((
    'scope_kind', 'authoritative_universe_size', 'requested', 'classification_counts',
    'candidate_count', 'excluded', 'exclusion_counts', 'identity_exclusions', 'symbols'))
_PROVENANCE_KEYS = frozenset((
    'artifact_sha256', 'adapter_version', 'rank_version', 'config_id', 'session',
    'generated_at', 'evidence_available_at', 'binding_schema_version',
    'freshness_policy', 'identity_admission', 'calendar_admission', 'retained_evidence'))
_ROW_KEYS = frozenset(('symbol', 'market', 'classification', 'status', 'reason'))
_CARD_KEYS = frozenset((
    'contract_version', 'decision_id', 'observed_at', 'observed_fact',
    'strategy_inference', 'risk_decision', 'research_evidence', 'mode', 'live_money',
    'execution_allowed', 'promotion', 'invalidation_reason', 'status'))
_FACT_KEYS = frozenset(('market', 'symbol', 'data_as_of', 'detected_at', 'provenance'))
_SOURCE_KEYS = frozenset((
    'market', 'symbol', 'instrument_id', 'source_id', 'artifact_sha256', 'evidence_ids',
    'data_as_of', 'available_at', 'fresh_until', 'completed_bar'))
_INFERENCE_KEYS = frozenset(('why_found', 'setup', 'setup_version', 'config_id', 'trigger', 'plan'))
_PLAN_KEYS = frozenset((
    'entry_trigger', 'entry_low', 'entry_high', 'entry_reference', 'stop', 'target',
    'valid_until', 'max_holding_sessions', 'flat_by'))
_RISK_KEYS = frozenset(('status', 'risk_per_share', 'event_risk'))
_RESEARCH_KEYS = frozenset(('status', 'confidence', 'candidate_sha256', 'signal_evidence_json'))


class RetainedCardReportError(ValueError):
    """Retained card report failed closed; nothing from it may be displayed."""


def _fail(reason):
    raise RetainedCardReportError(reason)


def _require(condition, reason):
    if not condition:
        _fail(reason)


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            _fail('duplicate JSON key')
        out[key] = value
    return out


def _constant(name):
    _fail('non-finite JSON number')


def _obj(value, keys, what):
    _require(type(value) is dict and value.keys() == keys, f'{what} fields drifted')
    return value


def _hex(value, what):
    _require(type(value) is str and _HEX64.fullmatch(value) is not None, f'invalid {what}')
    return value


def _time(value, what):
    _require(type(value) is str, f'invalid {what}')
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _fail(f'invalid {what}')
    _require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
             f'naive {what}')
    return parsed


def _count(value, what):
    _require(type(value) is int and value >= 0, f'invalid {what}')
    return value


def _price(value, what):
    _require(type(value) is str, f'invalid {what}')
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        _fail(f'invalid {what}')
    _require(parsed.is_finite() and parsed > 0, f'invalid {what}')
    return parsed


def _safety(doc, what):
    _require(doc['mode'] == 'SHADOW' and doc['live_money'] is False
             and doc['execution_allowed'] is False and doc['promotion'] == 'NOT_AUTHORIZED',
             f'{what} safety invariant violated')


def _rows(coverage):
    rows = coverage['symbols']
    _require(type(rows) is list, 'coverage rows drifted')
    symbols = []
    for row in rows:
        candidate = type(row) is dict and row.get('status') == 'RESEARCH_CANDIDATE'
        _obj(row, _ROW_KEYS | {'decision_id'} if candidate else _ROW_KEYS, 'coverage row')
        _require(type(row['symbol']) is str and row['symbol'] != ''
                 and row['symbol'].strip() == row['symbol'] and row['market'] == 'EGX'
                 and row['classification'] in egx_ranking.CLASSES, 'invalid coverage row')
        if candidate:
            _require(row['reason'] is None and row['classification'] in CANDIDATE_CLASSES,
                     'invalid candidate row')
            _hex(row['decision_id'], 'decision_id')
        else:
            # Same mapping as the adapter: non-candidate classes map to one fixed
            # reason; candidate classes can only be excluded for identity/terms.
            allowed = ((_UNBOUND_OR_INVALID if row['classification'] in CANDIDATE_CLASSES
                        else (_EXCLUSION_BY_CLASS.get(row['classification']),)))
            _require(row['status'] == 'EXCLUDED' and row['reason'] in EXCLUSIONS
                     and row['reason'] in allowed, 'invalid exclusion row')
        symbols.append(row['symbol'])
    _require(symbols == sorted(set(symbols)), 'coverage rows not unique and ordered')
    return rows


def _coverage(coverage):
    _obj(coverage, _COVERAGE_KEYS, 'coverage')
    _require(coverage['scope_kind'] == 'PUBLISHED_RANKING_SCOPE_NOT_AUTHORITATIVE_UNIVERSE'
             and coverage['authoritative_universe_size'] is None,
             'coverage claims authoritative universe')
    rows = _rows(coverage)
    candidates = [r for r in rows if r['status'] == 'RESEARCH_CANDIDATE']
    excluded = [r for r in rows if r['status'] == 'EXCLUDED']
    classes = Counter(r['classification'] for r in rows)
    reasons = Counter(r['reason'] for r in excluded)
    _require(_count(coverage['requested'], 'requested') == len(rows)
             and _count(coverage['candidate_count'], 'candidate_count') == len(candidates)
             and _count(coverage['excluded'], 'excluded') == len(excluded),
             'coverage counts inconsistent')
    _require(coverage['classification_counts']
             == {c: classes.get(c, 0) for c in egx_ranking.CLASSES},
             'classification counts inconsistent')
    _require(coverage['exclusion_counts'] == {r: reasons.get(r, 0) for r in EXCLUSIONS},
             'exclusion counts inconsistent')
    exclusions = coverage['identity_exclusions']
    _require(type(exclusions) is list and all(
        type(e) is dict and e.keys() == {'symbol', 'reason'}
        and type(e['symbol']) is str and type(e['reason']) is str for e in exclusions),
        'identity exclusions drifted')
    return candidates


def _provenance(provenance, observed_at):
    _obj(provenance, _PROVENANCE_KEYS, 'provenance')
    _hex(provenance['artifact_sha256'], 'artifact_sha256')
    _require(provenance['adapter_version'] == ADAPTER_VERSION
             and provenance['rank_version'] == egx_ranking.VERSION
             and provenance['config_id'] == CONFIG_ID
             and provenance['freshness_policy'] == FRESHNESS_POLICY
             and provenance['identity_admission'] == 'INTERNAL_SNAPSHOT_VERIFIED'
             and provenance['calendar_admission'] == 'NOT_ESTABLISHED'
             and type(provenance['binding_schema_version']) is str
             and type(provenance['session']) is str,
             'provenance versions or admission labels drifted')
    available = _time(provenance['evidence_available_at'], 'evidence_available_at')
    generated = _time(provenance['generated_at'], 'generated_at')
    # Later evidence never backdates a decision; viewing never precedes generation.
    _require(available <= generated <= observed_at, 'provenance timestamps out of order')
    retained = provenance['retained_evidence']
    _require(type(retained) is dict and type(retained.get('binding')) is dict
             and retained.get('schema_version') == provenance['binding_schema_version']
             and retained.get('market') == 'EGX'
             and retained.get('freshness_policy') == FRESHNESS_POLICY
             and retained.get('identity_admission') == 'INTERNAL_SNAPSHOT_VERIFIED'
             and retained.get('calendar_admission') == 'NOT_ESTABLISHED'
             and retained.get('runtime_activation') is False
             and retained.get('live_money') is False,
             'retained evidence drifted')
    _require(_time(retained.get('evidence_available_at'), 'retained evidence_available_at')
             == available, 'retained evidence availability drifted')
    binding = retained['binding']
    ids = (binding.get('calendar_evidence_id'), binding.get('identity_evidence_id'))
    _require(all(type(i) is str and i.strip() for i in ids), 'retained evidence ids missing')
    _require(binding.get('session') == provenance['session'], 'retained session drifted')
    return {'fresh_until': _time(binding.get('fresh_until'), 'retained fresh_until'),
            'evidence_ids': (f'egx-ranking-sha256:{provenance["artifact_sha256"]}', *ids),
            'artifact_sha256': provenance['artifact_sha256'], 'generated_at': generated}


def _card(card, row, observed_text, observed_at, bound):
    _obj(card, _CARD_KEYS, 'card')
    _safety(card, 'card')
    _require(card['contract_version'] == 'trade-card-v1'
             and card['decision_id'] == row['decision_id']
             and card['observed_at'] == observed_text, 'card does not match coverage row')
    fact = _obj(card['observed_fact'], _FACT_KEYS, 'card observed fact')
    source = _obj(fact['provenance'], _SOURCE_KEYS, 'card source')
    _require(fact['market'] == source['market'] == 'EGX'
             and fact['symbol'] == source['symbol'] == row['symbol']
             and source['artifact_sha256'] == bound['artifact_sha256']
             and source['completed_bar'] is True
             and type(source['evidence_ids']) is list
             and all(type(i) is str for i in source['evidence_ids'])
             and set(bound['evidence_ids']) <= set(source['evidence_ids']),
             'card provenance not bound to retained evidence')
    data_as_of = _time(source['data_as_of'], 'card data_as_of')
    _require(_time(fact['data_as_of'], 'card data_as_of') == data_as_of, 'card data_as_of drifted')
    available = _time(source['available_at'], 'card available_at')
    detected = _time(fact['detected_at'], 'card detected_at')
    fresh_until = _time(source['fresh_until'], 'card fresh_until')
    _require(fresh_until == bound['fresh_until'], 'card freshness not bound to retained evidence')
    _require(data_as_of <= available <= detected < fresh_until and detected <= observed_at
             and detected <= bound['generated_at'], 'card timestamps out of order')

    inference = _obj(card['strategy_inference'], _INFERENCE_KEYS, 'card inference')
    plan = _obj(inference['plan'], _PLAN_KEYS, 'card plan')
    _require(inference['why_found'] in _CONFIRMED_STATES
             and inference['trigger'] == plan['entry_trigger'] == TRIGGER
             and inference['config_id'] == CONFIG_ID, 'card setup drifted')
    low, high = _price(plan['entry_low'], 'entry_low'), _price(plan['entry_high'], 'entry_high')
    reference = _price(plan['entry_reference'], 'entry_reference')
    stop, target = _price(plan['stop'], 'stop'), _price(plan['target'], 'target')
    _require(stop < low <= high < target and low <= reference <= high,
             'card plan geometry invalid')
    _require(type(plan['max_holding_sessions']) is int and plan['max_holding_sessions'] > 0,
             'invalid max_holding_sessions')
    valid_until = _time(plan['valid_until'], 'valid_until')
    flat_by = None if plan['flat_by'] is None else _time(plan['flat_by'], 'flat_by')

    risk = _obj(card['risk_decision'], _RISK_KEYS, 'card risk decision')
    _require(risk['status'] == 'NOT_EVALUATED' and risk['event_risk'] == 'UNKNOWN'
             and risk['risk_per_share'] == str(reference - stop), 'card risk drifted')
    research = _obj(card['research_evidence'], _RESEARCH_KEYS, 'card research evidence')
    _require(research['status'] == 'UNVALIDATED' and research['confidence'] == 'UNVALIDATED'
             and type(research['signal_evidence_json']) is str, 'card research evidence drifted')
    _hex(research['candidate_sha256'], 'candidate_sha256')

    # Recompute the viewing label exactly as trade_card does; never trust stored labels.
    if observed_at >= valid_until or (flat_by is not None and observed_at >= flat_by):
        state = 'PLAN_EXPIRED'
    elif observed_at >= fresh_until:
        state = 'DATA_STALE'
    else:
        state = 'RESEARCH_CANDIDATE'
    expected = ((None, 'RESEARCH_CANDIDATE') if state == 'RESEARCH_CANDIDATE'
                else (state, 'EXCLUDED'))
    _require((card['invalidation_reason'], card['status']) == expected,
             'card state does not match retained timestamps')

    # Rebuild the exact decision through the existing contract validators. The
    # EGX-RANK-v1 adapter never sets float candidate references; a producer that
    # did would fail closed here rather than be reinterpreted.
    _require(type(source['instrument_id']) is str, 'invalid card instrument_id')
    try:
        decision = TradeDecision(
            candidate=Candidate(
                strategy_id=inference['setup'], strategy_version=inference['setup_version'],
                config_id=inference['config_id'], symbol=fact['symbol'],
                decision_time=detected, data_cutoff=data_as_of, state=inference['why_found'],
                entry_reference=None, stop_reference=None, target_reference=None,
                evidence_json=research['signal_evidence_json']),
            source=DecisionSource(
                market=source['market'], symbol=source['symbol'],
                instrument_id=UUID(source['instrument_id']), source_id=source['source_id'],
                artifact_sha256=source['artifact_sha256'],
                evidence_ids=tuple(source['evidence_ids']), data_as_of=data_as_of,
                available_at=available, fresh_until=fresh_until, completed_bar=True),
            terms=DecisionTerms(
                entry_trigger=plan['entry_trigger'], entry_low=low, entry_high=high,
                entry_reference=reference, stop=stop, target=target, valid_until=valid_until,
                max_holding_sessions=plan['max_holding_sessions'], flat_by=flat_by))
    except (ValueError, TypeError, ArithmeticError):
        _fail('card decision cannot be rebuilt')
    _require(decision.identity == card['decision_id'],
             'card decision_id does not match rebuilt decision')
    _require(strategy_candidate_sha256(decision.candidate) == research['candidate_sha256'],
             'card candidate_sha256 does not match rebuilt candidate')
    _require(trade_card(decision, observed_at=observed_at) == card,
             'card does not match projection of rebuilt decision')
    return state


def read_retained_egx_card_report(payload, *, expected_sha256):
    """Validate exact retained card-report bytes; return the parsed document.

    Raises ``RetainedCardReportError`` on any drift. Callers must treat a
    failure as UNAVAILABLE and must not display partial content.
    """
    _require(type(payload) is bytes and 0 < len(payload) <= MAX_BYTES,
             'retained report must be bounded bytes')
    _hex(expected_sha256, 'expected_sha256')
    _require(sha256(payload).hexdigest() == expected_sha256, 'retained report hash mismatch')
    try:
        doc = json.loads(payload.decode('utf-8'), object_pairs_hook=_pairs,
                         parse_constant=_constant)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail('retained report is not strict JSON')
    _obj(doc, _TOP_KEYS, 'report')
    _safety(doc, 'report')
    _require(doc['schema_version'] == SCHEMA_VERSION and doc['market'] == 'EGX'
             and doc['feature_status'] == FEATURE_STATUS
             and doc['runtime_activation'] is False
             and doc['research_status'] == 'UNVALIDATED'
             and doc['risk_status'] == 'NOT_EVALUATED'
             and doc['limitations'] == list(LIMITATIONS)
             and doc['lifecycle_evidence'] == {'status': 'NOT_VERIFIED', 'sample_size': None,
                                               'net_result': None},
             'report identity, limitations or lifecycle claim drifted')
    observed_at = _time(doc['observed_at'], 'observed_at')
    candidates = _coverage(doc['coverage'])
    bound = _provenance(doc['provenance'], observed_at)
    cards = doc['cards']
    _require(type(cards) is list and len(cards) == len(candidates),
             'cards do not match candidate rows')
    states = Counter(_card(card, row, doc['observed_at'], observed_at, bound)
                     for card, row in zip(cards, candidates))
    _require(doc['card_state_counts'] == {s: states.get(s, 0) for s in CARD_STATES},
             'card state counts inconsistent')
    return doc
