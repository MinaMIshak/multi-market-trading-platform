"""Scanner v2 binding of published EGX-RANK-v1 plans to trade-decision-v1.

Reporting projection only. It reuses the existing champion's explicit decimal
plan terms (entry zone, stop, T1) exactly as published; it never recomputes,
rounds or invents prices, targets, triggers or risk decisions. It performs no
admission, RiskEngine call, ledger write, publication or execution.

Trust boundary:
- the ranking artifact is checked against an expected SHA-256 supplied by the
  caller from a verified snapshot manifest;
- instrument identities, the session close, freshness and next-session
  validity are caller assertions from admitted security-master/calendar
  evidence. The report's ``next_expected_session`` is an expectation and is
  only carried as evidence, never used as calendar truth.

Only STRONG_CANDIDATE/CANDIDATE records become decisions. WATCHLIST and
NO_TRADE stay excluded. Missing identity or invalid terms are visibly excluded.
No research edge is implied: risk NOT_EVALUATED, confidence UNVALIDATED.
"""
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from app.paper.system_candidates import CANDIDATE_CLASSES, COMMISSION, MAX_HOLD, SLIPPAGE
from app.strategies import egx_ranking
from app.strategies.contracts import Candidate, Contract, aware
from app.strategies.trade_decision import DecisionSource, DecisionTerms, TradeDecision


ADAPTER_VERSION = 'egx-rank-v1-decision-adapter-v1'
TRIGGER = 'EGX_RANK_V1_NEXT_SESSION_ZONE_V1'
CAIRO = ZoneInfo('Africa/Cairo')
# simulate() exits at index MAX_HOLD, where index 0 is the fill session.
MAX_HOLDING_SESSIONS = MAX_HOLD + 1
EXCLUSIONS = ('NO_TRADE', 'SETUP_NOT_CONFIRMED', 'IDENTITY_UNBOUND', 'TERMS_INVALID')
_EXCLUSION_BY_CLASS = {'NO_TRADE': 'NO_TRADE', 'WATCHLIST': 'SETUP_NOT_CONFIRMED'}

# Identity of the unchanged champion rules this adapter projects.
RULES = {
    'rank_version': egx_ranking.VERSION, 'min_bars': egx_ranking.MIN_BARS,
    'min_traded_value': str(egx_ranking.MIN_TRADED_VALUE),
    'entry_band': str(egx_ranking.ENTRY_BAND), 'stop_floor': str(egx_ranking.STOP_FLOOR),
    'atr_multiple': str(egx_ranking.ATR_MULTIPLE), 't1_r': str(egx_ranking.T1_R),
    't2_r': str(egx_ranking.T2_R), 'max_hold_after_fill': MAX_HOLD,
    'slippage': str(SLIPPAGE), 'commission': str(COMMISSION),
}
CONFIG_ID = 'ca917d171004210a6b313dbc1bf8f70cda059c36e2c285e1bbeb0d2cb806fceb'


def _check_rules():
    if sha256(json.dumps(RULES, sort_keys=True, separators=(',', ':')).encode()).hexdigest() != CONFIG_ID:
        raise ValueError('projected rules changed; versioned adapter required')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('non-finite JSON number')


class InvalidTerms(ValueError):
    pass


class EgxRankBinding(Contract):
    """Admitted assertions absent from the ranking artifact (trusted caller)."""
    session: date
    next_session: date
    next_session_open: datetime
    data_as_of: datetime
    fresh_until: datetime
    valid_until: datetime
    instrument_ids: tuple[tuple[str, UUID], ...]
    calendar_evidence_id: str = Field(min_length=1, pattern=r'^\S+$')
    identity_evidence_id: str = Field(min_length=1, pattern=r'^\S+$')

    @model_validator(mode='after')
    def check(self):
        if not (aware(self.data_as_of) < aware(self.fresh_until)):
            raise ValueError('invalid freshness window')
        aware(self.valid_until)
        if self.data_as_of.astimezone(CAIRO).date() != self.session:
            raise ValueError('data_as_of is not the bound EGX session')
        if not (self.session < self.next_session == self.valid_until.astimezone(CAIRO).date()):
            raise ValueError('valid_until must end on the admitted next session')
        if not (self.data_as_of < aware(self.next_session_open) < self.valid_until):
            raise ValueError('invalid next-session open')
        if self.next_session_open.astimezone(CAIRO).date() != self.next_session:
            raise ValueError('open is not on the admitted next session')
        tickers = [t for t, _ in self.instrument_ids]
        if len(set(tickers)) != len(tickers):
            raise ValueError('duplicate instrument binding')
        return self


