# Phase 5 — D0/D1 decision and review packet

## 1. Starting truth and authority

Starting HEAD: `6595966d0747c8f96912c203cc3b50b0bdb09760`.
Branch: `agent/er1c-free-acquisition`. Repository:
`/home/egx-agent/work/egx-trading-platform-us`. Assessment: 2026-09-25.
The current mission supersedes the older AGENTS workspace/branch defaults.
Phases 1–4 remain closed: REACHED / LOCAL_SCOPE_REACHED / PUBLIC_SCOPE_REACHED /
DECISION_PACK_READY respectively. EMPIRICAL_READY: NO. LIVE_READY: NO.
Authentic admitted canonical PIT, execution, OOS, holdout and forward samples
remain zero. The 297 retained daily rows remain unadmitted and the TWTR
2022-10-28 row remains quarantined. This document creates no evidence or approval.

Controlling references: [Phase 4](EVIDENCE_AUTHORIZATION_PROCUREMENT_PLAN.md),
[acquisition specification](ER1_DATA_ACQUISITION_SPEC.md),
[original U22 freeze](ER1C_US_PILOT_PREDECLARATION.md),
[Phase 2](EVIDENCE_VALIDATION_READINESS.md), [Phase 3](EXTERNAL_EVIDENCE_EMPIRICAL_READINESS.md),
[retained inventory](evidence-validation/retained-inventory.json),
[retained manifest](external-evidence/acquisition-manifest.json) and
[admission audit](external-evidence/admission-audit.json).
References to external sources are identifiers only; no network, source discovery,
acquisition, messages, accounts, purchases or contract acceptance are authorized.

Every blank `___` means UNANSWERED. Every unchecked box means NOT ACCEPTED.
Copies completed by humans must retain packet version/hash, actor, authority,
UTC timestamp, signature/attestation and supporting attachment references.
Do not populate canonical `approved=True` from an unsigned form or agent judgment.

## 2. D0 fixed versus unresolved matrix

ALREADY_FROZEN means an existing scope commitment; DERIVED_FROM_CONTRACT means
an enforced rule, not an empirical parameter choice. HUMAN_DECISION_REQUIRED
means a human must answer the matching Q row. DEPENDENT_ON_UPSTREAM_SCOPE means
an exact result cannot be computed until those decisions and genuine calendar/
identity facts exist. NOT_APPLICABLE is a bounded excluded use, never a waiver.

| Field | Classification | Existing value / unresolved boundary | Authority / question |
| --- | --- | --- | --- |
| U22 market/type/cohort | ALREADY_FROZEN | XNYS common stock; IBM continuing control, TWTR later-removed structural case, chosen without return/signal/performance selection; IBM CIK 0000051143, TWTR CIK 0001418091 are source identifiers, not substitutes for stable platform IDs | Original U22 freeze |
| U22 raw/warm-up start | ALREADY_FROZEN | 2022-04-01 | Original U22 freeze |
| U22 observation start | ALREADY_FROZEN | 2022-07-01; Phase-4 2022-04-01–2022-11-04 envelope does not move this observation start | Original U22 freeze |
| U22 removal endpoints | ALREADY_FROZEN | Last expected TWTR trade 2022-10-27; suspension before 2022-10-28 open; post-removal evidence through 2022-11-04; no invented post-suspension bars | Original U22 freeze; actual facts still require admission |
| U22 price basis / use | ALREADY_FROZEN | Raw unadjusted IBM/TWTR daily OHLCV; adjusted values audit-only; acquisition/admission and bias pilot, not strategy validation; no forward fill or inferred missing sessions/removals | Original U22 freeze |
| Complete U22 universe | ALREADY_FROZEN | Every then-member of declared XNYS common-stock universe on every open/early-close date, including ineligible/removed members; IBM/TWTR are the pilot derivation cohort, not the whole roster | Phase 4 / acquisition specification |
| Strategy/configuration selection | HUMAN_DECISION_REQUIRED | No empirical strategy/configuration freeze follows from the acquisition pilot | Q01 |
| Numerical strategy warm-up requirements | HUMAN_DECISION_REQUIRED | Existing April–June raw interval is fixed; sufficiency and any additional per-strategy windows are not | Q02 |
| Exact extended acquisition start/end | DEPENDENT_ON_UPSTREAM_SCOPE | Derive from Q01–03 plus authenticated sessions, preserving original U22 interval; do not assume a number of weekdays equals sessions | Q02–03 |
| Label/outcome horizons | HUMAN_DECISION_REQUIRED | Targets, horizon units, terminal/exit definitions and maturity cutoffs absent from U22 acquisition freeze | Q03 |
| EGX pilot dates | HUMAN_DECISION_REQUIRED | Contiguous recent interval; exact dates absent | Q04 |
| EGX cohort selection/names | HUMAN_DECISION_REQUIRED | Persistent eligible member plus later-removed member if one existed; complete universe/index required; select using dated eligibility, never returns | Q05 |
| EGX structural requirements | DERIVED_FROM_CONTRACT | If no removed member exists, retain proof and expand scope before claiming survivorship branch tested; evidenced symbol-change/reuse case or mark branch untested | Acquisition specification |
| Z08/Z20 procurement envelopes | ALREADY_FROZEN | US 2007-01-01–2009-12-31 and 2019-01-01–2021-12-31 inclusive; Phase-4 whole-year procurement requirements, not hardcoded engine endpoints | Phase 4 |
| Z08 cohort | HUMAN_DECISION_REQUIRED | Then-eligible era cohort and complete dated membership; no IBM/TWTR back-projection | Q06 |
| Z20 cohort | HUMAN_DECISION_REQUIRED | Then-eligible era cohort and complete dated membership | Q07 |
| Stress warm-up/label extensions and EGX era feasibility | DEPENDENT_ON_UPSTREAM_SCOPE | Extend whole-year bounds for chosen protocol; EGX 2008 not testable absent authentic reach; EGX 2020 requires matching 2019–2021 cohort/archive | Q01–03, Q05–07 |
| Matched ETF/comparator identities/rule | HUMAN_DECISION_REQUIRED | No ETF names fixed; require matched stock/ETF intervals, horizons, currencies and costs | Q08 |
| OOS folds | HUMAN_DECISION_REQUIRED | No dated empirical fold plan frozen | Q09 |
| OOS interval/purge semantics | DERIVED_FROM_CONTRACT | UTC half-open positive intervals; training end <= test start; ordered nonoverlapping tests; rolling training bounds; label availability strictly < test start; equality purged; incomplete labels unavailable | M8 models/splits, US8 |
| Additional embargo | HUMAN_DECISION_REQUIRED | No default additional embargo or fixed-day purge horizon in M8A; decide explicit train/test gaps, including explicitly chosen zero if justified | Q10 |
| Untouched holdout dates | HUMAN_DECISION_REQUIRED | Every development test end strictly < holdout start; exact start/end absent | Q11 |
| Holdout custodian/non-inspection | HUMAN_DECISION_REQUIRED | Named independent custodian and custody/release procedure absent | Q12 |
| Forward interval/cutoffs/sample goal | HUMAN_DECISION_REQUIRED | No universal session count or HH:MM; missed 2026-09-13 EGX / 2026-09-14 US cannot be recovered | Q13 |
| Forward ordering | DERIVED_FROM_CONTRACT | information_cutoff <= generated_at <= freeze_receipt <= completed_at < decision_cutoff <= session open; exact authenticated sessions and review available at freeze | Phase 4 §8; shadow collection |
| Completed-trade minima | HUMAN_DECISION_REQUIRED | Both development and holdout must be declared; engine floor each >=2; no universal sufficient sample target | Q14 |
| Economic thresholds | HUMAN_DECISION_REQUIRED | Required explicit thresholds, no numeric empirical defaults | Q15 |
| Scenarios/regimes | HUMAN_DECISION_REQUIRED | Exact scenario definitions/IDs and regime provenance not frozen | Q16 |
| Frozen research criteria/configuration | HUMAN_DECISION_REQUIRED | Protocol identity, bootstrap, equity and evaluation cutoffs absent | Q17 |
| No outcome inspection | DERIVED_FROM_CONTRACT | Scope/criteria/predictions frozen before outcomes; no holdout tuning; selection cannot use future returns or surviving availability | Acquisition specification / M8B |
| Live execution, raw public redistribution | NOT_APPLICABLE | Neither required nor authorized by D0/D1; aggregate publication requires later explicit rights decision | Mission / Phase 4 P0 |

