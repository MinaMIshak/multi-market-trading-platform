# EGX Trading Platform - Agent Constitution

## Mission
Build the EGX trading platform according to EXECUTION_PLAN.md with production-grade engineering discipline.

## Workspace Boundary
Work ONLY inside:
  /home/egx-agent/work/egx-trading-platform

NEVER access, read, modify, traverse, copy, mount, or inspect:
  /opt/egx-trading-platform
  /opt/egx-runtime-secrets
  /opt/egx-scanner
  /var/run/docker.sock

Use only the rootless Docker daemon belonging to egx-agent.

## Production Safety
NEVER:
- use sudo
- restart or modify production services
- modify production docker-compose configuration
- write to or copy the live platform.db
- deploy to production
- modify host firewall, SSH, systemd system services, or OS configuration
- expose, print, search for, or copy secrets
- manually force a trading day to VERIFIED
- enable paper_refresh without its required safety gates
- implement or enable live-money broker execution

Production changes require explicit human approval outside this workspace.

## External Network / Paid APIs
Do not call EODHD or any paid/limited external market-data API unless the current mission explicitly authorizes it.

When tests can run offline, prefer no-network execution.

Never purchase credits, enable auto top-up, configure API billing, or use an OpenAI API key.

If ChatGPT/Codex allowance is exhausted, STOP.

## Architecture Rules
Preserve the existing architecture unless the execution plan explicitly requires a change.

Do not silently redesign components to make implementation easier.

Calendar truth must remain fail-closed.

Post-close evidence must never be used as pre-market truth.

Do not introduce look-ahead bias, future-conditioned filtering, survivorship leakage, or synthetic trading evidence.

Do not fabricate recommendations, prices, fills, market sessions, or validation results.

## Trading Safety
Paper trading comes before live trading.

Realistic simulation must include, where applicable:
- transaction costs
- slippage
- gaps
- fill assumptions
- MAE/MFE
- R-multiples
- position sizing
- portfolio risk constraints

Hit rate alone is not an optimization target.

Primary validation metrics include net expectancy, profit factor, drawdown, average win/loss, consistency, and out-of-sample behavior.

## Engineering Workflow
For each logical task:

1. Inspect existing code and tests first.
2. Make the smallest coherent change.
3. Add or update tests.
4. Run focused tests.
5. Run broader regression when appropriate.
6. Run `git diff --check`.
7. Review `git diff`.
8. Commit one logical change at a time.

Do not hide failing tests.
Do not weaken tests merely to make them pass.
Do not delete safety checks without explicit authorization.

## Git
Primary working branch:
  agent/development

Do not commit directly to production/master workflow.

Use clear conventional commit messages.

Do not rewrite existing published history.

## STOP Gates
STOP and report before any action involving:
- production deployment
- production DB migration or write
- production Docker/service changes
- production secrets
- paid market-data consumption
- broker integration or live-money execution
- security boundary changes
- architecture changes conflicting with EXECUTION_PLAN.md

## Reporting
After every milestone report:

- current HEAD
- files changed
- tests run and exact results
- safety checks
- external API calls made
- unresolved risks/blockers
- recommended next milestone

Claims must be supported by commands/tests, not assumptions.
