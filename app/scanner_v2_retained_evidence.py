"""Read retained EGX identity/calendar evidence for one ranking, as of its generation.

This is the read-side input step for the trusted host card-report job: it turns
manifest-verified ranking bytes plus already-retained evidence into the
``RetainedEgxRankBinding`` that ``publish_egx_card_report`` consumes. It:

1. checks the ranking bytes against the manifest SHA-256 and reads only the
   session, generation time and published ticker scope (strict JSON);
2. reads the retained EGID admission observation with the existing
   ``SecurityMasterRepository.get_admission_observation`` (never
   ``retain_admission_observation``/``observe_admission`` as a substitute) and
   binds it with the existing ``bind_observed_egid_identities`` at the ranking
   generation time;
3. reads the retained calendar observation with the existing
   ``TradingRepository.get_calendar_observation`` at that same decision time;
4. composes with the existing ``compose_egx_rank_binding``.

Point in time: the ranking ``generated_at`` is the only decision time. Evidence
observed after it is rejected by the existing gates, so later observations never
backdate a decision. Nothing is written, retained, admitted or promoted: identity
stays INTERNAL_SNAPSHOT_VERIFIED, calendar NOT_ESTABLISHED, runtime_activation
false, LIVE_MONEY=false. There is no scheduler or runtime caller yet.
"""
from datetime import date, datetime
from hashlib import sha256
import json

from app.data.models import RawArtifactManifest
from app.scanner_v2_egx_rank import _invalid_constant, _ticker, _unique_object
from app.scanner_v2_identity import MAX_BYTES, bind_observed_egid_identities
from app.scanner_v2_retained_binding import compose_egx_rank_binding
from app.strategies.contracts import aware

IDENTITY_PROVIDER = 'egid'
EVIDENCE_REASONS = (
    'RANKING_INVALID', 'IDENTITY_OBSERVATION_UNAVAILABLE', 'IDENTITY_REJECTED',
    'CALENDAR_OBSERVATION_UNAVAILABLE', 'CALENDAR_REJECTED', 'BINDING_REJECTED')


class RetainedEvidenceError(ValueError):
    """Retained evidence unusable; ``reason`` is one of EVIDENCE_REASONS."""

    def __init__(self, reason, detail=None):
        super().__init__(reason if detail is None else f'{reason}: {detail}')
        self.reason, self.detail = reason, detail


def ranking_scope(artifact, *, expected_sha256):
    """Return (session, generated_at, sorted ticker tuple) from exact ranking bytes.

    Only the fields needed to look up evidence are read here; the full ranking
    contract is still enforced later by the unchanged ``bind_egx_rank_report``.
    """
    try:
        if type(artifact) is not bytes or len(artifact) > MAX_BYTES:
            raise ValueError('bounded exact ranking bytes required')
        if type(expected_sha256) is not str or sha256(artifact).hexdigest() != expected_sha256:
            raise ValueError('ranking artifact hash mismatch')
        report = json.loads(artifact, object_pairs_hook=_unique_object,
                            parse_constant=_invalid_constant)
        json.dumps(report, allow_nan=False)
        if (type(report) is not dict or report.get('schema') != 'egx-ranking-report-v1'
                or type(report.get('based_on_session')) is not str
                or type(report.get('generated_at')) is not str
                or type(report.get('symbols')) is not list):
            raise ValueError('invalid EGX ranking report')
        session = date.fromisoformat(report['based_on_session'])
        generated_at = aware(datetime.fromisoformat(report['generated_at']))
        tickers = [_ticker(r) for r in report['symbols']]
        if len(set(tickers)) != len(tickers):
            raise ValueError('duplicate EGX ranking record')
    except (ValueError, TypeError, RecursionError) as exc:
        raise RetainedEvidenceError('RANKING_INVALID', str(exc)) from None
    return session, generated_at, tuple(sorted(tickers))


def load_retained_egx_rank_binding(artifact, *, expected_sha256, identity_artifact,
                                   identity_manifest, security_master, trading,
                                   calendar_sha256):
    """Compose the retained binding for one ranking from retained evidence only.

    ``security_master`` and ``trading`` are the existing repositories (trusted
    host database). ``identity_artifact``/``identity_manifest`` are the exact
    preserved EGID bytes and their manifest; ``calendar_sha256`` names the
    retained calendar observation. Raises ``RetainedEvidenceError``; nothing is
    written on any path.
    """
    session, generated_at, requested = ranking_scope(artifact, expected_sha256=expected_sha256)

    if type(identity_manifest) is not RawArtifactManifest:
        raise RetainedEvidenceError('IDENTITY_REJECTED', 'raw artifact manifest required')
    try:
        observation = security_master.get_admission_observation(
            provider=IDENTITY_PROVIDER, snapshot_date=session,
            artifact_sha256=identity_manifest.sha256)
    except KeyError:
        raise RetainedEvidenceError('IDENTITY_OBSERVATION_UNAVAILABLE') from None
    except (ValueError, TypeError) as exc:
        raise RetainedEvidenceError('IDENTITY_REJECTED', str(exc)) from None
    try:
        identity = bind_observed_egid_identities(
            identity_artifact, manifest=identity_manifest, observation=observation,
            session=session, observed_at=generated_at, requested=requested)
    except (ValueError, TypeError, RecursionError) as exc:
        raise RetainedEvidenceError('IDENTITY_REJECTED', str(exc)) from None

    if type(calendar_sha256) is not str:
        raise RetainedEvidenceError('CALENDAR_REJECTED', 'calendar digest required')
    try:
        calendar = trading.get_calendar_observation(
            artifact_sha256=calendar_sha256, decision_time=generated_at)
    except KeyError:
        raise RetainedEvidenceError('CALENDAR_OBSERVATION_UNAVAILABLE') from None
    except (ValueError, TypeError, RecursionError) as exc:
        raise RetainedEvidenceError('CALENDAR_REJECTED', str(exc)) from None

    try:
        if calendar.session != session:
            raise ValueError('calendar session differs from ranking session')
        return compose_egx_rank_binding(identity=identity, calendar=calendar)
    except (ValueError, TypeError, AttributeError) as exc:
        raise RetainedEvidenceError('BINDING_REJECTED', str(exc)) from None