Code anchors: `app/research/models.py` (`WalkForwardPlan`, `FrozenHoldout`),
`app/research/splits.py`, `app/research/evidence.py` (`ResearchEvidenceCriteria`,
`BootstrapConfig`, `FrozenResearchProtocol`), `app/performance/models.py`
(`PerformanceConfig`), `app/us/research_validation_models.py`
(`USReplayFrozenResearchProtocol`), `app/strategies/eod.py`,
`app/strategies/intraday.py`; [M8 rules](M8_RESEARCH_VALIDATION.md),
[US8 rules](US8_REPLAY_VALIDATION.md). Example/test-fixture settings are not freezes.

## 3. Exact human D0 questions and response sheet

For **each Q01–Q17**, fill:
`answer ___; rationale/source ___; owner ___; decision UTC ___; signature ___;
protocol version/hash ___; custodian receipt/hash ___; outcomes already seen ___`.
Disclose prior inspections; a new signature cannot retroactively make seen data
untouched. Preserve all prior versions and rejected alternatives without selecting
based on their outcomes. No answer is inferred by this packet.

| ID / exact question | Allowed/valid shape | Consequence and downstream gates | Effect of later change on OOS/holdout |
| --- | --- | --- | --- |
| Q01 Which strategy IDs, versions, configurations, selection/exclusion rules, risk/sizing and fill policies are frozen for each market/scope? | Named versions and complete typed configs with hashes; no automatic US-to-M4 mapping; rationale independent of outcomes | Determines inputs, warm-up and canonical execution; G-P/X/C/M/F/O/H/V/Z/E | Material change requires new protocol; after holdout inspection, new untouched evidence/research cycle |
| Q02 What exact lookbacks and additional warm-up sessions does each selected strategy require? | Per strategy daily/session/bar units, numerical windows, target first decision and calculation from authenticated sessions. Swing minimum_history >=2, 0 < fast EMA < slow EMA <= minimum_history; breakout null or positive and < minimum_history. First15 opening minutes >0; ORB opening bars >0; VWAP pattern >=3 and optional positive stop window; Momentum lookback >0, enough bars beyond it, optional positive stop window. Only selected strategies apply | Derive dated acquisition extensions; retain 2022-04-01 raw and 2022-07-01 observation starts as original scope; G-I/S/A/U/D/P/X/O | Changing inputs can change selections/outcomes; rerun under new freeze, cannot preserve inspected holdout claim |
| Q03 What are the exact targets, outcome/label horizons and maturity rules? | Per target definition, units, start/termination/exit rule, incomplete/censored treatment, prediction freeze time, required post-window coverage and evaluation UTC; completed economic labels require actual exits | Extends prices/actions/execution and separates predictions from elapsed bars; G-M/F/O/H/V/Z/E | Yes if labels/targets/cutoffs change; no retrospective target selection |
| Q04 What inclusive EGX pilot start/end and first decision dates are chosen? | Contiguous dated interval with authenticated session mapping, Q02 warm-up and Q03 label extensions, no fixed dates supplied here | Defines EGX orders and official-index coverage; G-E/O/Z | Changed sample needs new pre-outcome freeze; spoiled holdout cannot be repaired |
| Q05 Which EGX eligible cohort and dated selection rule are frozen? | Stable IDs plus dated symbols/membership proof; persistent member and removed member where present; record symbol-change branch and complete-universe/index requirement | Prevents survivor selection; G-E/F/O/Z | Yes if universe/eligibility changes after outcomes; source corrections require versioned review |
| Q06 Which historically eligible Z08 cohort and selection rule are frozen? | Era-specific stable IDs and dated eligibility proof over full 2007–2009 envelope plus extensions; separately state authentic EGX reach or not testable | G-Z and era-specific G-T/I/S/A/U/D/P/X/C/O | Outcome-conditioned cohort change invalidates original OOS claim |
| Q07 Which historically eligible Z20 cohort and selection rule are frozen? | Same form for full 2019–2021; separate US/EGX coverage and no newer-cohort projection | G-Z/E/O and all era inputs | Same as Q06 |
| Q08 Which ETF/comparators and matching/allocation rule are frozen? | Named stable IDs, historical ETF classification, venues/currencies, matched stock cohort, common intervals/horizons, cost/FX and suitability/allocation criteria; no suggested tickers | G-F/X/C/O/H; extra venues require their own sessions and identity | Yes; post-outcome comparator selection invalidates original comparison |
| Q09 What exact rolling OOS folds are frozen? | Plan/fold IDs; nonempty tuple of UTC [train_start,train_end), [test_start,test_end), train end <= test start, ordered disjoint tests and nondecreasing training bounds | G-O/H; determines training/test data orders | Yes; refolding after inspecting results is a new research cycle |
| Q10 What additional embargo/gap is required beyond strict label purge? | Explicit duration/units per fold, including reasoned zero, expressed through Q09 boundaries; no unsupported new embargo schema or assumed default | G-O/H; reduces eligible sample; label < test-start rule always remains | Changing gap changes training set; invalidate prior frozen comparison if outcome-driven |
| Q11 What exact untouched holdout ID/start/end and release/evaluation time are frozen? | Positive UTC half-open interval strictly after every development test end; holdout evaluation >= interval end with matured labels; confirm uninspected | G-H; orders require legal custody-compatible access | Yes; observed holdout is never restored by relabeling boundaries |
| Q12 Who independently holds the holdout and how is non-inspection evidenced? | Named person/organization, independence/conflicts, authority, access roster/log, sealed hashes, freeze receipt, release rule, breach/recusal procedure, signed attestation | G-H; separates acquisition custody from researcher outcome access | Custodian replacement alone need not invalidate if signed chain remains intact; access breach or post-inspection redesign does |
| Q13 What forward collection interval, sample target/deadline and exact cutoff rules are frozen? | Future session qualification rule, dates and actual UTC per authenticated session; information/decision/evaluation cutoffs, publication completion, horizon, eligible non-trades/missingness and MISSED handling; no weekday inference | G-V/H; elapsed time unavoidable; immutable completion/lifecycle receipts required | Cannot change old cutoffs retrospectively; material policy changes create a new prospective series |
| Q14 What completed development/holdout trade minima and forward sufficiency target are adopted? | Explicit development and holdout integer minima each >=2; forward target/deadline and justification; no universal contract forward minimum; distinguish pilot structural coverage from statistical sufficiency | G-O/H/V; inadequate sample remains insufficient, bootstrap block may exceed sample | Lowering thresholds after results invalidates declared validation |
| Q15 What economic acceptance thresholds are adopted? | Explicit finite Decimal minimum_net_expectancy; maximum_drawdown_fraction >=0; minimum_profit_factor null or >=0; minimum_net_expectancy_lower_bound null or finite Decimal; justify optional nulls, units and cost basis | G-O/H; PF/win-loss/drawdown/expectancy and consistency remain reported; no hit-rate-only target | Yes; retrospective relaxation is not confirmation under original criteria |
| Q16 What validation scenarios and regime rules are frozen? | Canonical nonblank unique sorted scenario-ID tuple with full dated slippage/cost/fill definitions, baseline binding and regime provenance. Explicitly justify an empty tuple if chosen; no default scenario values. Required stress claims still need authentic era data | G-C/M/F/O/H/Z; each declared scenario must cover exact baseline observation set | Yes; dropping failed scenarios or changing regime membership invalidates original validation |
| Q17 What complete frozen research protocol and evaluation configuration are adopted? | Protocol/strategy/version IDs, plan, Q14–15 criteria, positive finite starting_equity, bootstrap integer seed, positive integer replications/block_size, confidence 0<level<1; US8 scenarios and UTC evaluation cutoffs. Development end <= development evaluation <= holdout start; holdout evaluation >= holdout end and development evaluation | G-O/H; runtime build clocks are separate and >= evaluation; evidence versions selected as-of, no holdout bootstrap tuning | Material changes require new identity/freeze; after holdout inspection require new untouched evidence |

