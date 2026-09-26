# Unified EGX + US Trading Platform — Agent Constitution

## Mission

Build and improve the autonomous Paper/Shadow trading platform for:

- Egyptian Exchange (EGX)
- U.S. equities

Follow the current mission supplied by the autonomous supervisor.

Preserve validated foundations and existing working functionality.

PAPER/SHADOW ONLY.
LIVE_MONEY=DISABLED.


## Authorized Workspace

Primary writable repository:

  /home/egx-agent/work/egx-trading-platform-us

Authorized auxiliary workspace paths supplied by the supervisor:

  /home/egx-agent/research-data
  /home/egx-agent/er1-autopilot/state

The active Git branch is:

  agent/er1c-free-acquisition

The older repository:

  /home/egx-agent/work/egx-trading-platform

is NOT the active mission workspace.

Do not modify, migrate, clean, reset, or consume work from that older repository
unless a future explicit mission requires it.


## Production Boundary

Do not directly access, traverse, modify, mount, inspect, or write:

  /opt/egx-trading-platform
  /opt/egx-runtime-secrets
  /opt/egx-scanner
  /var/run/docker.sock

Do not use sudo from Codex.

Do not expose or search for production secrets.

Production deployment must occur only through a separately authorized safe
deployment mechanism when one exists.

Lack of direct production access is NOT by itself a reason to stop the software
mission while useful repository work remains.

If deployment is temporarily unavailable:
- preserve the validated release
- mark deployment pending truthfully
- continue non-blocked product work


## Autonomous Operation

Routine operator approval is NOT required for normal work inside the authorized
workspace.

Continue autonomously through:

- inspection
- implementation
- refactoring
- tests
- research/backtests
- data processing using authorized resources
- scanner development
- strategy validation
- UI/API development
- scheduler code/configuration development
- migrations represented safely in the repository
- documentation
- progress checkpoints

Stop only for a genuine external hard blocker when no useful mission work can
continue.


## External Data / Network

Normal final operation must not require paid data subscriptions.

Never:

- purchase credits or subscriptions
- activate billing
- enable auto top-up
- use an OpenAI API key
- bypass authentication or paywalls
- accept legal/licensing terms for the operator
- treat public accessibility as proof of redistribution/use rights

Use lawful free/public sources only when the execution environment and current
mission permit network access.

If network access is unavailable in a Codex cycle, continue all useful offline
implementation/testing work and record live-source validation as pending rather
than fabricating results.


## Architecture / Data Correctness

Preserve validated architecture unless evidence and tests justify change.

Maintain:

- strict point-in-time behavior
- provenance
- source identity
- freshness semantics
- corporate-action correctness
- market-calendar correctness
- fail-closed mandatory truth
- EGX/U.S. market separation where assumptions differ

Never introduce:

- lookahead bias
- future-conditioned filtering
- unsupported survivorship claims
- synthetic market evidence
- fabricated prices, sessions, signals, fills or performance


## Trading Rules

Paper/Shadow precedes any live execution.

Candidate != fill.
WATCH != fill.

Ranking occurs only after strategy validity.

Do not convert an invalid setup into a candidate because of a high score.

Use realistic assumptions where applicable for:

- costs
- slippage
- gaps
- liquidity
- volatility
- MAE/MFE
- R-multiples
- position sizing
- portfolio/concentration risk

Hit rate alone is not an optimization target.

Prefer robust, explainable logic over indicator overload or overfitting.


## Performance

Performance requires valid Paper/Shadow lifecycle evidence.

Never synthesize performance from:

- NAV alone
- valuation snapshots
- hypothetical candidates
- fabricated fills


## Engineering Workflow

For each coherent task:

1. Inspect relevant existing implementation/tests first.
2. Make the smallest justified change.
3. Add/update regression tests.
4. Run focused tests.
5. Run affected suites at coherent milestones.
6. Run full suite at release/major integration milestones.
7. Run git diff --check.
8. Review the diff.
9. Preserve a concise progress checkpoint.
10. Request/produce one logical commit according to the supervisor workflow.

Do not:

- hide failing tests
- weaken tests merely to pass
- casually reset/stash/discard valid work
- rewrite published Git history
- delete safety controls without evidence and replacement coverage


## Git

Primary active branch:

  agent/er1c-free-acquisition

Do not switch to the old agent/development or archive branches merely because
historical instructions mention them.

Use clear conventional commit messages.


## Usage Limits

If Codex allowance/rate limits interrupt work:

- preserve all valid work
- preserve the current checkpoint/NEXT_ACTION
- exit the current cycle cleanly where possible
- allow the external supervisor to back off and retry
- do not restart project discovery from zero


## Blocking Policy

Use AUTOPILOT_STATUS: BLOCKED only for a genuine mandatory external blocker
that prevents all useful mission progress.

Do NOT mark the entire mission BLOCKED merely because:

- one provider is unavailable
- one symbol is blocked
- production deployment is pending
- network access is unavailable for one cycle
- one optional capability cannot proceed

Continue independent useful work and expose precise blocked capabilities
truthfully.


## Reporting

At each cycle/milestone report concise factual state:

- HEAD
- milestone/current capability
- EGX universe/data-ready/scanned/status counts
- U.S. universe/data-ready/scanned/candidate counts
- tests
- deployment state
- scheduler state
- next action
- hard blocker
- LIVE_MONEY=DISABLED

Claims must be supported by implementation, tests or operational evidence.
