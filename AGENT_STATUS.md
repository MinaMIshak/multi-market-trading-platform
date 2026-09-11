# M5 milestone status

Result: **M5 PASS**

Engineering complete for research/paper admission only. Policy values remain
unvalidated. STOPPED BEFORE M6.

## Scope and HEAD

- Starting HEAD: `78e648b6f6aef7b2020721f42fbd9ddfc017fd49` (required `78e648b`).
- Ending HEAD: `78e648b6f6aef7b2020721f42fbd9ddfc017fd49`.
- Branch: `agent/development`; hardening started with the existing uncommitted M5 candidate.
- AGENTS.md and EXECUTION_PLAN.md unchanged; M5/M6 scope checked.
- No git add or commit; index unchanged. M5 only.

## Files and architecture

Modified:
- `app/risk/policy.py`: required explicit immutable strict policy, cross-field
  validation, version and deterministic canonical SHA-256 configuration identity.
- `app/risk/models.py`: required strict immutable portfolio snapshot, cash
  reservations, pending commitments, open risk, symbol/group exposures and validation.
- `app/risk/engine.py`: explicit inputs, TradePlan boundary, time/lifecycle gates,
  preserved loss/RR/regime/short controls, independent whole-share quantity caps,
  deterministic cap reasons and zero-capacity blocking.
- `app/domain/models.py`: required RiskDecision policy version/identity and cap audit.
- `tests/test_risk_engine.py`: original nine behavior tests retained with explicit
  fixture policy/context and fixed decision timestamps.
- `tests/test_domain.py`, `tests/test_storage.py`: explicit audit metadata in existing
  decision fixtures; prior assertions retained.
- `AGENT_STATUS.md`: this milestone report.

New:
- `tests/test_m5_risk_portfolio.py`: 128 acceptance cases; 17 additional domain boundary cases in tests/test_domain.py.
- `docs/M5_RISK_PORTFOLIO.md`: API semantics, formulas, audit, upstream obligations,
  compatibility limits and unvalidated assumptions.

Architecture is preserved: pure risk admission remains in app/risk with existing
TradePlan/RiskDecision domain contracts. No Candidate conversion, operational wiring,
Position/TradeOutcome creation or lifecycle transition in the risk engine.

## Schema impact

None. No SQL/schema/storage changes or migration. The existing payload_json field
can serialize new decision metadata. Existing storage tests use isolated test data.
Older decision payloads lacking required policy metadata are not silently assigned
fabricated policy provenance and are not migrated by M5.

## Exact validation results

Focused final command:
`.venv/bin/python -m pytest -q tests/test_risk_engine.py tests/test_m5_risk_portfolio.py tests/test_domain.py tests/test_storage.py`

Final result: **160 passed in 0.57s**.

Full final command: `.venv/bin/python -m pytest -q`

Final result: **667 passed in 15.76s** (643 candidate tests plus 24 boundary regressions).

Earlier candidate verification runs (recorded before this hardening):
- Existing risk/domain/storage: **15 passed in 0.30s**.
- Initial expanded focused run: **134 passed, 1 warning in 0.50s**. The warning
  was the deliberate invalid-model-copy serializer case; it is now explicitly
  asserted with pytest.warns. No warning or failure is hidden.
- Expanded focused run: **136 passed in 0.49s**.
- First full run: **643 passed in 15.32s**.
- Final runs followed retention of the defensive risk-per-share guard and policy
  identity canonicalization cleanup.
- No test failures. An initial shell edit command used unavailable `python` and
  exited 127; it was rerun with `.venv/bin/python` successfully before testing.

Final boundary hardening changed only `app/domain/models.py`, `app/risk/policy.py`,
`tests/test_domain.py`, `tests/test_m5_risk_portfolio.py` and this report relative to
the starting uncommitted candidate. APPROVE/REDUCE now require positive quantity
and approved risk; BLOCK zero constraints and the risk budget bound are retained.
Quantity caps require nonblank string keys and strict nonnegative integer values.
Policy versions strip surrounding whitespace, reject blank versions and use the
canonical version in deterministic identity. Sizing calculations are unchanged.
All 24 new regressions passed, including canonical identity and serialization replay.

Coverage includes every required policy argument, strict/range/cross-field rejects,
required context/time/state, aware time and exact validity boundaries, all lifecycle
states, open plus pending concurrency, reserved/insufficient cash, portfolio open
risk/symbol/group reduce and block, missing group context, simultaneous independent
caps, original loss/RR/regime/liquidity behavior, shorts, zero capacity, budget bounds,
Candidate/dict rejection, model-copy revalidation, immutability and deterministic
replay. BLOCK results have zero quantity, risk and position value.

`git diff --check`: passed after the final full regression. New files additionally
checked with `git diff --no-index --check` against /dev/null (exit 1 denotes new-file
differences; no whitespace diagnostics). Tracked implementation/test diffs and new
file contents reviewed. Protected-file diff for AGENTS.md, EXECUTION_PLAN.md,
storage, data, strategies and tests/conftest.py is empty. Index remains unchanged.

## Safety accounting

- Network/external API/EODHD/paid market-data calls: **0**.
- Production/forbidden path/secret access, Docker, sudo, deploy, broker actions: **0**.
- No API key, billing or credit usage configured.
- No schema migration, production DB write, live execution, simulator or fill
  implementation; no operational paper_refresh invocation or enablement.
- Existing full-regression fixtures exercise mocked scheduler/refresh paths and
  temporary database behavior; this does not enable operational jobs.
- Existing autouse test guard blocks socket.connect, connect_ex and
  create_connection. No network-guard failures occurred. M5 imports only local
  domain/contracts, Pydantic and standard-library calculation/identity utilities.
- No new reservation write, Position, TradeOutcome, fill or state transition from
  risk admission. Tests assert unchanged input plan/state and forbid execution
  artifact construction during admission.
- No git staging, commit, production access or M6 work.

## Unvalidated assumptions and remaining risks

- All operational thresholds/scales remain caller-supplied unvalidated research/paper
  choices. Tests use synthetic arithmetic fixtures, not trading evidence.
- Caller must supply accurate contemporaneous equity, cash, daily R, exposure,
  open risk and pending commitments for the correct account/symbol/group/day.
  Context has no independent freshness or completeness authority.
- Exposure/open risk must include outstanding commitments; caller must serialize
  admission and update commitments. Evaluation does not atomically reserve funds.
- Correlation groups are trusted upstream single-group context, not statistical
  correlation estimates or overlapping-group portfolio analysis.
- Entry reference is planning-only. Short allowance does not implement margin,
  borrow or sale proceeds. Fees, slippage, fills and executable pricing are deferred.
- Old decision payload provenance needs an explicit future compatibility decision
  if historical objects are rehydrated. No silent migration occurs.
- No engineering blocker remains within authorized M5 scope. No claim of validated
  profitability or live readiness is made.

Recommended next milestone: review M5, then separately authorize M6 Paper Execution
Simulator. M6 has not been started.