D0 completion checklist: [ ] all Q responses signed; [ ] fixed scope preserved;
[ ] exact extension bounds calculated and signed; [ ] eligibility/calendar unknowns
explicitly blocking affected orders; [ ] canonical protocol validation passed;
[ ] independent non-inspection receipt and custody active. Status: **NOT EXECUTED**.
Forward policy must be frozen before trigger publication and effective by the
required information cutoff; recheck evidence receipt/review at freeze receipt.
A frozen file alone is not completed collection. Record MISSED only after cutoff
with approved exact session truth; unknown session truth remains NOT SCORED.
US8 execution evidence cutoff must be <= evaluation cutoff, request build <=
caller runtime build, and caller build >= evaluation cutoff. None of these
clocks may be backdated or used to turn post-close evidence into pre-market truth.

The original acquisition pilot already exists; do not call its retained prices
an untouched empirical holdout or infer an empirical preregistration from it.

## 4. D1 scope and decision routing

D1 includes R01 Tiingo, R02 Nasdaq directory/halts, R03 NYSE mapping, R04 NYSE
actions, R05 NYSE master, R06 EGX research rights; A01 independent reviewer,
A02 NYSE customer/access authority and A06 private agreement authority.
Foundation scope is combined U22 identity, complete XNYS common-stock universe,
exact sessions, complete all-type security/corporate actions, and retained daily
editions/rights, including archive, historical availability and completeness
commitments. Nasdaq/EGX rights reviews remain separate source-specific tasks.
P-N + P-S + retained daily rights are the foundation packet; candidate role
specifications do not select or rank vendors. D0 extensions must be resolved
before a bounded order can be accepted. No D1 signature authorizes purchase.

