"""Shared model / feature / experiment registry (one infrastructure, separate market models).

Every entry is versioned and immutable. A change means a new version. EGX and US share the
code (labels, walk-forward, metrics) but never datasets, fitted parameters, weights or labels.
"""
from app.learning import dataset, fusion, models
from app.strategies.egx_experiment_config import ACTIVE_CONFIG as TRADE_CONFIG, STRATEGY as TRADE_STRATEGY
from app.strategies.egx_ranking import US_PROFILE, VERSION as EGX_RANK

REGISTRY = {
    "shared": {"data_version": dataset.VERSION, "labels": "1/2/3/5-session returns, MFE/MAE, +3..+20% thresholds",
               "walk_forward": "calendar-month folds, expanding window, labels matured before each fold",
               "metrics": "rank IC, Precision@K, top-gainer recall, Top-K realised returns, Brier/calibration"},
    "EGX": {"technical_champion": EGX_RANK, "trade_experiment": f"{TRADE_STRATEGY} ({TRADE_CONFIG}): V1, V2A-V2D",
            "feature_schema": dataset.FEATURE_SCHEMA, "forecast_champion": models.CHAMPION,
            "forecast_challengers": [m for m in models.MODELS if m != models.CHAMPION],
            "decision_fusion": f"{fusion.STRATEGY} ({fusion.CONFIG}, effective {fusion.EFFECTIVE_DATE})",
            "fusion_arms": list(fusion.ARMS), "fusion_champion": "DF0 (technical)"},
    "US": {"technical_champion": US_PROFILE.version, "trade_experiment": "none (US hard gates in Decision Fusion)",
           "feature_schema": dataset.FEATURE_SCHEMA + " + gap/intraday extras",
           "forecast_champion": models.CHAMPION,
           "forecast_challengers": [m for m in models.MODELS if m != models.CHAMPION],
           "decision_fusion": f"{fusion.US_STRATEGY} ({fusion.US_CONFIG}, effective {fusion.EFFECTIVE_DATE})",
           "fusion_arms": list(fusion.US_ARMS), "fusion_champion": "US-DF0 (technical)",
           "options_context": "UNAVAILABLE", "sec_fundamentals": "BLOCKED: NO_OPERATOR_CONTACT_EMAIL"},
    "isolation": "Each market's datasets, fitted models, weights and labels are built only from that market's "
                 "series; cross-market inputs are limited to timestamped context in the research report.",
}