def _decimal(value):
    if type(value) is not str:
        raise ValueError('explicit decimal string required')
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError('finite decimal required')
    return result


def _ticker(record):
    ticker = record.get('ticker') if type(record) is dict else None
    if type(ticker) is not str or not ticker or ticker != ticker.strip() or ' ' in ticker:
        raise ValueError('invalid EGX ranking record')
    return ticker


def _decision(record, *, ticker, instrument_id, report, artifact_sha256, available_at, binding):
    # Only term parsing/geometry errors may be classified as TERMS_INVALID.
    try:
        zone = record.get('entry_zone')
        if type(zone) is not list or len(zone) != 2:
            raise ValueError('explicit entry zone required')
        entry_low, entry_high = _decimal(zone[0]), _decimal(zone[1])
        target_1, target_2 = _decimal(record.get('target_1')), _decimal(record.get('target_2'))
        reference, stop = _decimal(record.get('entry_reference')), _decimal(record.get('stop'))
        if not (0 < stop < entry_low <= reference <= entry_high < target_1 < target_2):
            raise ValueError('invalid zone/stop/target geometry')
        if not entry_low < entry_high:
            raise ValueError('collapsed entry zone')
        terms = DecisionTerms(
            entry_trigger=TRIGGER, entry_low=entry_low, entry_high=entry_high,
            entry_reference=reference, stop=stop, target=target_1,
            valid_until=binding.valid_until, max_holding_sessions=MAX_HOLDING_SESSIONS)
    except (ValueError, ArithmeticError) as exc:
        raise InvalidTerms('invalid published terms') from exc
    evidence = {
        'adapter_version': ADAPTER_VERSION, 'rules': RULES,
        'classification': record['classification'], 'score': record.get('score'),
        'score_components': record.get('score_components'),
        'selection_reason': record.get('selection_reason'),
        'target_2': str(target_2), 'partial_exit': 'HALF_AT_T1_THEN_STOP_TO_ENTRY',
        'risk_reward_t1': record.get('risk_reward_t1'),
        'risk_reward_t2': record.get('risk_reward_t2'),
        'published_risk_per_share': record.get('risk_per_share'),
        'entry_timing': record.get('entry_timing'),
        'data_warnings': record.get('data_warnings'), 'licensing': record.get('licensing'),
        'isin': record.get('isin'), 'artifact_id': record.get('artifact_id'),
        'evidence_snapshot_date': record.get('evidence_snapshot_date'),
        'report_next_expected_session': report.get('next_expected_session'),
        'report_next_session_basis': report.get('next_session_basis'),
        'admitted_next_session': binding.next_session.isoformat(),
        'admitted_next_session_open': binding.next_session_open.isoformat(),
    }
    candidate = Candidate(
        strategy_id=egx_ranking.VERSION, strategy_version='1', config_id=CONFIG_ID,
        symbol=ticker, decision_time=available_at, data_cutoff=binding.data_as_of,
        state='READY', entry_reference=None, stop_reference=None, target_reference=None,
        evidence_json=json.dumps(evidence, sort_keys=True, separators=(',', ':'), allow_nan=False))
    evidence_ids = [f'egx-ranking-sha256:{artifact_sha256}', binding.calendar_evidence_id,
                    binding.identity_evidence_id]
    if type(record.get('artifact_id')) is str and record['artifact_id'].strip():
        evidence_ids.append(f"daily-artifact:{record['artifact_id']}")
    source = DecisionSource(
        market='EGX', symbol=ticker, instrument_id=instrument_id, source_id=report['provider'],
        artifact_sha256=artifact_sha256, evidence_ids=tuple(evidence_ids),
        data_as_of=binding.data_as_of, available_at=available_at,
        fresh_until=binding.fresh_until, completed_bar=True)
    return TradeDecision(candidate=candidate, source=source, terms=terms)