| Packet | Responsible human / exact response | Current state |
| --- | --- | --- |
| LEGAL / RIGHTS REVIEW | Qualified legal/rights authority completes §5 for all six sources and private agreements, signs exact granted/refused/conditional clauses and required use compatibility | UNANSWERED |
| DATA-OWNER / ACCESS REVIEW | Authorized customer/data owner completes §6 with documentary entity/product/archive/export authority | UNANSWERED |
| INDEPENDENT REVIEWER APPOINTMENT | Sponsor and independent human reviewer sign §7; custodian assignment separately satisfies Q12 | UNANSWERED |
| VENDOR-SCOPE ACCEPTANCE | Authorized source representative and research/data owner complete §9 field/date/edition commitments, omissions and role acceptance; any future contact needs separate authority | UNANSWERED |
| FOUNDATION PACKAGE ACCEPTANCE | Legal, data owner, research owner and reviewer sponsor sign §10 scope-readiness; actual independent package review and canonical admissions occur only after separately authorized delivery | UNANSWERED |

For each packet: `version/hash ___; decision ACCEPT / REJECT / CONDITIONAL / DEFER ___;
authority ___; scope/conditions ___; attachments ___; UTC ___; signature ___`.
CONDITIONAL/DEFER and blanks block affected next gates. Forms existing does not close D1.

## 5. Six-source legal / rights checklist

These are the **exact Phase-4 rights questions**, reproduced without answers.
A signed response must bind contracting/granting entity, recipient entity,
product, editions including already consumed copies, dates, governing terms,
clause/page, authority, restrictions and effective/expiry dates.

1. Is the exact retrieval method permitted for the named artifact/product and
   entity? Does the answer also cover previously retrieved retained editions?
2. May original bytes, metadata and normalized copies be stored locally? Where,
   by which users/applications, and for what period?
3. May historical editions/corrections be archived for audit and replay after
   subscription termination? What deletion requirements apply?
4. Are normalization, identity crosswalks, split-only transformations and joining
   to other licensed data permitted? Are derived values still restricted?
5. Are derived internal research outputs permitted, including performance and
   reproducibility artifacts? Can any output expose/reconstruct source data?
6. Are internal model training, backtesting, research and paper simulation
   permitted? What separate approvals/fees or use restrictions apply?
7. May internal collaborators and an independent reviewer inspect the exact
   bytes/attachments? Identify named-user, affiliate and confidentiality limits.
8. Is aggregate research publication allowed; what constitutes redistribution?
   Raw publication is not currently required. Record NOT_APPLICABLE for that
   intended use without implying permission for future publication.

| Source | Additional exact questions / material to review |
| --- | --- |
| R01 Tiingo | Do controlling terms for the retained IBM/TWTR daily artifacts permit archival research and transformations? Does any upstream exchange right constrain internal derivation or review? Phase-2 A manifests identify bytes/receipts |
| R02 Nasdaq | Answer separately for directory snapshots and halt feed/history, including archive/retention limits, automation, derived halt states, internal sharing and non-Nasdaq instruments; inspect Phase-2 C/J retained terms and fields, not a new rights search |
| R03 NYSE mapping | Does permission extend from published BQT documentation to actual dated mapping files, historical archive and stable identifier crosswalks? Are exchange/identifier sublicenses required? Phase-2 K |
| R04 NYSE actions | Are full historical event files, corrections, negative complete coverage and derived research use licensed? Distinguish temporary public upcoming/removal notices from customer archive rights; Phase-2 I |
| R05 NYSE master | Is retained historical master data/export licensed, including post-termination archive and independent review? Are customer access and data-use permissions separate? G03 v4.0.6 specification is not the grant |
| R06 EGX | Which permission governs raw prices, index levels/methodology/membership, universe, sessions and corporate actions for internal research? Does structured-product text apply at all to this use? Who can grant underlying rights when ICE/EGX.news delivers? G08 is insufficient |

Complete every cell below independently. `___` is unanswered, never approved.
Each response uses `GRANTED / REFUSED / CONDITIONAL / NOT_REQUIRED_FOR_DECLARED_USE`,
with signed clause evidence, dates and conditions. The last option is not a grant;
legal must confirm no required operation is excluded. R02 requires **two copies**,
one for directory and one for halt products; R06 requires per-product subresponses.

| Required use / restriction | R01 Tiingo | R02 Nasdaq (each product) | R03 NYSE mapping | R04 NYSE actions | R05 NYSE master | R06 EGX (each product) |
| --- | --- | --- | --- | --- | --- | --- |
| Exact retrieval method / prior retrieval permitted | ___ | ___ | ___ | ___ | ___ | ___ |
| Immutable local original bytes and metadata | ___ | ___ | ___ | ___ | ___ | ___ |
| Historical archive and correction retention | ___ | ___ | ___ | ___ | ___ | ___ |
| Every consumed historical edition covered | ___ | ___ | ___ | ___ | ___ | ___ |
| Normalization / split-only transformation / joins | ___ | ___ | ___ | ___ | ___ | ___ |
| Stable identity crosswalk and identifier use | ___ | ___ | ___ | ___ | ___ | ___ |
| Internal research / backtesting / paper simulation | ___ | ___ | ___ | ___ | ___ | ___ |
| Internal model training | ___ | ___ | ___ | ___ | ___ | ___ |
| Derived outputs / reconstruction restrictions | ___ | ___ | ___ | ___ | ___ | ___ |
| Internal collaborators / affiliates | ___ | ___ | ___ | ___ | ___ | ___ |
| Named independent reviewer exact-byte access | ___ | ___ | ___ | ___ | ___ | ___ |
| Reproducibility / export without active account | ___ | ___ | ___ | ___ | ___ | ___ |
| Post-termination retention / deletion deadlines | ___ | ___ | ___ | ___ | ___ | ___ |
| Upstream exchange / identifier restrictions | ___ | ___ | ___ | ___ | ___ | ___ |
| Storage jurisdiction / approved storage locations | ___ | ___ | ___ | ___ | ___ | ___ |
| Named-user / application / automation restrictions | ___ | ___ | ___ | ___ | ___ | ___ |

