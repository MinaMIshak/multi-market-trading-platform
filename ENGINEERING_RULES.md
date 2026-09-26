ENGINEERING / TRADING RULES
===========================

These rules apply throughout the mission.

TRUTH
- Unknown mandatory truth must fail closed.
- Never fabricate evidence, market data, signals, fills, execution or performance.
- Candidate != fill.
- Public/anonymous access does not automatically imply licensing rights.

DATA
- Preserve provenance.
- Preserve PIT semantics.
- Preserve freshness semantics.
- Validate source identity and data contracts.
- Keep EGX and U.S. market assumptions separate.
- Corporate actions and calendars are part of market-data correctness.

STRATEGIES
- Audit existing strategies before adding new ones.
- Every feature must have an explicit purpose.
- Avoid indicator overload and redundant features.
- Strategy eligibility precedes ranking.
- Research performance does not automatically authorize operational use.
- Prefer robust/simple logic over overfit complexity.

VALIDATION
- No lookahead.
- No unsupported survivorship claims.
- Use out-of-sample/walk-forward testing where feasible.
- Model transaction costs/slippage where relevant.
- Treat small samples honestly.

RISK
- Stops must represent real invalidation logic.
- Position sizing must respect risk and liquidity.
- Consider volatility, concentration, correlation and market exposure.
- No live-money execution.

PERFORMANCE
- Performance requires valid Paper/Shadow lifecycle evidence.
- Never derive performance from NAV alone.
- Never treat hypothetical candidates as executed trades.

ENGINEERING
- Reuse validated architecture.
- Prefer small coherent changes.
- Do not reset/stash/discard valid work casually.
- Preserve backward compatibility unless a deliberate tested migration is needed.
- Add regression tests for changed behavior.

TESTS
- Focused tests during implementation.
- Affected suites at coherent milestones.
- Full suite at release/major integration milestones.

DEPLOYMENT
- Preserve rollback.
- Modify/restart only required services.
- Validate health after deployment.
- Roll back failed releases when practical.
- Keep LIVE_MONEY=DISABLED.

SECURITY
- Never expose secrets.
- Do not weaken authentication/security merely to simplify development.
- Do not purchase services or accept legal agreements for the operator.

FREE DATA
- Required normal operation must remain zero-paid-subscription.
- Evaluate coverage, reliability, history, freshness, rate limits and terms.
- Use lawful fallback chains.
- Failure must become stale/blocked, never fabricated data.
