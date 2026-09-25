# Phase 3 — external evidence and empirical readiness

Assessment: 2026-09-25. Starting HEAD:
`9e8dab4fc03fd373e9f77d92b1dfd60da0542794`, branch
`agent/er1c-free-acquisition`. Initial worktree was clean. This mission explicitly
authorizes this repository and overrides the older AGENTS workspace/branch rules.
Phase 1 and Phase 2 remain closed and unchanged.

**EXTERNAL_EVIDENCE_STATUS: PUBLIC_SCOPE_REACHED** for the bounded source queue
below. **EMPIRICAL_READY: NO. LIVE_READY: NO.** Acquisition/qualification is
complete; supervisor commit and post-commit verification remain administrative
work. No future commit or clean final worktree is asserted here.

## Evidence record

New evidence root:
`/home/egx-agent/research-data/phase3/20260925T161516Z-public-qualification/`.
Direct acquisition ran from `2026-09-25T16:16:01.358748+00:00` through
`2026-09-25T16:19:17.326423+00:00`. There were **25 direct HTTP requests**:
**22 HTTP 200**, **2 HTTP 403**, and **1 transport failure without a response**.
All **24 response bodies** were preserved and rehashed. A filename ending in
`.pdf` is not proof of PDF content: the SEC PDF request returned an HTML error.
Public web search/open/find requests were additional discovery/qualification
calls, not included in the 25 direct captures. No paid market-data API was called.

The repository retains metadata only:

- [Acquisition manifest](external-evidence/acquisition-manifest.json): every
  requested/resolved URL, provider, exact request/receipt clock, status, content
  type, byte length, SHA256, original receipt hash/path, source group, intended
  admission role, rights basis, known edition/date and limitation.
- [Admission and integrity audit](external-evidence/admission-audit.json): exact
  real raw receipts and canonical constructor refusals, test command/result,
  old-evidence integrity verification and log hash.

The raw root also retains the original per-request receipt sidecars, acquisition
script/request lists, final manifest and audit. Original bytes and sidecars were
not rewritten. Later edition/date annotations are in the final manifest, with
unknowns left null. HTTP headers and document dates were not converted into
historical availability. The acquisition helper refused a duplicate receipt
basename before a second request; the PDF attachment was captured under a new
name. This was an acquisition naming collision, not a product software defect.

Raw `manifest.json` SHA256:
`5031e650a7cb662a1147476aac0d0e4720512d33176ce69c205e30fb3f6441aa`.
It is byte-identical to the repository acquisition manifest. Its sidecar is
digest-only. The manifest binds all acquired source-body hashes; it is not an
independent attestation of their historical availability or completeness.

## Current dependency order

Readiness was read first, then checked against current R1, US1/US2/US4/US5A,
US5B composition, US7B, M7 and US8 contracts and their tests. This is the order
required by the contracts, not a proposal to change architecture:

1. Source rights and real receipt/edition/hash, field/scope-specific historical
   availability attachments, then independent approved review. R1 binds the
   exact attachment set and receipt; receipt/review must be no later than research
   build, and latest proven availability no later than the simulated decision.
2. Exact-date listing identity (US1), every-calendar-date/MIC sessions (US2), raw
   daily bars (US3), complete bounded all-type actions (US4), and complete
   exact-date then-membership (US5A) compose into US5B. Missing dates cannot be
   inferred from nearby records, activity, issuer identity or current symbols.
3. Admitted PIT strategy/risk inputs plus separately admitted opening-origin
   M1/M5 execution coverage (US7B/US7C), actual volume and supported costs are
   needed for canonical M6/M6.1 observations and therefore operational M7/M7.1.
4. Comparable stock/ETF observations require those same gates and matched
   periods/costs plus suitability/allocation evidence. Classification alone
   does not create a comparator. Account-specific IBKR economics add a separate
   applicability gate; generic paper assumptions cannot satisfy it.
5. M8/US8 require authentic canonical samples and frozen protocols, training/OOS
   boundaries, purge/embargo, complete scenarios and matured labels. Holdout
   non-inspection and timely forward collection are additional chronology
   requirements. Severe 2008/2020 coverage remains required by the readiness
   scope and US8 delivery boundary; it was not weakened or replaced with 2022.

