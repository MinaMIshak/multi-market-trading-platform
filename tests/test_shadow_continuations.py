"""Artificial continuation fixtures only; no authentic sessions, exits, or P&L."""
import json
from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.paper import shadow_continuations, shadow_positions
from app.paper.shadow_continuations import (
    ContinuationCalendarDay, ForwardContinuationBundle,
)
from app.paper.shadow_facts import (
    ACTION_FIELDS, BAR_FIELDS, IDENTITY_FIELDS, SESSION_FIELDS, TRADING_STATUS_FIELDS,
)
from app.paper.shadow_records import ShadowEvidenceReference
from tests.test_shadow_collection import package
from tests.test_shadow_positions import setup_position


def prepared_continuation(tmp_path, monkeypatch):
    args, now, _ = setup_position(tmp_path, monkeypatch)
    shadow_positions.append_position_open_event(tmp_path, *args)
    original = args[2]
    start = original.session.opens_at + timedelta(days=1)
    available = start + timedelta(minutes=6)
    reference = ShadowEvidenceReference(
        evidence_id="7" * 64, source_authority="official fixture authority",
        source_locator="fixture://continuation", artifact_sha256="7" * 64,
        available_at=available,
    )
    evidence = package("7", reference, tuple(
        SESSION_FIELDS | IDENTITY_FIELDS | ACTION_FIELDS | BAR_FIELDS | TRADING_STATUS_FIELDS
    ))
    facts = ForwardContinuationBundle(
        continuation_id="session-2",
        calendar_days=(ContinuationCalendarDay(
            market="US", market_date=original.session.market_date + timedelta(days=1),
            calendar_mic="XNYS", state="OPEN", opens_at=start,
            closes_at=start + timedelta(hours=6, minutes=30),
            evidence_package_id=evidence.identity,
        ),),
        identity=original.identity.model_copy(update={
            "effective_through": original.session.market_date + timedelta(days=1),
            "evidence_package_id": evidence.identity,
        }),
        action_coverage=original.action_coverage.model_copy(update={
            "coverage_through": original.session.market_date + timedelta(days=1),
            "evidence_package_id": evidence.identity,
        }),
        trading_status=original.trading_status.model_copy(update={
            "coverage_start": start, "coverage_end": start + timedelta(minutes=5),
            "evidence_package_id": evidence.identity,
        }),
        bars=(original.bars[0].model_copy(update={
            "market_date": original.session.market_date + timedelta(days=1),
            "interval_start": start, "interval_end": start + timedelta(minutes=5),
            "available_at": available, "source_row": "fixture-continuation-row-1",
            "evidence_package_id": evidence.identity,
        }),),
    )
    monkeypatch.setattr(shadow_continuations, "_now", lambda: available + timedelta(minutes=1))
    return args, facts, (evidence,), available


def test_admits_and_audits_later_session_without_execution(tmp_path, monkeypatch):
    args, facts, evidence, _ = prepared_continuation(tmp_path, monkeypatch)
    path = shadow_continuations.append_continuation_event(
        tmp_path, *args, facts, evidence,
    )
    event = shadow_continuations.audit_continuation_event(
        tmp_path, *args, facts, evidence,
    )
    assert event == json.loads(path.read_bytes())
    assert event["facts"] == facts.model_dump(mode="json")
    assert event["execution_status"] == "NO EXIT OR PNL INFERENCE"
    assert event["scoring"] == "NOT SCORED"
    with pytest.raises(FileExistsError):
        shadow_continuations.append_continuation_event(tmp_path, *args, facts, evidence)


def test_same_position_and_date_cannot_publish_competing_facts(tmp_path, monkeypatch):
    args, facts, evidence, _ = prepared_continuation(tmp_path, monkeypatch)
    path = shadow_continuations.append_continuation_event(tmp_path, *args, facts, evidence)
    competing = facts.model_copy(update={
        "continuation_id": "alternate-session-2",
        "bars": (facts.bars[0].model_copy(update={"close": facts.bars[0].close + 1}),),
    })
    with pytest.raises(FileExistsError):
        shadow_continuations.append_continuation_event(
            tmp_path, *args, competing, evidence,
        )
    assert json.loads(path.read_bytes())["facts"] == facts.model_dump(mode="json")


