# anthropics/financial-services evaluation

Status: **METHODOLOGY_REFERENCE_ONLY** (research layer; execution authority NONE).

## Pinned upstream

- Repository: https://github.com/anthropics/financial-services
- Commit: `574ed3624aebd0418c7e96cd101262f30210ab26` (committed 2026-09-21T22:10:41+01:00)
- License: Apache-2.0
- Evaluated: 2026-09-28, read-only shallow clone at
  `/home/egx-agent/research-data/vendor/financial-services` (auxiliary research
  workspace, not part of this repository and not installed as a plugin).

## Re-check 2026-09-29

A fresh shallow clone of upstream `main` resolved to the same commit
`574ed3624aebd0418c7e96cd101262f30210ab26`. The recorded findings were
re-verified against the files:
- plugin versions financial-analysis 0.1.1, equity-research 0.1.2,
  market-researcher 0.1.1;
- `financial-analysis/.mcp.json` still fails to parse (JSON error at line 47);
- the same 12 credentialed connectors.

The upstream tree also contains agent plugins (earnings-reviewer,
model-builder, valuation-reviewer, pitch-agent, kyc-screener, gl-reconciler,
month-end-closer, statement-auditor, meeting-prep-agent) and vertical plugins
(investment-banking, private-equity, fund-admin, operations). Beyond the
research methodology already adapted, none supplies EGX/U.S. market data,
admission evidence or execution. Their connectors need the same unavailable
commercial credentials.

The official plugin mechanism (`claude plugin`, Claude Code 2.1.285) is
available in the agent session. Nothing was installed: it would change only
the ephemeral agent environment, not the product, and every connector would
fail closed without credentials. The next evaluation is needed only when
upstream HEAD changes; review that diff before adopting anything.

## Findings

| Plugin | Version | MCP config | Hooks |
| --- | --- | --- | --- |
| financial-analysis | 0.1.1 | **invalid JSON upstream** (missing comma before `box`, unclosed object) | empty |
| equity-research | 0.1.2 | none | empty |
| market-researcher (agent plugin) | 0.1.1 | none | none |

- Every upstream MCP connector (Daloopa, Morningstar, S&P Global/Kensho,
  FactSet, Moody's, MT Newswires, Aiera, LSEG, PitchBook, Chronograph, Egnyte,
  Box) is a credentialed commercial service. No credentials or entitlements
  exist for this platform and none were sought, so all are **FAIL_CLOSED**.
- None of the connectors establishes EGX coverage; EGX coverage is UNKNOWN.
- The skills are prompt methodology (comps, sector overview, earnings,
  thesis tracking). They are not data sources.

## Decision

- No plugin installation and no user/global configuration change.
- No connector enabled. The recorded status is exposed read-only by
  `app/financial_services_status.py`, `/api/product?section=RESEARCH` and the
  RESEARCH view.
- Research output must use the platform-owned contract
  `app/research/intelligence.py` (`ResearchNote`): each statement is exactly one
  of SOURCE_FACT (provenance: source id, locator, aware observed_at, as_of),
  DERIVED_METRIC (method + earlier fact/metric inputs), MODEL_INTERPRETATION
  (model + earlier fact/metric inputs) or UNKNOWN (reason). Facts observed after
  note generation are rejected as lookahead. The contract has no order, side,
  quantity, fill or execution fields, and its projection always reports
  `execution_authority: NONE`.
- RESEARCH notes are built deterministically by `app/research/receipt_notes.py`
  from hash-verified EGX operational receipts only (no language model, no
  trade levels): SOURCE_FACTs locate the PIT audit event and raw daily-data
  hash, expiry is a DERIVED_METRIC, and fundamentals/disclosures are UNKNOWN.
  Unverifiable receipts yield UNKNOWN-only notes; with no receipt reader (and
  for US) notes are UNKNOWN rather than generated.

## To enable a connector later (manual)

An operator would have to hold a lawful entitlement, provide credentials
outside this repository, and the upstream config would have to be valid JSON
at a newly pinned commit. Connector output would still enter only as
SOURCE_FACT provenance in the research layer.
