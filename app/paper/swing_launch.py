"""Manual EGX SWING v1 WATCH composition. No scheduler or execution inference."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Context, Decimal, localcontext
import hashlib
import json
from pathlib import Path
from typing import Literal
from uuid import UUID, NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator

from app.core.daily_refresh_runtime import build_daily_refresh_runtime, DEFAULT_EODHD_TARGETS
from app.data.daily_canonical import (
    DAILY_SEMANTIC_CONTRACT_VERSION,
    DAILY_SERIALIZATION_FORMAT,
    DailyBarSemanticClass,
    serialize_daily_rows,
)
from app.data.daily_refresh_admission import DailyRefreshAdmissionPolicy
from app.data.point_in_time import PointInTimeDailyRepository
from app.data.quota import VerifiedQuotaCost
from app.data.raw_store import ImmutableRawStore
from app.domain import TradePlan
from app.egx_scope import valid_scope_symbol, require_equity_identity
from app.egx_refresh_mapping import require_refresh_mapping
from app.data.daily_refresh_job import DailyRefreshTarget
from app.paper.shadow_candidate_admission import ShadowCandidateAdmission
from app.paper.shadow_facts import ForwardSessionFact, SESSION_FIELDS, _require_fields
from app.paper.shadow_producer import StrategyShadowRequest, StrategyShadowSelection, produce_strategy_watchlist
from app.paper.shadow_records import ShadowEvidenceReference, ShadowSession
from app.research.historical_evidence import HistoricalEvidencePackage, require_historical_evidence
from app.research.historical_pit import HistoricalSessionRecord, SESSION_EVIDENCE_FIELDS, DAILY_EVIDENCE_FIELDS
from app.storage.reference_repository import ReferenceRepository
from app.storage.scheduler_repository import SchedulerRepository
from app.storage.security_master_repository import SecurityMasterRepository
from app.strategies.contracts import Contract
from app.strategies.eod import SwingConfig, SwingEngine


class LaunchBlocked(ValueError):
    def __init__(self, status: str, reason: str):
        self.status, self.reason = status, reason
        super().__init__(reason)


class SwingLaunchInput(Contract):
    schema_version: Literal['swing-paper-launch-v1']
    symbol: str
    instrument_id: UUID
    # Explicit human selection, never derived from an M4 state.
    decision_status: Literal['WATCH']
    planning_rule: Literal['SWING-V1-Q03-v1']
    history_start: date
    sessions: tuple[HistoricalSessionRecord, ...]
    signal_session: ForwardSessionFact
    entry_session: ForwardSessionFact
    clock_packages: tuple[HistoricalEvidencePackage, ...]
    # May be absent for refresh; required for verify/publish after source review.
    daily_package: HistoricalEvidencePackage | None

    @field_validator('symbol')
    @classmethod
    def validate_symbol(cls, value):
        # Spelling is not dated membership or source identity evidence.
        if not valid_scope_symbol(value):
            raise ValueError('canonical EGX symbol required')
        return value


class Q03Rule(Contract):
    version: Literal['SWING-V1-Q03-v1'] = 'SWING-V1-Q03-v1'
    entry_band: Decimal = Field(default=Decimal('0.005'), ge=Decimal('0.005'), le=Decimal('0.005'))
    stop_fraction: Decimal = Field(default=Decimal('0.03'), ge=Decimal('0.03'), le=Decimal('0.03'))
    target_fraction: Decimal = Field(default=Decimal('0.06'), ge=Decimal('0.06'), le=Decimal('0.06'))
    entry_timing: Literal['NEXT_ELIGIBLE_SESSION_ONLY'] = 'NEXT_ELIGIBLE_SESSION_ONLY'
    no_fill: Literal['NO_FILL'] = 'NO_FILL'
    maximum_holding_eligible_sessions: Literal[10] = 10
    time_exit: Literal['CLOSE_OF_10TH_ELIGIBLE_SESSION_AFTER_FILL'] = 'CLOSE_OF_10TH_ELIGIBLE_SESSION_AFTER_FILL'
    session_end_at: None = None


def swing_config():
    return SwingConfig(config_version='SWING-V1-Q02-50-20-50-20', minimum_history=50,
                       fast_ema_window=20, slow_ema_window=50, breakout_lookback=20)


def _now():
    return datetime.now(timezone.utc)


def _blocked(reason, status='EVIDENCE_BLOCKED'):
    raise LaunchBlocked(status, reason)


def _package(package, fields, at):
    require_historical_evidence(package, decision_at=at, research_built_at=at)
    _require_fields(package, set(fields))


def admit_calendar(source: SwingLaunchInput, at: datetime):
    """Every intervening civil date is explicit; never infer weekends or opens."""
    from app.ui.shadow_input import _decode
    source = _decode(SwingLaunchInput, source.model_dump(mode='json'))
    if at.tzinfo is not timezone.utc:
        _blocked('UTC runtime clock required')
    signal, entry = source.signal_session, source.entry_session
    for clock in (signal, entry):
        if (clock.market != 'EGX' or clock.calendar_mic != 'XCAI'
                or clock.opens_at.astimezone(ZoneInfo('Africa/Cairo')).date() != clock.market_date
                or clock.closes_at.astimezone(ZoneInfo('Africa/Cairo')).date() != clock.market_date):
            _blocked('EGX dated session clocks required')
    if not signal.closes_at < at < entry.opens_at - timedelta(minutes=15):
        _blocked('after-close / next-session freeze window unavailable')
    if not source.history_start <= signal.market_date < entry.market_date:
        _blocked('session ordering invalid')
    # Bounded input; historical packages may not silently imply omitted days.
    span = (entry.market_date - source.history_start).days
    if not 1 <= span <= 1500:
        _blocked('bounded daily calendar required')
    records = {s.market_date: s for s in source.sessions}
    expected = {source.history_start + timedelta(days=i) for i in range(span + 1)}
    if len(records) != len(source.sessions) or set(records) != expected:
        _blocked('complete unique dated session coverage required')
    for session in records.values():
        _package(session.evidence, SESSION_EVIDENCE_FIELDS, at)
        if session.instrument_state == 'UNSUPPORTED':
            _blocked('unknown instrument session eligibility')
    for day in (signal.market_date, entry.market_date):
        if records[day].instrument_state != 'EXPECTED_OBSERVATION':
            _blocked('signal and entry must be authenticated eligible sessions')
    if any(s.market_state == 'TRADING_SESSION' for d, s in records.items()
           if signal.market_date < d < entry.market_date):
        _blocked('signal is not latest session or entry is not next session')
    clocks = {p.identity: p for p in source.clock_packages}
    if len(clocks) != len(source.clock_packages) or set(clocks) != {
            signal.evidence_package_id, entry.evidence_package_id}:
        _blocked('exact session-clock evidence required')
    for p in clocks.values():
        _package(p, SESSION_FIELDS, at)
    return source


def build_q03_plan(*, canonical_close: Decimal, symbol: str, instrument_id: UUID,
                   decision_at: datetime, entry_session: ForwardSessionFact, source_audit_id: str):
    """Explicit frozen rule over canonical Decimal close, never Candidate floats."""
    if type(canonical_close) is not Decimal or not canonical_close.is_finite() or canonical_close <= 0:
        raise ValueError('positive canonical Decimal close required')
    rule = Q03Rule()
    identity = f'{rule.version}:{instrument_id}:{source_audit_id}:{decision_at.isoformat()}'
    with localcontext(Context(prec=34)):
        return TradePlan(
            trade_plan_id=uuid5(NAMESPACE_URL, identity + ':plan'),
            signal_id=uuid5(NAMESPACE_URL, identity + ':signal'), symbol=symbol,
            entry_reference=canonical_close,
            entry_low=canonical_close * (1 - rule.entry_band),
            entry_high=canonical_close * (1 + rule.entry_band),
            stop_price=canonical_close * (1 - rule.stop_fraction),
            target_1=canonical_close * (1 + rule.target_fraction), target_2=None, target_3=None,
            created_at=decision_at, valid_until=entry_session.closes_at,
        )


def _identity(database, source):
    try:
        resolved = SecurityMasterRepository(database).resolve(source.symbol, provider='canonical')
    except (KeyError, ValueError):
        _blocked('required security-master identity unavailable')
    try:
        require_equity_identity(resolved, symbol=source.symbol, instrument_id=source.instrument_id)
    except ValueError as exc:
        _blocked(str(exc))


def refresh_once(database, data_root, source, *, cost: VerifiedQuotaCost, api_token=None, provider=None, target=None):
    """Explicit operator invocation; keep original 260-bar admission and quota gates."""
    at = _now()
    source = admit_calendar(source, at)
    _identity(database, source)
    if type(cost) is not VerifiedQuotaCost or type(cost.units) is not int or cost.units <= 0 or not cost.evidence.strip():
        _blocked('verified quota cost required')
    if target is None:
        target = next((t for t in DEFAULT_EODHD_TARGETS if t.canonical_symbol == source.symbol), None)
    if target is None:
        _blocked('reviewed provider refresh mapping unavailable')
    try:
        if type(target) is not DailyRefreshTarget:
            raise ValueError("explicit provider refresh mapping required")
        require_refresh_mapping(
            SecurityMasterRepository(database), canonical_symbol=target.canonical_symbol,
            provider_symbol=target.provider_symbol,
            provider_name=getattr(provider, 'name', None) if provider is not None else 'eodhd',
            symbol=source.symbol, instrument_id=source.instrument_id,
        )
    except ValueError as exc:
        _blocked(str(exc))
    runtime = build_daily_refresh_runtime(
        database=database, scheduler_repository=SchedulerRepository(database), data_root=data_root,
        api_token=api_token, provider=provider, targets=(target,),
        quota_cost_contract=lambda **inputs: cost,
    )
    # The job explicitly excludes scheduler ledger/dispatch. No scheduler mode is changed.
    result = runtime.refresh_job.run(provider=runtime.provider, start_date=source.history_start,
                                    end_date=source.signal_session.market_date,
                                    snapshot_date=source.signal_session.market_date)
    return {'status': 'NOT_RUN', 'operation': 'REFRESH_COMPLETED_SIGNAL_NOT_RUN',
            'ingestion_id': result.items[0].ingestion_id, 'artifact_id': result.items[0].artifact_id,
            'valid_bar_count': result.items[0].valid_bar_count}


class _OperationalWindowRepository(PointInTimeDailyRepository):
    """Windowed consumer view retaining the full admitted source's PIT audit."""

    def __init__(self, references, history_start):
        super().__init__(references, selection_context="OPERATIONAL_EXPLICIT_SYMBOL")
        self.history_start = history_start

    def window(self, data, signal_date):
        return replace(
            data,
            rows=tuple(b for b in data.rows
                       if self.history_start <= b.market_date <= signal_date),
            split_adjusted=tuple(b for b in data.split_adjusted
                                 if self.history_start <= b.market_date <= signal_date),
        )

    def load(self, **kwargs):
        return self.window(super().load(**kwargs), kwargs['expected_market_date'])


