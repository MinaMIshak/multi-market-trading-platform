"""Read-only collection views; no portfolio inference."""
from datetime import datetime
from decimal import Context, Decimal, localcontext
from pathlib import Path

from app.paper.shadow_allocations import (
    audit_capital_settlement, audit_continuation_capital_settlement,
)
from app.paper.shadow_collection import LABEL, audit_missed_session
from app.paper.shadow_exits import (
    ShadowExitPolicy, audit_continuation_exit_event, audit_exit_event,
)
from app.paper.shadow_facts import ForwardFactBundle
from app.paper.shadow_fills import ShadowFillPolicy, audit_fill_event
from app.paper.shadow_ledger import audit_candidate_event
from app.paper.shadow_positions import audit_position_open_event
from app.paper.shadow_portfolio import ShadowPortfolioPolicy
from app.paper.shadow_records import ShadowSession, ShadowWatchlist
from app.paper.shadow_triggers import audit_trigger_event
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


def _bar_end_elapsed(entry: dict, observed_interval_end: str) -> str:
    """Measure authenticated bar-end elapsed time without inventing fill time."""
    started = datetime.fromisoformat(entry["interval_end"])
    observed = datetime.fromisoformat(observed_interval_end)
    elapsed = observed - started
    if elapsed.total_seconds() < 0:
        raise ValueError("observed bar precedes authenticated entry bar")
    return str(elapsed)


def _attach_open_mark(
    view: dict,
    directory: Path,
    watchlist: ShadowWatchlist,
    watchlist_packages: tuple[HistoricalEvidencePackage, ...],
    facts: ForwardFactBundle,
    fact_packages: tuple[HistoricalEvidencePackage, ...],
    fill_policy: ShadowFillPolicy,
    fill_packages: tuple[HistoricalEvidencePackage, ...],
    observed_facts,
    evaluation: dict,
) -> dict:
    """Attach one authenticated as-of gross mark without inventing liquidation."""
    if type(evaluation) is not dict or evaluation.get("status") != "OPEN":
        raise ValueError("open mark requires authenticated OPEN evaluation")

    sequence = evaluation["evaluated_through_sequence"]
    mark = next(
        (bar for bar in observed_facts.bars if bar.sequence == sequence),
        None,
    )
    if mark is None:
        raise ValueError("open exit evaluation does not bind an admitted mark")

    position = audit_position_open_event(
        directory, watchlist, watchlist_packages, facts, fact_packages,
        fill_policy, fill_packages,
    )
    entry = position["entry"]

    with localcontext(Context(prec=34)):
        entry_notional = Decimal(entry["notional"])
        gross_market_value = Decimal(entry["quantity"]) * mark.close
        gross_unrealized_pnl = gross_market_value - entry_notional
        gross_unrealized_return = gross_unrealized_pnl / entry_notional

    view["open_paper_positions"] = {
        "status": "ONE AUTHENTICATED OPEN POSITION AS OF OBSERVED BAR",
        "position": {
            "candidate_position_key": position["candidate_position_key"],
            "ticker": entry["ticker"],
            "instrument_id": entry["instrument_id"],
            "quantity": entry["quantity"],
            "currency": entry["currency"],
            "entry_fill_price": entry["fill_price"],
            "initial_stop": position["initial_stop"],
            "initial_targets": position["initial_targets"],
            "holding_window": position["holding_window"],
            "mark_price": str(mark.close),
            "gross_market_value": str(gross_market_value),
            "gross_unrealized_pnl": str(gross_unrealized_pnl),
            "gross_unrealized_return": str(gross_unrealized_return),
            "gross_unrealized_pnl_status": (
                "AS OF OBSERVED BAR / BEFORE FEES AND LIQUIDATION SLIPPAGE"
            ),
            "marked_through_sequence": mark.sequence,
            "mark_interval_end": mark.interval_end.isoformat(),
            "mark_known_at": mark.available_at.isoformat(),
            "observed_bar_end_elapsed": _bar_end_elapsed(
                entry, mark.interval_end.isoformat(),
            ),
            "observed_bar_end_elapsed_status": (
                "BOUNDED OBSERVATION WINDOW / EXACT INTRABAR FILL TIME UNKNOWN"
            ),
            "unrealized_pnl": None,
            "unrealized_pnl_status": (
                "UNKNOWN / NO AUTHENTICATED LIQUIDATION SLIPPAGE AND COST"
            ),
        },
    }
    return view