Raw public publication/redistribution is NOT_REQUIRED_FOR_DECLARED_USE in this
internal packet, not permitted by inference. Aggregate publication is deferred;
if later required, obtain a separate signed response to question 8 before use.
No redistribution purchase is requested. If archive retention is time-limited,
record exact term, lawful rerun/export plan and legal acceptance; otherwise the
reproducibility use remains blocked. Refusal rejects the source role, not the
underlying empirical requirement. No GLEIF/OpenFIGI/government grant substitutes
for another source's rights. **No right is approved by this packet.**

## 6. NYSE / customer / private data-owner access checklist

Complete a separate sheet for mapping R03, actions R04 and master R05, and each
A06 agreement affecting a role. Include P-S/session and retained-daily providers
where private delivery or agreements apply. Do not supply passwords, API keys,
account numbers, tokens, portal sessions or secrets.

| Required field / exact question | Human response and documentary reference |
| --- | --- |
| Authorized customer and contracting legal entity; who can attest authority? | ___ |
| Product name/version, provider and entitled use; which mapping/master/actions products are separately entitled? | ___ |
| Historical archive entitlement, inclusive dates, historical editions/corrections and retention rights? | ___ |
| Export custodian name/role, employer, delegation and approved receiving research entity? | ___ |
| Allowed delivery method and file/API-export format, controlled channel, named applications/users, storage jurisdiction? | ___ |
| Are exact consumed historical editions available, with immutable version IDs and original bytes rather than latest-only backfill? | ___ |
| Publication/acquisition/correction clocks, revision lineage and how superseded versions remain retrievable? | ___ |
| Exact fields/schema/units, MIC/security type, stable instrument scope, every-calendar-date/session bounds including D0 extensions? | ___ |
| Which stable identifiers/provider keys and dated crosswalks are included; identifier sublicense restrictions? | ___ |
| Is complete then-member membership included on every required open/early-close date, including removed/ineligible names and removal history? | ___ |
| Are ALL action types included with bounded completeness and explicit negative/empty coverage? | ___ |
| What omissions, unsupported dates/venues/fields and corrections SLAs are stated? | ___ |
| Private agreement owner, authorized signatory, governing executed scope, clause/attachment IDs and reviewer confidentiality permission? | ___ |
| Does existing authority permit the proposed non-purchase review/export, and what separate approval would any charged delivery need? | ___ |
| Signed data-owner attestation, effective dates, UTC and scope-limited decision? | ___ |

The retained NYSE master specification's 2015-12-16 archive start cannot close
Z08. Published BQT mapping schema is not historical mapping data. Upcoming public
notices do not prove complete actions. Customer access does not establish data-use
rights. For A06, unsigned or proposed clauses remain unresolved; no agent executes
an agreement. Authorized source commitments, rights and later delivered facts must
agree; any discrepancy returns the role to unresolved.

## 7. Independent reviewer appointment packet (A01)

Sponsor: ___; authority/organization: ___; appointed human reviewer: ___;
organization/role: ___; qualifications for source timing/completeness: ___;
appointment scope (R1, U22 foundation and named later packages): ___;
permitted exact-byte/attachment access under §5: ___; secure review location: ___;
confidentiality terms: ___; conflict declaration: ___; recusal/replacement rule: ___;
appointment UTC: ___; sponsor signature: ___; reviewer acceptance signature: ___.

Required independence: reviewer did not prepare the availability claim or select
empirical outcomes, is independent of this autonomous agent's preparation, and
can reject any package. Disclose source/vendor, acquisition, research and economic
conflicts and have sponsor resolve them before appointment. A model identity
string does not establish independence. The autonomous agent cannot approve its
own evidence. Reviewer and holdout custodian roles need explicit appointments;
one appointment does not implicitly confer the other.

Scope: inspect originals and exact evidence attachments, source authority,
field-specific edition availability, complete membership/actions, corrections,
rights basis and clocks; sign approve/reject **per subject edition and scope**.
Appointment itself approves no evidence. The narrow retained G14 fee-order review
in Phase 4 §6 can use that exact existing packet, but does not close foundation
rights, all-in costs or IBKR applicability; it is not a new D1 acquisition task.

## 8. Reviewer evidence template (one per exact subject package)

| Field | Fillable record |
| --- | --- |
| Review task / subject package ID / version / gate / D0 protocol hash | ___ |
| Provider / source authority / source locator / evidence category | ___ |
| Original-byte SHA256 / byte count / local path / raw record locators | ___ |
| Raw receipt identity / actual request and receipt UTC / custodian | ___ |
| Exact source edition / publication-effective dates / edition inventory | ___ |
| Historical availability kind EXACT or BOUNDED_INTERVAL; exact UTC or inclusive start/end; authority proof per consumed field/version | ___ |
| Covered scope: MIC/type, stable IDs, date bounds; exact covered_fields tuple | ___ |
| Revision semantics: corrections, supersession, available-at versus effective-at | ___ |
| Attachment set: each locator, attachment ID, SHA256, actual receipt UTC, description, source-authority context | ___ |
| Availability evidence ID / package identity / corresponding receipt and SHA | ___ |
| Signed rights grant/product/edition clause references / access authority | ___ |
| Completeness claim: every date/member/type; explicit negative action coverage; known gaps | ___ |
| Methodology: independent roster/removal checks, original edition comparisons, field/timing checks, hash recomputation and limitations | ___ |
| Simulated decision UTC / research build UTC / maximum historical availability UTC | ___ |
| Reviewer identity / organization / appointment and independence declaration | ___ |
| Approve or reject by scope / reasons / failed fields and missing attachments | ___ |
| Review UTC / signed attestation / signature artifact hash | ___ |

Exact review questions; record YES / NO / UNPROVEN plus attachment/row references:

- Does recomputed hash/size identify the actual subject bytes and receipt edition?
- Does every consumed field/version have authoritative historical availability,
  with latest possible availability <= its simulated decision?
