"""Read-only collection views; no execution or portfolio inference."""
from pathlib import Path

from app.paper.shadow_collection import LABEL, audit_missed_session
from app.paper.shadow_exits import ShadowExitPolicy, audit_exit_event
from app.paper.shadow_facts import ForwardFactBundle
from app.paper.shadow_fills import ShadowFillPolicy
from app.paper.shadow_ledger import audit_candidate_event
from app.paper.shadow_positions import audit_position_open_event
from app.paper.shadow_records import ShadowSession, ShadowWatchlist
from app.research.historical_evidence import HistoricalEvidencePackage


def _base() -> dict:
    return {
        "schema_version": "shadow-collection-view-v1",
        "label": LABEL,
        "scope": "ONE COLLECTION RECORD / NOT A COMPLETE DAILY PORTFOLIO",
        "scoring": "NOT SCORED",
        "market_status": "UNKNOWN / NOT AUDITED BY THIS VIEW",
        "open_paper_positions": {"status": "NOT EVALUATED"},
        "closed_paper_trades": {"status": "NOT EVALUATED"},
        "performance": {
            "status": "NOT EVALUATED",
            **{key: None for key in (
                "nav", "cumulative_return", "realized_return", "max_drawdown",
                "hit_rate", "expectancy", "egx_attribution", "us_attribution",
                "combined_attribution",
            )},
        },
    }


def watchlist_collection_view(
    directory: Path,
    watchlist: ShadowWatchlist,
    evidence_packages: tuple[HistoricalEvidencePackage, ...],
) -> dict:
    """Display every candidate only after auditing its durable upstream chain.

    A candidate's frozen liquidity/risk fields describe its declaration, not
    execution admission. Unknown downstream state is never an empty portfolio.
    """
    event = audit_candidate_event(directory, watchlist, evidence_packages)
    return _base() | {
        "record_id": event["record_id"],
        "market": event["market"],
        "market_date": event["market_date"],
        "collection_status": "FROZEN",
        "generated_at": watchlist.generated_at.isoformat(),
        "information_cutoff": event["information_cutoff"],
        "decision_cutoff": event["decision_cutoff"],
        "candidate_count": event["candidate_count"],
        "candidates": [
            dict(candidate, label=LABEL, execution_status="NOT EVALUATED")
            for candidate in event["candidates"]
        ],
        "evidence_references": [item.model_dump(mode="json") for item in watchlist.evidence],
        "audit_references": {
            key: event[key] for key in (
                "event_id", "completion_sha256", "watchlist_sha256", "document_sha256",
            )
        },
    }


def missed_collection_view(
    directory: Path,
    *,
    record_id: str,
    session: ShadowSession,
    session_package: HistoricalEvidencePackage,
) -> dict:
    """Display an audited missed receipt without reconstructing candidates."""
    receipt = audit_missed_session(
        directory, record_id=record_id, session=session, session_package=session_package,
    )
    return _base() | {
        "record_id": receipt["record_id"],
        "market": receipt["market"],
        "market_date": receipt["market_date"],
        "collection_status": "MISSED / NOT SCORED",
        "decision_cutoff": receipt["decision_cutoff"],
        "recorded_at": receipt["completed_at"],
        "reason": receipt["reason"],
        "candidate_count": None,
        "candidates": None,
        "audit_references": {
            "session_evidence_package_id": receipt["session_evidence_package_id"],
        },
    }


def position_open_view(
    directory: Path,
    watchlist: ShadowWatchlist,
    watchlist_packages: tuple[HistoricalEvidencePackage, ...],
    facts: ForwardFactBundle,
    fact_packages: tuple[HistoricalEvidencePackage, ...],
    fill_policy: ShadowFillPolicy,
    fill_packages: tuple[HistoricalEvidencePackage, ...],
) -> dict:
    """Display an audited entry-position record without inferring current state."""
    event = audit_position_open_event(
        directory, watchlist, watchlist_packages, facts, fact_packages,
        fill_policy, fill_packages,
    )
    return _base() | {
        "record_id": event["watchlist_record_id"],
        "market": event["market"],
        "collection_status": "SIMULATED OPEN AT ENTRY",
        "current_position_status": "UNKNOWN / EXIT NOT AUDITED BY THIS VIEW",
        "position": {
            "candidate_position_key": event["candidate_position_key"],
            "entry": event["entry"],
            "initial_stop": event["initial_stop"],
            "initial_targets": event["initial_targets"],
            "holding_window": event["holding_window"],
        },
        "audit_references": {
            "position_event_id": event["event_id"],
            "fill_event_id": event["fill_event_id"],
            "fill_event_sha256": event["fill_event_sha256"],
        },
    }


def exit_evaluation_view(
    directory: Path,
    watchlist: ShadowWatchlist,
    watchlist_packages: tuple[HistoricalEvidencePackage, ...],
    facts: ForwardFactBundle,
    fact_packages: tuple[HistoricalEvidencePackage, ...],
    fill_policy: ShadowFillPolicy,
    fill_packages: tuple[HistoricalEvidencePackage, ...],
    exit_policy: ShadowExitPolicy,
    exit_packages: tuple[HistoricalEvidencePackage, ...],
) -> dict:
    """Display one audited conservative exit evaluation without calculating P&L."""
    event = audit_exit_event(
        directory, watchlist, watchlist_packages, facts, fact_packages,
        fill_policy, fill_packages, exit_policy, exit_packages,
    )
    evaluation = event["evaluation"]
    return _base() | {
        "record_id": watchlist.record_id,
        "market": watchlist.session.market,
        "collection_status": "EXIT EVALUATED",
        "position_status": evaluation["status"],
        "exit_evaluation": evaluation,
        "audit_references": {
            "exit_event_id": event["event_id"],
            "position_event_id": event["position_event_id"],
            "position_event_sha256": event["position_event_sha256"],
            "fact_record_id": event["fact_record_id"],
            "exit_package_ids": event["exit_package_ids"],
        },
    }