def _attach_closed_trade(
    view: dict,
    directory: Path,
    watchlist: ShadowWatchlist,
    watchlist_packages: tuple[HistoricalEvidencePackage, ...],
    facts: ForwardFactBundle,
    fact_packages: tuple[HistoricalEvidencePackage, ...],
    fill_policy: ShadowFillPolicy,
    fill_packages: tuple[HistoricalEvidencePackage, ...],
    evaluation: dict,
) -> dict:
    """Attach exact native one-trade arithmetic only for an authenticated close."""
    if (
        type(evaluation) is not dict
        or evaluation.get("status") != "CLOSED"
        or type(evaluation.get("exit")) is not dict
    ):
        raise ValueError("closed trade view requires authenticated CLOSED exit")

    position = audit_position_open_event(
        directory, watchlist, watchlist_packages, facts, fact_packages,
        fill_policy, fill_packages,
    )
    entry, exit_fill = position["entry"], evaluation["exit"]

    if exit_fill["currency"] != entry["currency"]:
        raise ValueError("closed trade currency mismatch")

    with localcontext(Context(prec=34)):
        entry_notional = Decimal(entry["notional"])
        entry_cost = Decimal(entry["entry_cost"])
        exit_notional = Decimal(exit_fill["notional"])
        exit_cost = Decimal(exit_fill["exit_cost"])
        gross_pnl = exit_notional - entry_notional
        gross_return = gross_pnl / entry_notional
        net_pnl = gross_pnl - entry_cost - exit_cost
        capital_outlay = entry_notional + entry_cost
        net_return = net_pnl / capital_outlay

    view["closed_paper_trades"] = {
        "status": "ONE AUTHENTICATED CLOSED PAPER TRADE",
        "trade": {
            "candidate_position_key": position["candidate_position_key"],
            "ticker": entry["ticker"],
            "instrument_id": entry["instrument_id"],
            "quantity": entry["quantity"],
            "currency": entry["currency"],
            "entry_fill_price": entry["fill_price"],
            "entry_notional": entry["notional"],
            "entry_cost": entry["entry_cost"],
            "exit_fill_price": exit_fill["fill_price"],
            "exit_notional": exit_fill["notional"],
            "exit_cost": exit_fill["exit_cost"],
            "exit_reason": evaluation["reason"],
            "initial_stop": position["initial_stop"],
            "initial_targets": position["initial_targets"],
            "holding_window": position["holding_window"],
            "gross_pnl": str(gross_pnl),
            "gross_return": str(gross_return),
            "net_pnl": str(net_pnl),
            "net_return": str(net_return),
            "entry_known_at": entry["known_at"],
            "exit_known_at": exit_fill["known_at"],
            "observed_bar_end_elapsed": _bar_end_elapsed(
                entry, exit_fill["interval_end"],
            ),
            "observed_bar_end_elapsed_status": (
                "BOUNDED OBSERVATION WINDOW / EXACT INTRABAR FILL TIME UNKNOWN"
            ),
        },
    }
    return view


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


def entry_fill_view(
    directory: Path,
    watchlist: ShadowWatchlist,
    watchlist_packages: tuple[HistoricalEvidencePackage, ...],
    facts: ForwardFactBundle,
    fact_packages: tuple[HistoricalEvidencePackage, ...],
    fill_policy: ShadowFillPolicy,
    fill_packages: tuple[HistoricalEvidencePackage, ...],
) -> dict:
    """Display an audited entry attempt, including capacity-rejected no fills."""
    event = audit_fill_event(
        directory, watchlist, watchlist_packages, facts, fact_packages,
        fill_policy, fill_packages,
    )
    return _base() | {
        "record_id": watchlist.record_id,
        "market": watchlist.session.market,
        "market_date": watchlist.session.market_date.isoformat(),
        "collection_status": "ENTRY FILL EVALUATED",
        "execution_status": event["execution_status"],
        "current_position_status": "UNKNOWN / POSITION AND EXIT NOT AUDITED BY THIS VIEW",
        "recorded_at": event["recorded_at"],
        "fill": event["fill"],
        "fill_policy": event["policy"],
        "audit_references": {
            "fill_event_id": event["event_id"],
            "trigger_event_id": event["trigger_event_id"],
            "trigger_event_sha256": event["trigger_event_sha256"],
            "fill_package_ids": event["fill_package_ids"],
        },
    }