- Are raw receipt, attachment receipts and review all <= research build, using
  actual canonical UTC clocks without backdating?
- Do evidence and review bind the identical subject receipt/SHA, availability
  evidence identity and exact attachment-ID/SHA set, without additions/omissions?
- Do dates, stable identities, all members and all action types—including negative
  coverage—meet the precise completeness claim without survivor filtering?
- Are correction semantics explicit, originals retained and no latest-only data
  substituted for a historical edition?
- Do signed rights/access cover exact consumed copies, transformations, reviewer
  access and the required reproducibility period?
- Are every mandatory gate field and scope supported, and all conflicts,
  unsupported events, exclusions and quarantines preserved?
- Can you independently approve this precise claim under your appointment?

Any NO/UNPROVEN on a mandatory requirement rejects that scope; narrow approval
must create a precisely narrower package and cannot silently satisfy the full
order. Final signed text: `I [name ___], under appointment ___, independently
reviewed subject ___ and exact attachments ___ using methodology ___ at UTC ___;
I APPROVE / REJECT ___ scope for reasons ___, subject to stated limitations ___`.
Canonical R1 `HistoricalSourceReview` binds reviewer, reviewed_at, methodology,
approved, subject_receipt_id, subject_sha256, availability_evidence_id and exact
attachment references. The signature/authority/rights record is retained alongside
it; constructors do not authenticate signatures or legal rights.

Corrections: retain rejected/original bytes, receipts and reviews; receive a new
edition only under later authority, compute new hashes, establish its own actual
availability and acquisition clocks, produce new evidence/review identities and
rerun affected admissions. Never overwrite, backdate, reuse approval for changed
bytes/fields/attachments, or select a revision learned after the evaluation cutoff.

## 9. Vendor-scope acceptance packet (no order)

Source representative/authority ___; named product/role ___; customer entity ___;
D0 hash ___; exact instrument/MIC/type/date scope ___; response edition/date ___;
source signatory ___; research/data-owner counter-signature ___; decision ___.
For each §10 field, return `SUPPORTED / UNSUPPORTED / CONDITIONAL`, schema field,
units, coverage, original edition proof, correction policy, sample/attachment ID
from authorized retained material, completeness methodology and exception list.
No new sample retrieval is authorized here. If unavailable, leave it unresolved.

Required commitments: [ ] exact identity crosswalk; [ ] full then-member roster;
[ ] every calendar date and true session hours; [ ] all-type action ledger or
explicit complete-empty evidence; [ ] original daily raw editions;
[ ] immutable original bytes/receipts; [ ] publication/revision chronology;
[ ] historical archive and correction lineage; [ ] reproducible export/retention;
[ ] rights and reviewer access; [ ] authorized customer/export custodian;
[ ] no unsupported field hidden by marketing claims. Attach signed P0/P-N/P-S
schedule from Phase 4, with all gaps disclosed. Charged scope requires D2 budget
approval later; this acceptance is technical/legal scope only.

## 10. Foundation acceptance matrix and sign-off

Mandatory source-field dictionary, reproduced from Phase 4:

- **F-I:** `effective_date`, stable `instrument_id` UUID, `canonical_symbol`,
  `listing_mic`, `security_type`, `currency` (USD in US contract),
  `provider_symbol`, `source_provider`, `source_instrument_key`,
  `is_primary_listing`. Source keys map to stable IDs by dated evidence, never
  by ticker alone. Preserve symbol reuse, both sides of changes and removals.
- **F-S:** `market_date`, `calendar_mic`, `timezone_name` America/New_York,
  `state` REGULAR/EARLY_CLOSE/CLOSED, `opens_at_utc`, `closes_at_utc` (absent on
  closed dates); explicit corrections, not inferred DST or MIC equivalence.
- **F-U:** `effective_date`, `complete`, `members`; each member has stable ID,
  canonical symbol, listing MIC, security type, explicit `eligible`. Include
  negative/ineligible facts and evidence of completeness; absence is not empty.
- **F-A:** stable `instrument_id`, `coverage_start`, `coverage_end`, `complete`,
  `actions`; event identity, type, effective date and exact terms, split old/new
  shares, symbol-change old/new symbols, cash dividend USD amount; mergers,
  spinoffs, stock dividends, rights, delistings and OTHER retained. Event,
  announcement, ex-date, payment, suspension and removal dates stay distinct.
- **F-D:** stable ID, `market_date`, `calendar_mic`, canonical/provider symbols,
  provider instrument key, USD, RAW_UNADJUSTED, open/high/low/close/volume,
  `provider_adjusted_close_reference` (nullable audit-only), `source_provider`,
  `source_snapshot_date`, `source_row_number`, `source_sha256`. Positive coherent
  raw OHLC, nonnegative volume; no interpolated or adjusted execution prices.

All seven gates bind U22 original bounds and every D0-approved extension. Session
coverage is every calendar date/MIC; universe coverage every open/early-close date;
identity covers every consumed stable identity/date; prices cover each eligible
open-session date, not nonexistent post-removal trading. All roster members must
be retained even when only IBM/TWTR are derived. Other scopes require separate
matching admissions; U22 approval cannot approve EGX, Z08/Z20 or ETFs.

Common G-T fields: provider/source/locator/category, original SHA256/byte_size,
actual local_received_at, nonempty source_edition, exact covered_scope and
covered_fields, revision_semantics, EXACT or bounded availability, and exact
attachment/review identities and hashes. Latest availability <= decision;
receipt/attachments/review <= research build. Independent signed review required
for each source package; derived G-P inherits exact approved dependencies and
requires review of composition/exclusions, not a fabricated vendor approval.