def bind_egx_rank_report(artifact, *, expected_sha256, binding):
    """Project one manifest-verified egx-ranking-report-v1 into decisions."""
    if type(artifact) is not bytes or type(binding) is not EgxRankBinding:
        raise ValueError('exact artifact bytes and EgxRankBinding required')
    binding = EgxRankBinding.model_validate(binding.model_dump(mode='python'))
    if type(expected_sha256) is not str or sha256(artifact).hexdigest() != expected_sha256:
        raise ValueError('ranking artifact hash mismatch')
    _check_rules()
    report = json.loads(artifact, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    # Also reject overflowed JSON numbers such as 1e999, including unused fields.
    json.dumps(report, allow_nan=False)
    if (type(report) is not dict or report.get('schema') != 'egx-ranking-report-v1'
            or report.get('rank_version') != egx_ranking.VERSION
            or report.get('live_money') is not False or report.get('admission') != 'ADMITTED'
            or report.get('based_on_session') != binding.session.isoformat()
            or type(report.get('provider')) is not str or not report['provider'].strip()
            or type(report.get('generated_at')) is not str):
        raise ValueError('invalid EGX ranking report')
    available_at = aware(datetime.fromisoformat(report['generated_at']))
    # Detection must follow the bound close and precede staleness: no lookahead.
    if not (binding.data_as_of <= available_at < binding.fresh_until):
        raise ValueError('ranking generated outside the bound observation window')
    if available_at >= binding.next_session_open:
        raise ValueError('ranking generated after next-session entry window opened')
    records = report.get('symbols')
    if type(records) is not list or report.get('symbols_ranked') != len(records):
        raise ValueError('invalid EGX ranking records')
    tickers = [_ticker(r) for r in records]
    if len(set(tickers)) != len(tickers):
        raise ValueError('duplicate EGX ranking record')
    classes = Counter(r.get('classification') for r in records)
    if not set(classes) <= set(egx_ranking.CLASSES) or report.get('counts') != dict(classes):
        raise ValueError('inconsistent EGX ranking counts')
    identities = dict(binding.instrument_ids)
    if not set(identities) <= set(tickers):
        raise ValueError('instrument binding outside ranking scope')

    outcomes, decisions = [], []
    for ticker, record in sorted(zip(tickers, records)):
        cls = record['classification']
        row = {'symbol': ticker, 'market': 'EGX', 'classification': cls}
        if cls not in CANDIDATE_CLASSES:
            outcomes.append({**row, 'status': 'EXCLUDED', 'reason': _EXCLUSION_BY_CLASS[cls]})
            continue
        # classify() never emits a candidate with failed gates; treat as drift.
        if (record.get('rejection_reasons') != [] or record.get('freshness') != 'CURRENT'
                or record.get('rank_version') != egx_ranking.VERSION
                or record.get('last_market_session') != binding.session.isoformat()):
            raise ValueError('candidate record contradicts EGX-RANK-v1 gates')
        if ticker not in identities:
            outcomes.append({**row, 'status': 'EXCLUDED', 'reason': 'IDENTITY_UNBOUND'})
            continue
        try:
            decision = _decision(record, ticker=ticker, instrument_id=identities[ticker],
                                 report=report, artifact_sha256=expected_sha256,
                                 available_at=available_at, binding=binding)
        except InvalidTerms:
            outcomes.append({**row, 'status': 'EXCLUDED', 'reason': 'TERMS_INVALID'})
            continue
        decisions.append(decision)
        outcomes.append({**row, 'status': 'RESEARCH_CANDIDATE', 'reason': None,
                         'decision_id': decision.identity})
    excluded = [o for o in outcomes if o['status'] == 'EXCLUDED']
    return {
        'schema_version': 'scanner-v2-egx-rank-decisions-v1', 'market': 'EGX',
        'adapter_version': ADAPTER_VERSION, 'rank_version': egx_ranking.VERSION,
        'config_id': CONFIG_ID, 'artifact_sha256': expected_sha256,
        'session': binding.session.isoformat(), 'generated_at': report['generated_at'],
        'scope_kind': 'PUBLISHED_RANKING_SCOPE_NOT_AUTHORITATIVE_UNIVERSE',
        'authoritative_universe_size': None, 'requested': len(records),
        'classification_counts': {c: classes.get(c, 0) for c in egx_ranking.CLASSES},
        'symbols': outcomes, 'decisions': tuple(decisions), 'candidate_count': len(decisions),
        'excluded': len(excluded),
        'exclusion_counts': {r: sum(o['reason'] == r for o in excluded) for r in EXCLUSIONS},
        'research_status': 'UNVALIDATED', 'risk_status': 'NOT_EVALUATED',
        'mode': 'SHADOW', 'live_money': False, 'execution_allowed': False,
        'promotion': 'NOT_AUTHORIZED',
    }