def trigger_evaluation_view(
    directory: Path,
    watchlist: ShadowWatchlist,
    watchlist_packages: tuple[HistoricalEvidencePackage, ...],
    facts: ForwardFactBundle,
    fact_packages: tuple[HistoricalEvidencePackage, ...],
) -> dict:
    """Display an audited trigger outcome without inferring a fill or position."""
    event = audit_trigger_event(
        directory, watchlist, watchlist_packages, facts, fact_packages,
    )
    return _base() | {
        "record_id": watchlist.record_id,
        "market": watchlist.session.market,
        "market_date": watchlist.session.market_date.isoformat(),
        "collection_status": "ENTRY TRIGGER EVALUATED",
        "execution_status": event["execution_status"],
        "trigger_evaluation": event["evaluation"],
        "current_position_status": "NO FILL OR POSITION CREATED BY THIS EVENT",
        "recorded_at": event["recorded_at"],
        "audit_references": {
            "trigger_event_id": event["event_id"],
            "fact_event_id": event["fact_event_id"],
            "fact_event_sha256": event["fact_event_sha256"],
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
    *, evaluation_facts: ForwardFactBundle | None = None,
    evaluation_fact_packages: tuple[HistoricalEvidencePackage, ...] | None = None,
) -> dict:
    """Display one audited exit and exact native P&L only when it is closed."""
    event = audit_exit_event(
        directory, watchlist, watchlist_packages, facts, fact_packages,
        fill_policy, fill_packages, exit_policy, exit_packages,
        evaluation_facts=evaluation_facts,
        evaluation_fact_packages=evaluation_fact_packages,
    )
    observed_facts = facts if evaluation_facts is None else evaluation_facts
    evaluation = event["evaluation"]
    view = _base() | {
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
            "fact_event_id": event["fact_event_id"],
            "fact_event_sha256": event["fact_event_sha256"],
            "exit_package_ids": event["exit_package_ids"],
        },
    }
    if evaluation["status"] == "OPEN":
        return _attach_open_mark(
            view, directory, watchlist, watchlist_packages, facts, fact_packages,
            fill_policy, fill_packages, observed_facts, evaluation,
        )
    if evaluation["status"] != "CLOSED":
        return view

    return _attach_closed_trade(
        view, directory, watchlist, watchlist_packages, facts, fact_packages,
        fill_policy, fill_packages, evaluation,
    )


def continuation_exit_evaluation_view(
    directory: Path,
    watchlist: ShadowWatchlist,
    watchlist_packages: tuple[HistoricalEvidencePackage, ...],
    facts: ForwardFactBundle,
    fact_packages: tuple[HistoricalEvidencePackage, ...],
    fill_policy: ShadowFillPolicy,
    fill_packages: tuple[HistoricalEvidencePackage, ...],
    continuations: tuple,
    continuation_packages: tuple,
    exit_policy: ShadowExitPolicy,
    exit_packages: tuple[HistoricalEvidencePackage, ...],
) -> dict:
    """Display a reaudited continuation exit; realized P&L exists only if CLOSED."""
    event = audit_continuation_exit_event(
        directory, watchlist, watchlist_packages, facts, fact_packages,
        fill_policy, fill_packages, continuations, continuation_packages,
        exit_policy, exit_packages,
    )
    evaluation = event["result"]

    view = _base() | {
        "record_id": watchlist.record_id,
        "market": watchlist.session.market,
        "collection_status": "CONTINUATION EXIT EVALUATED",
        "position_status": evaluation["status"],
        "exit_evaluation": evaluation,
        "entry_session_exit_evaluation": event["entry_session_evaluation"],
        "continuation_evaluations": event["evaluations"],
        "audit_references": {
            "exit_event_id": event["event_id"],
            "position_event_id": event["position_event_id"],
            "position_event_sha256": event["position_event_sha256"],
            "continuation_events": event["continuation_events"],
            "exit_package_ids": event["exit_package_ids"],
        },
    }

    if evaluation["status"] == "OPEN":
        return _attach_open_mark(
            view, directory, watchlist, watchlist_packages, facts, fact_packages,
            fill_policy, fill_packages, continuations[-1], evaluation,
        )
    if evaluation["status"] != "CLOSED":
        return view

    return _attach_closed_trade(
        view, directory, watchlist, watchlist_packages, facts, fact_packages,
        fill_policy, fill_packages, evaluation,
    )