def test_continuation_requires_every_intervening_calendar_date(tmp_path, monkeypatch):
    args, facts, _, _ = prepared_continuation(tmp_path, monkeypatch)
    day = facts.calendar_days[0]
    shift = timedelta(days=1)
    skipped = ForwardContinuationBundle(**(facts.model_dump(mode="python") | {
        "calendar_days": (day.model_copy(update={
            "market_date": day.market_date + shift,
            "opens_at": day.opens_at + shift, "closes_at": day.closes_at + shift,
        }),),
        "identity": facts.identity.model_copy(update={
            "effective_through": facts.identity.effective_through + shift,
        }),
        "action_coverage": facts.action_coverage.model_copy(update={
            "coverage_from": facts.action_coverage.coverage_from + shift,
            "coverage_through": facts.action_coverage.coverage_through + shift,
        }),
        "trading_status": facts.trading_status.model_copy(update={
            "coverage_start": facts.trading_status.coverage_start + shift,
            "coverage_end": facts.trading_status.coverage_end + shift,
        }),
        "bars": (facts.bars[0].model_copy(update={
            "market_date": facts.bars[0].market_date + shift,
            "interval_start": facts.bars[0].interval_start + shift,
            "interval_end": facts.bars[0].interval_end + shift,
            "available_at": facts.bars[0].available_at + shift,
        }),),
    }))
    with pytest.raises(ValueError, match="next calendar date"):
        shadow_continuations.append_continuation_event(
            tmp_path, *args, skipped, (),
        )


def test_model_rejects_calendar_gap_and_inferred_closed_hours(tmp_path, monkeypatch):
    _, facts, _, _ = prepared_continuation(tmp_path, monkeypatch)
    day = facts.calendar_days[0]
    closed = day.model_copy(update={
        "state": "CLOSED", "opens_at": None, "closes_at": None,
    })
    later = day.model_copy(update={
        "market_date": day.market_date + timedelta(days=2),
        "opens_at": day.opens_at + timedelta(days=2),
        "closes_at": day.closes_at + timedelta(days=2),
    })
    with pytest.raises(ValidationError, match="every intervening date"):
        ForwardContinuationBundle(**(
            facts.model_dump(mode="python") | {"calendar_days": (closed, later)}
        ))
    with pytest.raises(ValidationError, match="closed calendar day cannot have session hours"):
        ContinuationCalendarDay(**(day.model_dump(mode="python") | {"state": "CLOSED"}))


def test_model_cannot_skip_an_intervening_open_session(tmp_path, monkeypatch):
    _, facts, _, _ = prepared_continuation(tmp_path, monkeypatch)
    day = facts.calendar_days[0]
    later = day.model_copy(update={
        "market_date": day.market_date + timedelta(days=1),
        "opens_at": day.opens_at + timedelta(days=1),
        "closes_at": day.closes_at + timedelta(days=1),
    })
    with pytest.raises(ValidationError, match="cannot skip an intervening open session"):
        ForwardContinuationBundle(**(
            facts.model_dump(mode="python") | {
                "calendar_days": (day, later),
                "identity": facts.identity.model_copy(update={
                    "effective_through": later.market_date,
                }),
                "action_coverage": facts.action_coverage.model_copy(update={
                    "coverage_through": later.market_date,
                }),
                "trading_status": facts.trading_status.model_copy(update={
                    "coverage_start": later.opens_at,
                    "coverage_end": later.opens_at + timedelta(minutes=5),
                }),
                "bars": (facts.bars[0].model_copy(update={
                    "market_date": later.market_date,
                    "interval_start": later.opens_at,
                    "interval_end": later.opens_at + timedelta(minutes=5),
                    "available_at": later.opens_at + timedelta(minutes=6),
                }),),
            }
        ))


def test_rejects_identity_substitution_and_incomplete_actions(tmp_path, monkeypatch):
    args, facts, evidence, _ = prepared_continuation(tmp_path, monkeypatch)
    changed_identity = facts.model_copy(update={
        "identity": facts.identity.model_copy(update={"ticker": "TWTR"}),
        "bars": (facts.bars[0].model_copy(update={"ticker": "TWTR"}),),
    })
    with pytest.raises(ValueError, match="does not bind the original position"):
        shadow_continuations.append_continuation_event(
            tmp_path, *args, changed_identity, evidence,
        )
    with pytest.raises(ValidationError, match="actions do not cover"):
        ForwardContinuationBundle(**(
            facts.model_dump(mode="python") | {
                "action_coverage": facts.action_coverage.model_copy(update={
                    "coverage_through": args[2].session.market_date,
                }),
            }
        ))


def test_rejects_unmatched_evidence_and_tampering(tmp_path, monkeypatch):
    args, facts, evidence, _ = prepared_continuation(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="must match references exactly"):
        shadow_continuations.append_continuation_event(tmp_path, *args, facts, ())
    path = shadow_continuations.append_continuation_event(tmp_path, *args, facts, evidence)
    event = json.loads(path.read_bytes())
    event["execution_status"] = "EXIT CREATED"
    path.write_text(json.dumps(event))
    with pytest.raises(ValueError, match="does not bind authenticated inputs"):
        shadow_continuations.audit_continuation_event(tmp_path, *args, facts, evidence)