def prepare_signal(database, data_root: Path, source: SwingLaunchInput):
    at = _now()
    source = admit_calendar(source, at)
    _identity(database, source)
    day = source.signal_session.market_date
    with database.connect() as con:
        rows = con.execute(
            'SELECT a.*, i.raw_path, i.ingestion_id, i.source_uri FROM daily_canonical_artifacts a '
            'JOIN daily_canonical_sources s USING(artifact_id) '
            'JOIN data_ingestions i USING(ingestion_id) '
            "WHERE a.canonical_symbol=? AND a.status='VALIDATED' AND i.status='VALIDATED' "
            'ORDER BY a.newest_market_date DESC', (source.symbol,),
        ).fetchall()
    if not rows:
        _blocked('admitted daily history missing', 'DATA_INSUFFICIENT')
    current = [r for r in rows if r['newest_market_date'] == day.isoformat()]
    if not current:
        _blocked('canonical history does not reach authenticated session', 'DATA_STALE')
    package = source.daily_package
    if package is None:
        _blocked('reviewed daily evidence package required')
    _package(package, DAILY_EVIDENCE_FIELDS, at)

    references = ReferenceRepository(
        database,
        ImmutableRawStore(Path(data_root) / 'raw'),
    )

    # Multiple validated canonical editions may coexist in the immutable ledger.
    # Operational verify/publish must select the one bound by the reviewed
    # evidence package; never choose by insertion order or silently demote history.
    bound = []
    for row in current:
        manifest = references.ingestions.get_manifest_by_raw_path(row['raw_path'])
        if (
            package.raw_receipt.sha256 == manifest.sha256
            and package.raw_receipt.provider == manifest.provider
            and package.raw_receipt.byte_size == manifest.byte_size
            and package.raw_receipt.local_received_at == manifest.received_at
            and package.raw_receipt.source_locator == row['source_uri']
        ):
            bound.append((row, manifest))

    if len(bound) != 1:
        _blocked('daily evidence must uniquely bind current canonical source')

    artifact, manifest = bound[0]
    if (artifact['instrument_id'] != str(source.instrument_id)
            or datetime.fromisoformat(artifact['validated_at']) > at):
        _blocked('canonical artifact identity/availability mismatch')
    repository = PointInTimeDailyRepository(
        references,
        selection_context="OPERATIONAL_EXPLICIT_SYMBOL",
    )
    data = repository.load(raw_path=artifact['raw_path'], universe_date=day,
                           expected_market_date=day, as_of=at)
    if any(b.instrument_id != source.instrument_id or b.canonical_symbol != source.symbol for b in data.rows):
        _blocked('canonical history identity mismatch')
    # Consumer layers never read canonical artifact files or their paths.
    # Re-materialize deterministically from the admitted immutable raw/PIT
    # boundary and bind that materialization to the validated artifact ledger.
    payload = serialize_daily_rows(data.rows)
    valid_bar_count = sum(
        row.semantic_class == DailyBarSemanticClass.VALID_EXECUTABLE
        for row in data.rows
    )
    quarantined_bar_count = len(data.rows) - valid_bar_count
    if (
        hashlib.sha256(payload).hexdigest() != artifact['sha256']
        or len(payload) != artifact['byte_size']
        or len(data.rows) != artifact['record_count']
        or data.rows[0].market_date.isoformat() != artifact['oldest_market_date']
        or data.rows[-1].market_date.isoformat() != artifact['newest_market_date']
        or valid_bar_count != artifact['valid_bar_count']
        or quarantined_bar_count != artifact['quarantined_bar_count']
        or artifact['semantic_contract_version'] != DAILY_SEMANTIC_CONTRACT_VERSION
        or artifact['serialization_format'] != DAILY_SERIALIZATION_FORMAT
    ):
        _blocked('validated artifact ledger does not match PIT materialization')
    # Re-read the admitted source manifest after PIT materialization so the
    # evidence-to-source binding is checked again at the consumer boundary.
    manifest = references.ingestions.get_manifest_by_raw_path(artifact['raw_path'])
    if (package.raw_receipt.sha256 != manifest.sha256
            or package.raw_receipt.provider != manifest.provider
            or package.raw_receipt.byte_size != manifest.byte_size
            or package.raw_receipt.local_received_at != manifest.received_at
            or package.raw_receipt.source_locator != artifact['source_uri']):
        _blocked('daily evidence must bind exact admitted raw bytes')
    window_repository = _OperationalWindowRepository(references, source.history_start)
    data = window_repository.window(data, day)
    if len(data.rows) < 260:
        _blocked('refresh admission requires 260 valid bars; SWING requires 50', 'DATA_INSUFFICIENT')
    DailyRefreshAdmissionPolicy().validate_rows(data.rows, expected_market_date=day)
    expected_days = {s.market_date for s in source.sessions
                     if s.market_date <= day and s.instrument_state == 'EXPECTED_OBSERVATION'}
    if {b.market_date for b in data.rows} != expected_days:
        _blocked('bar dates differ from authenticated eligible history', 'DATA_INSUFFICIENT')
    candidate = SwingEngine(window_repository, swing_config()).evaluate(
        raw_path=artifact['raw_path'], signal_date=day, decision_time=at)
    # Both loads must agree; never mix two PIT editions in a signal/plan.
    if json.loads(candidate.evidence_json)['source_audit_id'] != data.audit_id:
        _blocked('PIT snapshot changed during evaluation')
    evidence = json.loads(candidate.evidence_json)
    evidence['operational_history_window'] = {
        'history_start': source.history_start.isoformat(),
        'signal_date': day.isoformat(),
        'bar_count': len(data.rows),
    }
    candidate = candidate.model_copy(update={
        'evidence_json': json.dumps(evidence, sort_keys=True, separators=(',', ':')),
    })
    plan = build_q03_plan(canonical_close=data.rows[-1].close, symbol=source.symbol,
                          instrument_id=source.instrument_id, decision_at=at,
                          entry_session=source.entry_session, source_audit_id=data.audit_id)
    return source, candidate, plan, data