EGX has its own R1 historical identity/universe/session/action/index and
execution dependencies. US source facts cannot substitute. Forward collection
also needs an approved exact future session and timely frozen candidates; a
new calendar download would not reconstruct a missed cutoff.

## Source qualification and stopping decisions

Group IDs below resolve to exact URLs, retained artifact paths, clocks and
hashes in the manifest. These results apply to the intended admission role,
not to the general usefulness of a provider. Every group has zero downstream
gates unlocked and no empirical validation run.

| Group | Source / authority | Result and exact admission limitation |
| --- | --- | --- |
| G01 | [GLEIF terms](https://www.gleif.org/en/meta/lei-data-terms-of-use) and API documentation; authority for LEI reference data | ACQUIRED_NO_GO. Explicit CC0 rights for Access Service data resolve this new source's data reuse question. Legal-entity/ownership data do not establish exact-date exchange listing, primary-listing status or provider bindings. No bulk LEI data fetched after scope failure. |
| G02 | [OpenFIGI FAQ](https://www.openfigi.com/about/faq) and API specification; registration metadata authority | ACQUIRED_NO_GO. FIGI/metadata storage and reuse are explicitly permitted. The documented mapping response has no historical effective-date or primary-listing history. No ticker/name mapping was promoted to historical identity and no unnecessary mapping payload was requested. |
| G03 | [NYSE Security Master v4.0.6](https://www.nyse.com/publicdocs/nyse/data/NYSE_Group_Security_Master_Client_Specification_v4.0.6.pdf); exchange product specification | AUTHORIZATION_BLOCKED. Daily referential product could address identity/universe, but described customer access requires dashboard/IP/SSH-key arrangements. No credential endpoint was attempted. Archive starts December 16, 2015, excluding 2008. Specification is not a historical file or rights grant; its December 8, 2022 edition is after the pilot interval. |
| G04 | [TradingHours](https://www.tradinghours.com/data); secondary session vendor | PAID_SOURCE_BLOCKED. Historical schedules are a licensed commercial product with annual application-specific licenses; no anonymous complete date/MIC package acquired. Existing NYSE calendar limitations remain unchanged. |
| G05 | [Databento pricing](https://databento.com/pricing) and dated pricing-policy article; secondary vendor | AUTHORIZATION_BLOCKED. Signup credits require account identity/API access; paid usage/subscriptions are also out of scope. Raw pricing response is an application shell with signup metadata, not a complete rendered tariff. No account, API key, credits or market-data request used. |
| G06 | [IBKR stock pricing](https://www.interactivebrokers.com/en/pricing/commissions-stocks.php) and FX pricing; authority for published broker documentation | AUTHORIZATION_BLOCKED. Public pricing distinguishes plans and FX treatments but cannot identify the user's account entity, residency, tier, route/product or historical rates. No rates were installed as empirical costs; no account or broker operation attempted. |
| G07 | EGX legacy homepage; official endpoint | PUBLIC_SOURCE_NOT_FOUND for this failed endpoint attempt. Connection closed without response. This does not imply EGX has no public site: G08 investigates the newly discovered beta site. |
| G08 | [EGX beta site](https://beta.egx.com.eg/en), index overview and license page; exchange authority within stated scope | RIGHTS_BLOCKED. Index licensing text addresses structured-product licenses, not permission for required research retention/transformation. That gap remains unknown, rather than a finding that all research is forbidden. Current disclosures/overview are not complete historical market evidence. |
| G09 | [ICE EGX catalogue](https://developer.ice.com/fixed-income-data-services/catalog/egyptian-exchange-egx); vendor description of exchange feed | AUTHORIZATION_BLOCKED. Client/contact access path, no qualified anonymous archive. General history depth does not prove specific EGX 2008/2020 coverage. No contact message or account access performed. |
| G10 | [EGX.news](https://www.egx.news/en/our-data); third-party seller, not the exchange | PAID_SOURCE_BLOCKED. Historical daily/minute CSV offerings have prices. Neither market files nor trials purchased; seller coverage claims were not admitted. |
| G11 | [Cboe stock/ETF intervals](https://datashop.cboe.com/equity-etf-quotes); official product description | PAID_SOURCE_BLOCKED. Comparable interval OHLCV/quotes are offered for purchase/subscription; advertised history starts in 2010, not 2008. No sample downloaded as a substitute for complete comparable observations. |
| G12 | [Norgate packages](https://norgatedata.com/stockmarketpackages.php) and content tables; secondary vendor | PAID_SOURCE_BLOCKED. Delisted and historical constituent features need paid Platinum/Diamond access. Trial/account creation is outside scope. Index membership also cannot automatically become a complete exchange roster. |
| G13 | SEC direct reuse-FAQ and fee-order URLs | ACQUIRED_NO_GO. Both raw requests returned HTTP 403 threshold-error bodies. No source fact comes from them. No retries or circumvention; G14 uses a separately published official government edition. |
| G14 | [GPO fee order](https://www.govinfo.gov/content/pkg/FR-2022-04-13/pdf/2022-07881.pdf), HTML, OFR metadata and GovInfo policy; government authority | ACQUIRED_NO_GO. Authentic narrow cost/publication evidence acquired, but mandatory independent review and complete applicable execution economics remain absent. Details below. |

Searches covered new open identifier sources, exchange historical master/action
products, official EGX archives/index licensing, exact-session vendors, free
intraday sources, broker applicability, official fee orders and government
republication, comparable stock/ETF data and survivorship/stress-era providers.
Community/mirror and aggregate-return search results were discovery only: no
claim from them was admitted. Norgate, Cboe and Databento document potential
commercial paths, not authority to obtain them.

Historical Tiingo, Nasdaq symbol/halt and NYSE mapping/corporate-action rights
investigations were not repeated. Their unresolved rights remain
**RIGHTS_BLOCKED** for empirical use. New GLEIF/OpenFIGI grants do not apply to
those old sources. The new EGX license page did not resolve its older unknowns.
NYSE Security Master dataset rights are likewise unestablished; obtaining its
customer access would still require scope/license review.

## Acquired government cost evidence

G14 retains government-authored SEC Release 34-94644 as FR Doc. 2022-07881,
87 FR 21931–21938, published April 13, 2022. The order is dated April 8 and sets
the Section 31(b)/(c) charge at **USD 22.90 per USD 1,000,000**, effective
May 14, 2022. This is a narrow regulatory-charge fact, not a broker commission
or all-in trade cost. Its printed filing timestamp and publication metadata are
review material; no approved exact-edition availability claim was constructed.

| Raw artifact | SHA256 |
| --- | --- |
| `govinfo_fee_order_2022.html` | `7065dc5e97773e108eb8e07da3242f629be579bb534bd2600d696e5da9f5b384` |
| `govinfo_fee_order_print_2022.pdf` | `09da44f8651bd1510f1dad7e78a40989dabf22c2ca3e177b382d6891467b185c` |
| `federalregister_fee_metadata.json` | `c01bd14cdc9415885a50ef3497569046f4454180e0d08894b7962a390bc1d564` |
| `govinfo_policy.html` | `d9e3a2f1cb4486ab4ecc4eff640f94f9a9bb8655ca71ff00694b236e7011ad1c` |

Rights basis is the captured [GovInfo government-works policy](https://www.govinfo.gov/about/policies),
limited to this government-authored order. No third-party license was inferred.
The official SEC reuse FAQ was also inspected through the browser; its raw
403 capture cannot itself establish that policy. The PDF header, HTML release,
effective-date text and OFR document number/publication date/PDF URL match.
PDF cryptographic-signature validation was not performed or claimed.

## Blocker queue reconciliation

All rows refer to the source groups above (and unchanged Phase-2 catalogue where
stated). Exact retained provenance is inherited from those groups. For every
row: canonical admission is NO_GO, downstream gates unlocked are none,
empirical validation is NOT RUN, and empirical sample remains zero, unless
explicitly marked ALREADY_SATISFIED or NOT_APPLICABLE.

| Domain | Dependency / attempted sources | Classification | Remaining requirement / exact NO_GO reason |
| --- | --- | --- | --- |
| Historical security identity | R1 then US1; G01–03, Phase-2 SEC corroboration | ACQUIRED_NO_GO | No complete exact-date stable listing/provider crosswalk or approved review. Commercial G03 additionally AUTHORIZATION_BLOCKED. |
| Historical universe | R1/identity/sessions; G03/G12, Phase-2 activity/index probes | PUBLIC_SOURCE_NOT_FOUND | No public complete exact-date then-member roster package found; customer/subscription paths cannot be used. Activity and current membership cannot fill gaps. |
| Exact per-date sessions | R1/US2; G04, existing calendar audit | PAID_SOURCE_BLOCKED | No reviewed every-date/MIC explicit session package. General schedules do not establish corrections or actual session truth. |
| Complete bounded actions | R1/stable identity/US4; G03/G12 scope, closed Phase-2 NYSE/issuer probes | PUBLIC_SOURCE_NOT_FOUND | No public complete all-type bounded ledger or explicit complete empty coverage found. Referential product/schema or dividend/split history cannot substitute. |
| Historical edition/timing | R1; G14 publication materials, G01–03 scope | ACQUIRED_NO_GO | Fee-order edition materials are useful but unreviewed; none supplies consumed historical bar/roster/session/action editions and correction availability. Current receipt is never historical availability. |
| Independent approved review | Exact receipt/hash/scope/attachments, resolved source gaps | AUTHORIZATION_BLOCKED | No independent authorized human review supplied. Agent did not impersonate or self-approve a reviewer. |
| Authentic execution bars | PIT plus US7B/US7C; G05/G09/G11 | AUTHORIZATION_BLOCKED | No canonical opening-origin final contiguous M1/M5/volume/source-time evidence. G11 paid alternative cannot repair this. |
| Authentic execution costs | Applicable dated charges and spread/slippage/participation; G14/G06/G11 | ACQUIRED_NO_GO | One regulatory order is not a complete cost set or evidence of spreads, impact, fills or participation assumptions. |
| IBKR rates/FX/applicability | Costs plus private account/tier/entity/date facts; G06 | AUTHORIZATION_BLOCKED | Public current tariff does not prove actual account, historic tier or FX treatment. No verified IBKR economics. |
| Canonical M6/M6.1 observations / M7 | Admitted strategy/risk/execution/costs; G05/G06/G11 upstream | PUBLIC_SOURCE_NOT_FOUND | Zero authentic canonical observations. NAV, Shadow arithmetic and downloaded schedules are not M7 input. |
| Comparable ETF evidence | Matched admitted periods/costs/observations and suitability; G02/G11 | PAID_SOURCE_BLOCKED | Cboe product would still need all upstream admission gates. No matched stock/ETF sample or allocation evidence. |
| Predictive horizon/probability | Fixed predictions/labels and admitted outcomes | PUBLIC_SOURCE_NOT_FOUND | Upstream samples absent; third-party published model results would not validate this strategy. No calibration or confidence claim. |
| Real OOS / M8 / US8 | Canonical dataset and frozen folds/version/criteria/scenarios | PUBLIC_SOURCE_NOT_FOUND | No admitted trading dataset; no evaluation, tuning, invented folds or outcomes. |
| Untouched holdout | Pre-outcome freeze and non-inspection history, matured admitted labels | PUBLIC_SOURCE_NOT_FOUND | No qualifying untouched sample; no holdout downloaded or examined. A current historical download cannot manufacture a past freeze. |
| Forward validation | Reviewed future sessions and timely frozen strategy collection | PUBLIC_SOURCE_NOT_FOUND | No timely admitted collection/lifecycle sample. Requires resolved upstream gates and elapsed future time. No retrospective forward reconstruction. |
| EGX empirical evidence | EGX R1/index/PIT/execution/costs; G07–10 | RIGHTS_BLOCKED | New official site found but required research-use rights unresolved; alternate client/paid sources blocked. No complete admitted EGX package. |
| 2008/2020 stress coverage | Era-specific cohort and all upstream admission gates; G03/G05/G09/G11/G12 | PUBLIC_SOURCE_NOT_FOUND | No admitted era sample. G03 begins 2015 and G11 begins 2010; commercial deeper-history claims do not establish canonical coverage. No 2022-cohort back-projection. |
| Durable MISSED receipts | Exact approved session/cutoff proof; unchanged Phase-2 G | PUBLIC_SOURCE_NOT_FOUND | No new authenticated target-date package; no durable MISSED or scored claim fabricated. |
| Phase-1 software and Phase-2 exhaustion | Starting HEAD and unchanged documents | ALREADY_SATISFIED | Not reopened. Software fixes: none; authentic evidence revealed no reproducible product failure. |
| Synthetic fixtures as evidence | None | NOT_APPLICABLE | Regression fixtures excluded from every empirical count. |
| Production/live operations | Separate explicit authorization | AUTHORIZATION_BLOCKED | No deployment, broker execution, scheduler enablement or production mutation authorized or attempted. |

## Admission, tests and safety

For all 22 successful response bodies, genuine `HistoricalRawReceipt` objects
were built from actual hashes/byte lengths/UTC receipts and known-or-null
editions. `HistoricalEvidencePackage(raw_receipt=...)` refused every one with
the exact missing fields **evidence, review, attachments**. The audit preserves
each actual receipt identity and exception. These are minimal constructor
refusals, not claimed full semantic admission runs: no fake availability,
review, session, identity, action completeness or market facts were supplied to
reach deeper gates. HTTP failures were excluded from candidate receipts.

Source qualification and integrity checks passed; empirical validation did not
run. New admitted datasets/bars, canonical paper/replay trades, OOS, holdout and
forward observations: **0**. There is no empirical sample date range. The
existing 297 unadmitted vendor daily rows (IBM/TWTR, April–November 2022) remain
unchanged and were not merged with new observations. TWTR's quarantined final
row remains quarantined. M7 performance, Stock-vs-ETF comparison, predictive
quality, profitability and verified IBKR applicability are not established.

Focused regression command and log hash are in admission-audit.json:
**257 passed in 1.07s**. The repository network-denial fixture applied. No
software/repository behavior changed, so the complete suite was not rerun.
The most recent approved unchanged full-suite result remains Phase 2's
**2744 passed in 212.31s**, with its exact attribution and prior sandbox
qualification in EVIDENCE_VALIDATION_READINESS.md. It is not represented as a
new Phase-3 run. No new tests were added for documentation/metadata changes.

All **67** retained Phase-1/2 files, **129,416,738 bytes**, were rehashed against
the closed Phase-2 inventory and match. No raw source was normalized or replaced.
No paid data, user accounts, credentials, CAPTCHA workarounds, authentication
bypass, private contracts, external messages, EODHD, OpenAI API, production,
broker actions or live-money operations were used. Existing PAPER/OBSERVATION
labels and fail-closed gates remain unchanged.

## Exhaustion and commit handoff

This is contractual exhaustion of the useful public/no-account/zero-budget
queue, not a claim to have enumerated every website. The new openly licensed
identifier sources fail historical scope, the new government evidence is a
partial cost fact, exact historical products need paid/private access, EGX
research rights remain unresolved, and every canonical candidate still needs
independent review. More current mappings, calendars, selected prices or
unreviewed documentation would not pass the next gate. No further acquisition
is justified by this queue; OOS/holdout/forward cannot proceed by filling gaps.

Remaining RIGHTS_BLOCKED items: historical Tiingo/Nasdaq halt/directory/NYSE
mapping and action-use unknowns carried forward; new EGX beta-site research
use; NYSE master dataset retention/transformation not established. Remaining
PAID_SOURCE_BLOCKED paths: TradingHours, Cboe equity/ETF history, Norgate
historical packages, EGX.news datasets; Databento paid usage is also outside
scope. Remaining AUTHORIZATION_BLOCKED requirements: independent human review,
NYSE customer access, Databento signup/API identity, ICE client access, IBKR
account/tier/applicability facts, any vendor private agreement and all live
operations. No request to buy, log in or contact providers was made.

Recommended next substantive milestone requires separately supplied exact
historical packages/rights and an independent reviewer; account-specific IBKR
facts require separate account authority. Forward validation additionally needs
future elapsed time after timely admitted collection starts. There is no
authorized software milestone to reopen.

Repository milestone paths are exactly this document,
`docs/external-evidence/acquisition-manifest.json`, and
`docs/external-evidence/admission-audit.json`. Pre-handoff whitespace/JSON/hash
checks and documentation diff review passed, including explicit checks of all
new files (ordinary git diff does not include untracked files). The first
ad hoc new-file check incorrectly expected exit 0 from `git diff --no-index`;
exit 1 signaled a new-file difference with no whitespace diagnostics. Corrected
checks passed; this was not a failed repository test. The request is written to
`COMMIT_REQUEST.json` with message
`docs: record phase three public evidence qualification`.
The supervisor processes the existing commit bridge after the Codex cycle;
Codex does not directly stage/commit or invoke its private-token guard. Next
cycle must verify the resulting HEAD, exact three committed paths, clean tree
and retained manifest hashes before issuing the final terminal report.
