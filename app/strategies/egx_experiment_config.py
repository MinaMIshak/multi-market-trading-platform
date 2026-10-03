"""Versioned EGX champion-vs-challenger research configurations (never edited in place).

A new threshold means a new config version with its own effective date and
rationale. Decisions record the config version they were made under, so past
Paper/Shadow results stay reproducible. Thresholds were fixed on 2026-10-03,
before any challenger outcome existed. No threshold is fitted to outcomes.
"""
from decimal import Decimal

STRATEGY = "EGX-EXP-v1"
ARMS = ("V1", "V2A", "V2B", "V2C", "V2D")
ARM_DEFINITIONS = {
    "V1": "Champion EGX-RANK-v1, unchanged (STRONG_CANDIDATE / CANDIDATE, V1 lifecycle rules)",
    "V2A": "V1 + position-aware liquidity / tradeability gate",
    "V2B": "V2A + resistance-aware room-to-resistance gate",
    "V2C": "V2B + entry-zone validity, gap/chase protection, setup expiry",
    "V2D": "V2C + risk-based Paper position sizing with a portfolio open-risk cap",
}

CONFIGS = {
    "EGX-EXP-CFG-1": {
        "effective_date": "2026-10-03",
        "model_capital_egp": Decimal("1000000"),
        "risk_pct_per_trade": Decimal("0.5"),
        "max_position_pct_of_capital": Decimal("20"),
        "max_open_risk_pct": Decimal("3.0"),
        "min_avg_turnover_egp": Decimal("1000000"),
        "max_position_pct_of_avg_turnover": Decimal("5"),
        "resistance_lookback_sessions": 250,
        "pivot_half_width": 3,
        "min_room_to_resistance_r": Decimal("1.8"),
        "max_chase_pct_above_zone": Decimal("1.0"),
        "entry_window_sessions": 1,
        "rationale": {
            "min_avg_turnover_egp": "EGX-RANK-v1 ILLIQUID floor (1M EGP/day), kept identical so V2A adds only "
                                    "the position-aware rule.",
            "max_position_pct_of_avg_turnover": "Planned Paper position at most 5% of the 20-session average "
                                                "turnover: a conservative participation research default.",
            "model_capital_egp": "Round model capital for Paper/Shadow research; not an account.",
            "risk_pct_per_trade": "Conservative starting point (0.5% of model capital at risk per trade). "
                                  "Alternatives are evaluated as new config versions.",
            "max_position_pct_of_capital": "Concentration cap per position.",
            "max_open_risk_pct": "Portfolio cap: at most six simultaneous 0.5% risks (V2D only).",
            "min_room_to_resistance_r": "1.8R initial research threshold (above the 1.5R T1 of EGX-RANK-v1); no "
                                        "validated evidence supported another value.",
            "resistance_lookback_sessions": "About one EGX trading year of confirmed pivot highs.",
            "pivot_half_width": "A pivot high must be the maximum of the 3 sessions on each side, confirmed by "
                                "bars on or before the decision session.",
            "max_chase_pct_above_zone": "Twice the EGX-RANK-v1 entry half-band (0.5%); a larger gap above the zone "
                                        "blocks the setup instead of chasing.",
            "entry_window_sessions": "Same as EGX-RANK-v1 (next eligible session only); then SETUP_EXPIRED.",
        },
    },
}
ACTIVE_CONFIG = "EGX-EXP-CFG-1"