def run_signal(database, data_root, source, directory: Path, *, publish: bool):
    source, candidate, plan, data = prepare_signal(database, data_root, source)
    result = {'status': 'NOT_RUN', 'market_data': 'FRESH',
              'symbol': source.symbol, 'market': 'EGX',
              'last_verified_session': source.signal_session.market_date.isoformat(),
              'decision_at': candidate.decision_time.isoformat(), 'strategy_id': 'SWING',
              'strategy_version': '1', 'mode': 'SHADOW', 'live': 'DISABLED',
              'empirical_status': 'NOT VALIDATED', 'execution_status': 'NO EXECUTION INFERENCE'}
    operational = result | {
        'status': 'WATCH' if candidate.state == 'WATCH' else 'READY_NO_SIGNAL',
        'symbol': source.symbol, 'market': 'EGX',
        'entry_session': source.entry_session.market_date.isoformat(),
        'valid_until': source.entry_session.opens_at.isoformat(),
        'provider': source.daily_package.raw_receipt.provider,
        'raw_sha256': source.daily_package.raw_receipt.sha256,
        'history_start': source.history_start.isoformat(), 'bar_count': len(data.rows),
        'pit_audit_id': data.audit_id,
        'trade_plan': plan.model_dump(mode='json') if candidate.state == 'WATCH' else None,
    }
    if candidate.state not in ('WATCH', 'NO_CONFIRMATION'):
        raise RuntimeError('unexpected SWING v1 state')
    payload = json.dumps(operational, sort_keys=True)
    event_id = hashlib.sha256(payload.encode()).hexdigest()
    with database.connect() as con:
        con.execute(
            "INSERT OR IGNORE INTO audit_events "
            "(event_id,event_key,event_type,entity_type,entity_id,market_date,created_at,payload_json) "
            "VALUES (?,?,'PAPER_SIGNAL_VERIFIED','paper_signal',?,?,?,?)",
            (event_id, event_id, source.symbol, source.signal_session.market_date.isoformat(),
             candidate.decision_time.isoformat(), payload),
        )
    result['signal_status'] = operational['status']
    if not publish:
        return result | {'operation': 'VERIFIED_SIGNAL_NOT_PUBLISHED'}
    if directory.exists():
        _blocked('collection already exists; overwrite/backfill forbidden')
    if candidate.state == 'NO_CONFIRMATION':
        return result | {'status': 'READY_NO_SIGNAL'}
    if candidate.state != 'WATCH':
        raise RuntimeError('unexpected SWING v1 state')
    packages = {p.identity: p for p in (*source.clock_packages,
                *(s.evidence for s in source.sessions), source.daily_package)}
    evidence = tuple(ShadowEvidenceReference(
        evidence_id=p.identity, source_authority=p.attachments[0].source_authority_context,
        source_locator=p.raw_receipt.source_locator, artifact_sha256=p.raw_receipt.sha256,
        available_at=(p.evidence.availability.exact_at if p.evidence.availability.kind == 'EXACT'
                      else p.evidence.availability.end),
    ) for p in packages.values())
    entry = source.entry_session
    session = ShadowSession(market='EGX', calendar_mic='XCAI', market_date=entry.market_date,
                            state='OPEN', opens_at=entry.opens_at,
                            decision_cutoff=entry.opens_at - timedelta(minutes=15),
                            evidence_ids=(entry.evidence_package_id,))
    # Preserve typed plan + frozen rule in the existing audited context field.
    context = json.dumps({'planning_rule': Q03Rule().model_dump(mode='json'),
                          'trade_plan': plan.model_dump(mode='json'),
                          'signal_session': source.signal_session.model_dump(mode='json'),
                          'entry_session': entry.model_dump(mode='json'),
                          'pit_audit_id': data.audit_id, 'pit_provenance_ids': data.provenance_ids,
                          'freshness_at_decision': 'FRESH'}, sort_keys=True, separators=(',', ':'))
    admission = ShadowCandidateAdmission(
        candidate_id=str(plan.signal_id), decision_status=source.decision_status,
        identity_status='KNOWN', instrument_id=source.instrument_id,
        thesis='SWING v1; unvalidated PAPER/SHADOW observation', context=(context,),
        technical_setup='Frozen SWING 50/20/50/20',
        entry_condition='Next authenticated eligible session only; no valid fill -> NO_FILL; '
                        'maximum holding 10 eligible sessions after fill; exit at tenth close. '
                        'WATCH ONLY; risk, sizing and fill policy are not approved.',
        entry_rule='LONG_ENTRY_ZONE_TOUCH_V1', entry_low=plan.entry_low, entry_high=plan.entry_high,
        stop=plan.stop_price, targets=(plan.target_1,), liquidity_status='UNKNOWN',
        liquidity_reason='Not evaluated for unsized WATCH', paper_quantity=0,
        paper_risk_status='NOT_EVALUATED', evidence_ids=tuple(packages),
    )
    request = StrategyShadowRequest(
        record_id=f'SWING-v1-{source.symbol}-{source.signal_session.market_date}',
        information_cutoff=candidate.decision_time, session=session, evidence=evidence,
        selections=(StrategyShadowSelection(candidate=candidate, admission=admission),),
        evidence_packages=tuple(packages.values()),
    )
    receipt = produce_strategy_watchlist(directory, request)
    return result | receipt | {'status': 'PUBLISHED_PAPER_SIGNAL'}
