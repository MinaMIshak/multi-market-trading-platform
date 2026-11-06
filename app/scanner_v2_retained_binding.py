"""Compose retained identity and calendar observations into an EgxRankBinding.

Offline composition only: no file, network or database IO and no runtime caller.
It never upgrades admission. Identity keeps INTERNAL_SNAPSHOT_VERIFIED and the
calendar keeps NOT_ESTABLISHED; combining them is not schedule admission,
membership certification, source-rights approval or trading admission.

Freshness policy FRESH_UNTIL_NEXT_SESSION_OPEN_V1 is derived, not invented: the
retained next-session open ends the pre-open window in which the published
EGX-RANK-v1 plan was formed, so a card viewed at or after that open is labeled
DATA_STALE. All evidence must be available before the ranking generation time
that becomes the decision time; later observations never backdate a decision.
"""
from datetime import datetime
import re
from typing import Literal

from pydantic import model_validator

from app.scanner_v2_calendar import CalendarProjection
from app.scanner_v2_egx_rank import EgxRankBinding, bind_egx_rank_report
from app.scanner_v2_identity import ObservedIdentityProjection
from app.strategies.contracts import Contract, aware

FRESHNESS_POLICY = 'FRESH_UNTIL_NEXT_SESSION_OPEN_V1'
_SHA256 = re.compile(r'^[0-9a-f]{64}$')


def _receipt_evidence_id(receipt_sha256):
    return f'egid-admission-receipt-sha256:{receipt_sha256}'


def _available_at(identity, calendar):
    # Conservative: the projected raw bytes and the admission observation must
    # both exist, as must the retained calendar observation.
    return max(identity.identity.received_at, identity.available_at, calendar.observed_at)


class RetainedEgxRankBinding(Contract):
    schema_version: Literal['scanner-v2-egx-retained-binding-v1'] = 'scanner-v2-egx-retained-binding-v1'
    market: Literal['EGX'] = 'EGX'
    binding: EgxRankBinding
    identity: ObservedIdentityProjection
    calendar: CalendarProjection
    evidence_available_at: datetime
    freshness_policy: Literal['FRESH_UNTIL_NEXT_SESSION_OPEN_V1'] = FRESHNESS_POLICY
    identity_admission: Literal['INTERNAL_SNAPSHOT_VERIFIED'] = 'INTERNAL_SNAPSHOT_VERIFIED'
    calendar_admission: Literal['NOT_ESTABLISHED'] = 'NOT_ESTABLISHED'
    runtime_activation: Literal[False] = False
    live_money: Literal[False] = False

    @model_validator(mode='after')
    def check(self):
        b, i, c = self.binding, self.identity, self.calendar
        raw = i.identity
        for digest in (raw.artifact_sha256, i.receipt_sha256, c.artifact_sha256):
            if type(digest) is not str or not _SHA256.match(digest):
                raise ValueError('lowercase SHA-256 evidence digest required')
        if (raw.evidence_id != f'egid-security-master-sha256:{raw.artifact_sha256}'
                or c.evidence_id != f'egx-calendar-sha256:{c.artifact_sha256}'):
            raise ValueError('evidence identifier does not match retained digest')
        if not raw.session == c.session == b.session:
            raise ValueError('identity, calendar and binding sessions differ')
        # Exact raw retries may be received after the original admission was
        # observed; both must precede the projection's own observation time.
        if not (aware(raw.received_at) <= aware(raw.observed_at)
                and aware(i.available_at) <= raw.observed_at):
            raise ValueError('identity observation out of order')
        if not (aware(c.data_as_of) <= aware(c.observed_at) < aware(c.next_session_open)):
            raise ValueError('calendar observation outside completed pre-open window')
        available = _available_at(i, c)
        if aware(self.evidence_available_at) != available:
            raise ValueError('evidence availability must be the latest retained observation')
        if available >= c.next_session_open:
            raise ValueError('retained evidence unavailable before next-session open')
        if (b.next_session != c.next_session or b.next_session_open != c.next_session_open
                or b.data_as_of != c.data_as_of or b.valid_until != c.valid_until
                or b.fresh_until != c.next_session_open
                or b.instrument_ids != raw.instrument_ids
                or b.calendar_evidence_id != c.evidence_id
                or b.identity_evidence_id != _receipt_evidence_id(i.receipt_sha256)):
            raise ValueError('binding does not match retained evidence')
        return self


def compose_egx_rank_binding(*, identity, calendar):
    """Build an EgxRankBinding solely from retained, revalidated projections."""
    if type(identity) is not ObservedIdentityProjection or type(calendar) is not CalendarProjection:
        raise ValueError('retained identity and calendar projections required')
    # Revalidate unchecked copies (model_copy/model_construct bypass validation).
    identity = ObservedIdentityProjection.model_validate(identity.model_dump(mode='python'))
    calendar = CalendarProjection.model_validate(calendar.model_dump(mode='python'))
    binding = dict(
        session=calendar.session, next_session=calendar.next_session,
        next_session_open=calendar.next_session_open, data_as_of=calendar.data_as_of,
        fresh_until=calendar.next_session_open, valid_until=calendar.valid_until,
        instrument_ids=identity.identity.instrument_ids,
        calendar_evidence_id=calendar.evidence_id,
        identity_evidence_id=_receipt_evidence_id(identity.receipt_sha256))
    return RetainedEgxRankBinding.model_validate(dict(
        binding=binding, identity=identity.model_dump(mode='python'),
        calendar=calendar.model_dump(mode='python'),
        evidence_available_at=_available_at(identity, calendar)))


def bind_retained_egx_rank_report(artifact, *, expected_sha256, evidence):
    """Reuse bind_egx_rank_report; require evidence known at ranking generation."""
    if type(evidence) is not RetainedEgxRankBinding:
        raise ValueError('RetainedEgxRankBinding required')
    evidence = RetainedEgxRankBinding.model_validate(evidence.model_dump(mode='python'))
    result = bind_egx_rank_report(artifact, expected_sha256=expected_sha256,
                                  binding=evidence.binding)
    generated = aware(datetime.fromisoformat(result['generated_at']))
    if generated < evidence.evidence_available_at:
        raise ValueError('retained evidence unavailable at ranking generation')
    return {
        **result,
        'binding_schema_version': evidence.schema_version,
        'freshness_policy': evidence.freshness_policy,
        'identity_admission': evidence.identity_admission,
        'calendar_admission': evidence.calendar_admission,
        'identity_exclusions': [
            {'symbol': symbol, 'reason': reason}
            for symbol, reason in evidence.identity.identity.exclusions],
        'evidence_available_at': evidence.evidence_available_at.isoformat(),
        'retained_evidence': evidence.model_dump(mode='json'),
        'runtime_activation': False,
    }
