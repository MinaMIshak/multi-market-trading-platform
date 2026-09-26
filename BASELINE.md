VALIDATED BASELINE
==================

This is the foundation to preserve and extend, not redesign from scratch.

STARTING SOFTWARE BASELINE
- Repository: /home/egx-agent/work/egx-trading-platform-us
- Branch: agent/er1c-free-acquisition
- Starting validated revision: e55fbbc
- Existing test baseline was green.
- Current production API has already proven the new operational Paper/Shadow UI.
- Preserve existing working US functionality.

OPERATIONAL FOUNDATION ALREADY BUILT
- Paper/Shadow operational architecture exists.
- PIT/provenance/evidence controls exist.
- reviewed-source binding exists.
- corporate-action handling exists.
- operational history-window scoping exists.
- generic operational API exists.
- Candidate != fill.
- No execution/performance inference from signals.
- LIVE_MONEY is disabled.

FIRST VALIDATED EGX CASE
- COMI is the first end-to-end validated reference case only.
- COMI is NOT the final product scope.
- Real current result proved READY_NO_SIGNAL.
- Operational source: tradingview_tvdatafeed_egx.
- Operational window: 260 bars.
- Do not hardcode COMI.

EGX UNIVERSE
- Existing platform baseline universe: 224 EGX symbols.
- Treat 224 as the current target universe unless authoritative refreshed
  universe evidence proves that the listing count has changed.
- Do not silently reduce the EGX scope.

DO NOT REPEAT COMPLETED INVESTIGATIONS
Unless a new hard blocker proves they must be reopened:
- provider-source investigations already resolved
- COMI acquisition investigation
- completed calendar evidence work
- completed corporate-action evidence work
- historical licensing/provider comparisons
- old Experimental UI work

CORE PRINCIPLE
New functionality must build on the validated controls above.
Do not bypass PIT, provenance, evidence, freshness, corporate actions or
operational truth simply to increase scanner coverage.