| Gate | Exact source inputs / mandatory fields | Coverage / edition evidence / independent review | Canonical admission function | Focused test / later auditor | Conditional downstream gates |
| --- | --- | --- | --- | --- | --- |
| G-T | Exact original retained daily editions and later authorized P-N mapping/master/actions, P-S sessions; Nasdaq only its proved role; common R1 fields above plus rights/access records | Every consumed edition/field/scope; §8 signed availability/attachment review; public spec or receipt alone fails | `require_historical_evidence` with `HistoricalEvidencePackage`, `app/research/historical_evidence.py` | `tests/test_r1_historical_evidence.py`; actual receipt/attachment rehash and package admission | G-I/S/A/U/D and all other historical gates |
| G-I | P-N dated mapping + master/listing evidence, F-I; Nasdaq directory only if dates/identity/venue actually proved | All consumed stable IDs/dates; both sides of change/reuse/removal; independent continuity/provider-binding review | `admit_us_listing_history`, `app/us/historical_identity.py` | `tests/test_us1_historical_identity.py`; actual listing admission | G-U/D/X/P |
| G-S | P-S exact historical sessions and revisions, F-S | Every calendar date XNYS, including closed; exact UTC hours and correction edition; independent review; no inferred DST/MIC | `admit_us_session_history`, `app/us/historical_session.py` | `tests/test_us2_historical_session.py`; actual session admission | G-U/D/X/P; separate future session qualification |
| G-A | P-N all-type original action files/corrections with stable identity binding, F-A | Complete bounded per-instrument coverage, affirmative empty ledger where true; independent all-type/negative-coverage review | `admit_us_corporate_action_history`, `app/us/historical_actions.py` | `tests/test_us4_historical_actions.py`; actual action admission | G-P supported split-only derivation; unsupported crossings excluded |
| G-U | P-N complete historical master/membership plus admitted G-I/S and original roster/removal evidence, F-U | All then-members each open/early-close date; no current constituents or two-name roster; independent completeness review | `admit_us_universe_history`, `app/us/historical_universe.py` | `tests/test_us5a_historical_universe.py`; actual universe admission | G-P survivorship-safe membership |
| G-D | R01 retained Tiingo original daily rows/editions plus G-I/S; future P-H only if separately accepted; F-D | Every eligible open date for pilot targets; exact-field historical edition proof and independent review; retain TWTR quarantine | `admit_us_daily_bar_history`, `app/us/historical_daily.py` | `tests/test_us3_historical_daily.py`; actual daily admission | G-P once G-A/U also admitted |
| G-P | Exact admitted G-I/S/A/U/D histories, requested bounds, decision/build clocks, dependency identities, supported split terms and explicit exclusion reasons | Common bounds, sufficient selected strategy history, no future action projection; dependency reviews plus composition/exclusion audit | `build_us_retrospective_pit_daily_dataset`, `app/us/retrospective_pit.py` | `tests/test_us5b_retrospective_pit.py`, `tests/test_er1c_us_pilot_audit.py`; `tools/audit_er1c_us_pilot.py` on its supported retained bundle plus actual canonical G-P admission | Research inputs for G-M/F/O/H/V, only after execution/cost/other gates |

Later offline auditor invocation: `python tools/audit_er1c_us_pilot.py <authorized-bundle>
--predeclaration docs/ER1C_US_PILOT_PREDECLARATION.md`. It checks its supported
pilot bundle, not arbitrary vendor delivery and not every canonical admission.
Run each actual function with genuine reviewed inputs and record accepted/rejected
IDs, exceptions, input/output hashes, protocol hash, code HEAD and build clocks.
Focused fixture tests never admit real evidence. EGX uses `HistoricalPITInput` /
`derive_research_pit_daily` in `app/research/historical_pit.py` and
`tests/test_r1_2_historical_pit.py`, with its own official-index evidence; US gates
cannot be reused as EGX admission.

Foundation sign-off has two separate records:

1. **D1 legal/scope/access acceptance:** D0 scope hash ___; completed six-source
   decisions ___; accepted foundation required-use rights ___; signed archive/
   timing/completeness commitments ___; A01/A02/A06 records ___; unresolved
   conditions ___; legal/data-owner/research-owner/sponsor signatures and UTC ___.
   A rejected optional source needs an explicit role disposition; a required
   foundation right/role cannot be waived. Pending responses keep D1 open.
2. **Post-D2 evidence admission:** per-gate actual package/review/result IDs ___;
   original evidence hashes ___; exclusions/quarantine ___; auditor output ___;
   reviewer/data-custodian/research-owner signatures and UTC ___. This second
   record cannot be completed before authorized delivery and actual admissions.

D1_STATUS: **OPEN — NO HUMAN DECISIONS OR SIGNATURES SUPPLIED**.
FOUNDATION_ADMISSION_STATUS: **NOT_ADMITTED**. Scope acceptance is not data admission.

## 11. Exact D2 entry criteria — foundation procurement

D2 may enter bounded offer/budget decision only when all are documented:

- D0 signed U22 preservation and exact strategy/label extensions, with resolved
  identity/calendar-dependent ordering bounds and a frozen protocol/custody plan.
- D1 legal acceptance of required foundation uses for every proposed product and
  retained daily edition; all R01–R06 have signed dispositions, with Nasdaq/EGX
  scope separate and unresolved requirements explicitly blocking their own use.
- D1 signed P0/P-N/P-S field/date/edition/archive/timing/completeness acceptance
  covers the entire combined U22 foundation, discloses omissions, and gives a
  compliant source for every required role; no unsupported slot is presumed filled.
- A01 reviewer appointed with licensed exact-byte access; A02 customer/product/
  archive/export custodian authority documented; A06 controlling private authority
  and required agreement scope/rights executed or otherwise lawfully established
  by the authorized humans, without an agent accepting agreements.
- No open condition blocks the proposed scope. Foundation §10 record 1 is signed.

**D2_ENTRY_GATE = signed D0 bounded scope + signed D1 legal/scope/access/reviewer
acceptance for the complete foundation.** This permits consideration of exact
bounded offers, not purchase. A separate human budget/procurement decision must
approve named products, actual offer, price/usage cap, delivery and retention
before any charged procurement. P01/P03 and any charged P-N are candidates only;
no requirement to buy all. Actual delivery/review/G-T/I/S/A/U/D/P admission is
D2 output, not a circular requirement to begin D2. No prices or spending authority
are supplied by this packet.

