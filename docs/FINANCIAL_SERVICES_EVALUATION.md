# anthropics/financial-services evaluation

Status: **METHODOLOGY_REFERENCE_ONLY** (research layer; execution authority NONE).

## Pinned upstream

- Repository: https://github.com/anthropics/financial-services
- Commit: `574ed3624aebd0418c7e96cd101262f30210ab26` (committed 2026-09-21T22:10:41+01:00)
- License: Apache-2.0
- Evaluated: 2026-09-28, read-only shallow clone at
  `/home/egx-agent/research-data/vendor/financial-services` (auxiliary research
  workspace, not part of this repository and not installed as a plugin).

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
- No sourced note store is connected yet; RESEARCH reports notes as UNKNOWN
  rather than generating them.

## To enable a connector later (manual)

An operator would have to hold a lawful entitlement, provide credentials
outside this repository, and the upstream config would have to be valid JSON
at a newly pinned commit. Connector output would still enter only as
SOURCE_FACT provenance in the research layer.