def capital_settlement_view(
    directory: Path,
    watchlist: ShadowWatchlist,
    watchlist_packages: tuple[HistoricalEvidencePackage, ...],
    facts: ForwardFactBundle,
    fact_packages: tuple[HistoricalEvidencePackage, ...],
    fill_policy: ShadowFillPolicy,
    fill_packages: tuple[HistoricalEvidencePackage, ...],
    portfolio: ShadowPortfolioPolicy,
    exit_policy: ShadowExitPolicy,
    exit_packages: tuple[HistoricalEvidencePackage, ...],
) -> dict:
    """Display authenticated native cash settlement without inferring NAV."""
    settlement = audit_capital_settlement(
        directory, watchlist, watchlist_packages, facts, fact_packages,
        fill_policy, fill_packages, portfolio, exit_policy, exit_packages,
    )
    exit_view = exit_evaluation_view(
        directory, watchlist, watchlist_packages, facts, fact_packages,
        fill_policy, fill_packages, exit_policy, exit_packages,
    )
    if exit_view["position_status"] != "CLOSED":
        raise ValueError("capital settlement requires authenticated closed trade")
    return exit_view | {
        "collection_status": "CAPITAL SETTLED",
        "capital_settlement": {
            "status": "AUTHENTICATED NATIVE CASH FLOW / NO NAV OR PERFORMANCE",
            "market": settlement["market"],
            "currency": settlement["currency"],
            "capital_released": settlement["capital_released"],
            "risk_released": settlement["risk_released"],
            "exit_notional": settlement["exit_notional"],
            "exit_cost": settlement["exit_cost"],
            "net_exit_proceeds": settlement["net_exit_proceeds"],
            "recorded_at": settlement["recorded_at"],
        },
        "audit_references": exit_view["audit_references"] | {
            "reservation_id": settlement["reservation_id"],
            "settlement_id": settlement["settlement_id"],
            "portfolio_policy_id": settlement["policy_id"],
        },
    }


def continuation_capital_settlement_view(
    directory: Path,
    watchlist: ShadowWatchlist,
    watchlist_packages: tuple[HistoricalEvidencePackage, ...],
    facts: ForwardFactBundle,
    fact_packages: tuple[HistoricalEvidencePackage, ...],
    fill_policy: ShadowFillPolicy,
    fill_packages: tuple[HistoricalEvidencePackage, ...],
    portfolio: ShadowPortfolioPolicy,
    continuations: tuple,
    continuation_packages: tuple,
    exit_policy: ShadowExitPolicy,
    exit_packages: tuple[HistoricalEvidencePackage, ...],
) -> dict:
    """Display an authenticated continuation settlement without inferring NAV."""
    settlement = audit_continuation_capital_settlement(
        directory, watchlist, watchlist_packages, facts, fact_packages,
        fill_policy, fill_packages, portfolio, continuations,
        continuation_packages, exit_policy, exit_packages,
    )
    exit_view = continuation_exit_evaluation_view(
        directory, watchlist, watchlist_packages, facts, fact_packages,
        fill_policy, fill_packages, continuations, continuation_packages,
        exit_policy, exit_packages,
    )
    if exit_view["position_status"] != "CLOSED":
        raise ValueError(
            "continuation capital settlement requires authenticated closed trade"
        )

    return exit_view | {
        "collection_status": "CAPITAL SETTLED",
        "capital_settlement": {
            "status": "AUTHENTICATED NATIVE CASH FLOW / NO NAV OR PERFORMANCE",
            "market": settlement["market"],
            "currency": settlement["currency"],
            "capital_released": settlement["capital_released"],
            "risk_released": settlement["risk_released"],
            "exit_notional": settlement["exit_notional"],
            "exit_cost": settlement["exit_cost"],
            "net_exit_proceeds": settlement["net_exit_proceeds"],
            "recorded_at": settlement["recorded_at"],
        },
        "audit_references": exit_view["audit_references"] | {
            "reservation_id": settlement["reservation_id"],
            "settlement_id": settlement["settlement_id"],
            "portfolio_policy_id": settlement["policy_id"],
        },
    }