## 12. Exact D3 entry criteria — execution / quote / cost procurement

**D3_ENTRY_GATE = D1 accepted foundation rights/scope/access/reviewer commitments
+ D0 frozen execution/cohort/comparator/horizon/scenario scope + an explicit
foundation dependency disposition.** For a usable economic package, D2 must
actually admit the matching G-T/I/S/A/U/D/P scope. Bounded D3 scope/offer review
may be prepared while D2 delivery is pending, but cannot claim those gates passed;
record the dependency and do not accept a stand-alone execution purchase as PIT
closure. No D3 spending is authorized by D1 or by this packet.

Before any D3 purchase/delivery, the authorized owners must additionally accept
P-X/F-X/F-C scope: real opening-origin final contiguous M1/M5 OHLCV and actual
volume through complete lifecycles, quote/spread support, matching dated identity/
sessions/actions, archived publication/correction editions, lawful reviewer and
replay rights, explicit supported cost/slippage/participation assumptions, and
all selected ETF/venue/date coverage. Cboe's retained 2010 advertised start cannot
satisfy Z08. Databento A03 identity/entitlement and P05 usage/cap approvals remain
separate operator/procurement decisions; no account access is requested here.

A05 IBKR applicability sheet must be completed with dated documentary references
and independent review (Phase 4 §7), using minimal redacted records:
`contracting entity ___; pricing jurisdiction/residency ___; account type ___;
products/venues/routes ___; plan fixed/tiered/other ___; volume tier qualification
and changes ___; per-side commission/minimum/maximum ___; regulatory/exchange/
clearing/tax/rebate basis ___; currencies ___; FX manual/automatic/prefunded path,
pair/direction/timing/rate/spread/minimum ___; private exceptions ___; applicable
dates/source editions ___; operator authority/signature ___; reviewer ___`.
No credentials or full account number. If no historical applicable account
existed, distinguish supported prospective paper scenarios from actual historical
IBKR costs; a reasoned not-applicable response does not waive complete cost
support. Nonlinear tariffs that cannot be faithfully represented remain a stated
limitation, not an invented constant or a software task in this phase.

D3 spending gate additionally requires a separate exact offer, human budget/
usage-cap approval and permitted delivery. D3 output requires G-X/C/M/F actual
admission/replay and matched comparator review; G14 government fee evidence alone
is neither all-in economics nor account applicability. Live broker execution
remains outside every D0–D3 authorization.

## 13. Exact remaining human decisions and next action

Remaining: Q01–Q17 and derived dated bounds; R01–R06 full signed rights responses;
A01 independent reviewer appointment; A02 customer/product/archive/export inputs;
A06 private agreement/signatory/reviewer-access authority; all five §4 packet
signatures and precise conditional/rejected-role dispositions. Q12 independently
requires holdout custody and evidence of non-inspection. Later D2 budget/offer and
D3 A03/A05/quote/cost decisions are not inferred or decided here.

**Exact next human action:** the research owner and independent protocol custodian
complete/sign Q01–Q17 while preserving the original U22 freeze, compute exact
order extensions from authenticated facts, and record the protocol hash and
non-inspection receipt. The sponsor names and appoints the independent reviewer;
legal and data owners complete the six-source rights and NYSE/A06 authority
sheets, then sign the combined foundation scope acceptance. Any later external
request must be separately authorized; this document sends none. Do not order
data while mandatory scope, rights, archive or authority answers remain blank.

## 14. Verification and terminal status

Only `docs/D0_D1_DECISION_REVIEW_PACKET.md` is proposed for commit. No software
contract defect found, no software/test changes, no acquired evidence, no changed
Phase-2/3 artifacts or Phase-4 plan. No full suite required for this document.
Focused offline regression: **478 passed in 83.83s (0:01:23)**. Command:

```sh
PYTHONDONTWRITEBYTECODE=1 /home/egx-agent/work/egx-trading-platform/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_m8_research.py tests/test_m8b_evaluation.py tests/test_us8_replay_validation.py tests/test_r1_historical_evidence.py tests/test_er1c_us_pilot_audit.py
```

Log: `/home/egx-agent/er1-autopilot/state/phase5-focused-tests.log`, SHA256
`434f465a8d611948493e4ebd4a26b5900e845f5af35ae2bb21bbd3c2da184dcb`.
`tests/conftest.py` denies real network connections. No empirical tests run.
Verified all 67 Phase-2 inventory files, 24 Phase-3 response bodies and original
receipts unchanged; Phase-3 manifest retains SHA256
`5031e650a7cb662a1147476aac0d0e4720512d33176ce69c205e30fb3f6441aa`.
Exact changed-path check: this document only; index empty. `git diff --check`
and new-file `git diff --no-index --check` passed. Reviewed complete new-file
content/diff; verified 14 sections, 17 Q rows, contract paths and local links.

Supervisor request: message `docs: prepare D0 D1 evidence closure packet`, exact
path `docs/D0_D1_DECISION_REVIEW_PACKET.md`, submitted through
`/home/egx-agent/er1-autopilot/state/COMMIT_REQUEST.json`. Commit and final clean
read-only verification are pending the supervisor; no future HEAD is asserted.
No network/API calls, downloads, account access, messages, secrets access,
purchases, production changes, broker execution or readiness advancement occurred.
The supervisor alone runs its existing `commit_bridge.py` (which contains a private
leak guard); the agent submits `COMMIT_REQUEST.json`, never direct staging/commit.
Post-supervisor read-only verification must confirm exact committed path, message,
parent HEAD, clean tree and diff checks before reporting AUTOPILOT_STATUS: DONE.

D0_D1_PACKET_STATUS: **READY_FOR_HUMAN_DECISION**

EMPIRICAL_READY: **NO**

LIVE_READY: **NO**

D0/D1 decisions and real evidence remain open. Administrative terminal target
is AUTOPILOT_STATUS: DONE only after the supervisor commit and clean final check;
forms alone never close D1 or admit empirical inputs.
