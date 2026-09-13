# ER1C US Free PIT Pilot Findings

Status: offline acquisition audit complete; canonical PIT admission remains
**NO-GO**. This report does not modify the frozen predeclaration and does not
claim strategy validation.

## Retained acquisition

Bundle: `/home/egx-agent/research-data/er1c-us-pilot/20260912T152213Z`

The audit verifies both retained manifest SHA256 sidecars, verifies the current
frozen predeclaration bytes against the acquisition manifest, and recomputes
every artifact byte size and SHA256 recorded in both manifests.
It also validates the exact manifest schemas, safe local filenames, unique
request and evidence identities, successful retrieval status, ordered UTC
receipt clocks, explicit boolean availability claims, and Tiingo endpoint
identity. Manifest metadata is therefore checked before it controls any local
file read or market-row audit.
IBM contains 151 ordered daily rows from 2022-04-01 through 2022-11-04. TWTR
contains 146 ordered rows from 2022-04-01 through 2022-10-28. All raw OHLC
values are positive and internally coherent, volume is nonnegative, and dates
are unique, ordered, and within the requested bounds.

| Artifact | Bytes | SHA256 |
| --- | ---: | --- |
| IBM raw response | 39,306 | `7d943f73d068ab832c3eacf974e6224b78bdc3782bce7d4d2a491483b5332bd5` |
| TWTR raw response | 32,451 | `da33bb8bbf1ec381aa829176730762619a33a2e996b5a39bbaf2aa22a546ddd5` |
| Tiingo manifest | 1,841 | `8936272e0fcd855eed1e97d6a0361f4929211630e417e75ccbc2452e0885acd9` |
| IBM SEC submissions response | 166,091 | `0d698f754c7aa7f9373bc90b65d73d801e018ad176b6ffb5e4ec0f6d18fa984f` |
| IBM 2022 Q2 Form 10-Q | 6,700,677 | `3d2b1bed1ccf656b4aa7d158ee6af27c0147923e5f3b61238ee59ebfab0d68ec` |
| Public evidence manifest | 2,618 | `9f732e617469daf1b81333830057dcb78fcee92e03321cfba2672b755cf88dd3` |

The Tiingo rows mark IBM cash dividends of USD 1.65 on 2022-05-09 and
2022-08-09. These are vendor markers, not proof of complete bounded corporate
action coverage. Tiingo supplies no TWTR merger or delisting marker in this
response.

## TWTR conflict and classification

Tiingo's final TWTR row is dated 2022-10-28 with open, high, low, and close all
USD 53.70 and volume zero. The retained Twitter 8-K says the merger completed
on 2022-10-27, each eligible share converted to the right to receive USD 54.20,
and NYSE trading was suspended before the 2022-10-28 open. The retained NYSE
removal notice filed with the SEC independently states the same merger date,
consideration, and pre-open suspension. It identifies 2022-11-08 as the formal
removal from listing and registration.

Issue-specific suspension evidence overrides the vendor row. The 2022-10-28
row is therefore quarantined and cannot be an executable daily bar. The last
currently supportable TWTR executable bar is 2022-10-27. Suspension on
2022-10-28 and formal removal on 2022-11-08 are separate facts. No missing or
zero-volume row was interpreted as an exchange closure, suspension, or
delisting by itself.

The offline audit verifies the semantic scope of both retained SEC-hosted
artifacts before reporting these facts. Twitter's Form 8-K binds CIK
`0001418091`, common stock symbol `TWTR`, and the New York Stock Exchange. The
NYSE removal notice independently corroborates the merger date, USD 54.20
per-share cash terms, and pre-open suspension, and states the formal removal
date.

This proves one issue-specific event from the retained artifacts. It does not
prove that every corporate action for TWTR, or either pilot instrument, is
covered throughout 2022-04-01 through 2022-11-04. Both records declare
historical availability unproven, their local receipt occurred in 2026, and no
approved review binding exists. The audit therefore reports
`canonical_us4_action_coverage: NO_GO`; complete bounded corporate-action
coverage remains an admission blocker.

## Admission decision

The acquired price bytes pass this bounded offline integrity audit, but an
`USRetrospectivePITDailyDataset` cannot be admitted. Each public evidence
record explicitly says historical availability is unproven, and all bytes were
received in 2026. Current receipt time is not historical availability.
Approved review bindings are absent. Complete exact-date XNYS universe
snapshots, exact-date identity throughout the interval, every-calendar-date
session records, and complete bounded corporate-action coverage are also
absent. Tiingo's daily action fields cannot establish explicit empty coverage
or non-price events.

Unknown mandatory truth is a failed gate. No strategy, signal, return,
walk-forward, holdout, stress, execution, or paper-readiness test may consume
this incomplete package. The next defensible acquisition step is to seek free,
dated authoritative evidence for those missing boundaries without redownloading
the unchanged retained artifacts.

## Identity scope qualification

The audit now enforces that the acquisition request set exactly matches the
frozen `IBM` and `TWTR` cohort. This prevents a later manifest or bundle from
silently substituting, adding, or dropping a symbol while still claiming to be
the predeclared pilot.

The retained Twitter 8-K corroborates CIK `0001418091`, the `TWTR` symbol,
common stock, and New York Stock Exchange representation at that filing. The
retained SEC submissions response corroborates IBM CIK `0000051143`, issuer
name, and the response's current `IBM`/`NYSE` metadata, and contains 2022
periodic-filing entries. It also binds the 2022-07-25 Form 10-Q accession and
primary-document name to the retained archive document. That document states
IBM's capital stock symbol and New York Stock Exchange registration for the
quarter ended 2022-06-30. The audit validates these anchors and the exact SEC
archive locator before reporting them.

The IBM response and filing were received on 2026-09-12 and explicitly have
historical availability unproven. The filing is useful dated corroboration, but
one quarterly filing does not prove listing identity on every pilot session.
SEC CIK identifies the issuer rather than a listing or instrument. Likewise,
the Twitter artifact supplies only a filing-level identity fact.
Provider request symbols are not stable instrument IDs, the exchange name does
not by itself prove the canonical `XNYS` MIC mapping, and no artifact establishes
exact-date identity for every required session. The audit therefore still
reports `canonical_us1_identity_evidence: NO_GO` and does not construct a
`USListingIdentity`.

An earlier free direct capture of an official IBM 2022 SEC filing page was
denied with HTTP 403. No bytes from that attempt were retained or represented
as evidence. Later cycles made one successful request to SEC's free
`data.sec.gov` submissions endpoint and one successful request for the exact
archive document. Both original responses have source locators, receipt clocks,
sizes, SHA256 values, and explicit historical-availability status in the
evidence manifest.

## NYSE calendar scope qualification

The retained official NYSE PDF is a one-page `2022 TRADING CALENDAR`, created
on 2021-12-13. Its legend identifies exchange holidays as market closed and
identifies an early market close as 1 p.m. Eastern. The document also states
that its dates were correct as of 2021-12-13 but subject to change.

This artifact is useful official corroboration for the marked calendar events,
but it is not a complete US2 package. It does not bind each calendar date to
the declared XNYS MIC, state the regular session open, or supply exact UTC open
and close clocks for each open date. Its own change disclaimer also prevents
the retained edition from proving that no later revision applied. The audit
therefore reports `canonical_us2_session_evidence: NO_GO` and continues to list
every-calendar-date evidenced XNYS session records as an admission blocker.

## Reproduction

Run the offline audit with the authorized shared environment:

```bash
PYTHONDONTWRITEBYTECODE=1 /home/egx-agent/work/egx-trading-platform/.venv/bin/python \
  tools/audit_er1c_us_pilot.py \
  /home/egx-agent/research-data/er1c-us-pilot/20260912T152213Z
```

## Raw numeric integrity boundary

The offline audit rejects non-object rows and non-finite or nonnumeric raw
OHLCV and action markers, including booleans. Raw prices and split factors
must be positive; volume and cash-dividend markers must be nonnegative.
Positive fractional split factors remain valid vendor markers. Invalid values
are rejected without repairing the original bytes or deriving market state.
These checks close a comparison gap where NaN could bypass positivity and
OHLC consistency checks. They do not establish complete action coverage.

The retained authentic bundle passes the tightened audit offline. Canonical
PIT admission remains NO_GO with the same mandatory evidence gaps. The new
malformed-value test cases are software fixtures, not empirical observations.

## IBM dividend announcement scope

Two official IBM newsroom pages are now retained as original HTML with receipt
times and hashes. The April 26 notice reports a USD 1.65 dividend, June 10
payable date, and May 10 record date. The July 25 notice reports the same amount,
September 10 payable date, and August 10 record date. The retained Q2 10-Q
independently reports the July announcement and terms. The audit binds each
statement to its exact source locator and original-byte SHA256.

These amounts and sequences agree with Tiingo's May 9 and August 9 dividend
markers, but none of the issuer artifacts states an ex-date. Record and payable
dates cannot be substituted for effective ex-dates, and a payable date is not
proof that payment occurred. Current 2026 receipt of pages dated in 2022 also
does not prove their historical availability. No receipt is backdated.

IBM's official cash-dividend history is also retained. It identifies dividends
429 and 430 as actual USD 1.65 per-share payments, with the same record and
payable dates. This resolves actual payment for those two distributions only;
the page still provides no ex-date. A separate official IBM stock-history page
states that IBM's last stock split occurred in 1999 and its last stock-dividend
distribution occurred in 1967. That is issuer-level negative evidence for those
two action categories during the pilot interval.

The current pages do not prove their content was available at a historical
decision cutoff. Cash-dividend and split histories also cannot establish that
mergers, spinoffs, rights, symbol changes, delistings, or other action categories
are empty. Canonical US4 and overall PIT admission therefore remain **NO-GO**.
The next action probe should seek free official ex-date evidence and complete
bounded coverage across every mandatory action type.

Verification: 94 focused audit tests passed; the authentic retained bundle audit
completed successfully with nine verified public artifacts and PIT NO_GO. Two
additional free official IBM pages were acquired; no paid or limited API was
called, and the frozen declaration was unchanged.

## Payment-table date-role integrity

The IBM cash-payment qualifier now binds each target distribution to four
separate cells under its own table's exact dividend-number, actual-amount,
payable-date and record-date headings. Flattened text alone could previously
pass even if headings were swapped or a conflicting target row coexisted with
the expected row. Missing or swapped headings, duplicate/conflicting target
rows, detached rows and rows in a separate headerless table now fail closed.
The extractor is intentionally scoped to the retained IBM HTML table edition;
changed layouts require requalification rather than inferred column meanings.

Verification: 100 focused tests passed and the retained nine-artifact audit
completed offline with PIT NO_GO. An initial test run found a fragment/whole-HTML
validation mismatch (93 passed, one failed); that implementation defect was
corrected before the final passing run. No raw artifact or frozen declaration
changed. Two web discovery queries for official IBM May/August 2022 ex-date
statements did not establish new authoritative evidence; no search result was
admitted and no source was redownloaded. Ex-dates, full action coverage and all
other mandatory PIT gaps remain unresolved.

## Closed acquisition inventory

The offline audit now requires the acquisition root and public-evidence
directory to contain exactly the files declared by their manifests, plus the
expected manifest sidecars and evidence directory. Missing files, undeclared
artifacts, substituted directories and symbolic links fail closed. This closes
an audit ambiguity where valid declared hashes could coexist with untracked raw
bytes that were outside the reported inventory. It does not authenticate source
authority or relax any PIT gate.

A new official-source search for the IBM May 9 and August 9, 2022 ex-dates found
only the already retained issuer record/payable-date history; search results
that state ex-dates were secondary and were not acquired or admitted. The next
evidence probe should prioritize an official historical XNYS roster or
listing-change archive, while complete bounded actions remain unresolved.

## Official NYSE historical-roster probe

An official NYSE FTP probe retained the current symbol-mapping directory index
and daily short-sale volume files for 2022-07-01, 2022-10-27 and 2022-10-28.
The original bytes, locators, actual 2026 receipt times, HTTP metadata, sizes and
SHA256 values are recorded under
`/home/egx-agent/research-data/er1c-us-pilot/nyse-roster-probe-20260912`.
The current directory index exposed no symbol-mapping edition for any of those
pilot dates; direct requests for 2022-07-01 and 2022-10-27 editions returned
HTTP 404 and no response bytes were admitted as evidence.

The official reports contain NYSE-market (`N`) rows for IBM and TWTR on July 1
and October 27. On October 28 they contain IBM but no TWTR row. These are
bounded trading-report observations only. The report is a daily short-sale
volume report, not a complete listing roster, and an absent row cannot prove an
exchange closure, suspension, delisting, or universe ineligibility. TWTR's
October 28 suspension remains supported by the previously retained
issue-specific SEC-hosted NYSE notice, not inferred from this absence.

The probe therefore does not establish complete exact-date XNYS membership,
removed-name completeness, stable listing identity, or historical availability
at a decision cutoff. Canonical US5A and overall PIT admission remain
**NO-GO**. The bounded files may serve as independent activity corroboration,
but they must not be promoted into universe snapshots or execution volume.

## Official SEC listing-ledger probe

Official SEC guidance and the complete Q2, Q3 and Q4 2022 EDGAR form indexes
were retained under
`/home/egx-agent/research-data/er1c-us-pilot/sec-listing-ledger-probe-20260912`.
The indexes enumerate 344, 416 and 780 Form 25-NSE rows respectively, and 225,
226 and 205 Form 8-A12B rows (plus 13, 34 and 26 amendments). The Q4 index
contains Twitter CIK `1418091` as a Form 25-NSE filed on 2022-10-28, binding the
already retained removal submission to the complete quarterly filing index.
The Q2 index also contains a Twitter Form 8-A12B filed on 2022-04-18; its form
type alone does not identify the registered security class.

This establishes a reproducible SEC filing-discovery boundary, not a listing
ledger. SEC states that exchange delistings have been filed through EDGAR on
Form 25-NSE since April 24, 2006. A Form 25-NSE records removal from listing and
registration; it does not enumerate all listed securities or establish the
last trading session. Form 8-A12B records Section 12(b) registration but does
not by itself establish initial trading, common-stock eligibility, or that the
venue was XNYS. The form index also omits exchange and security-class fields,
so each candidate filing needs submission-level inspection. SEC separately
states that its periodically updated ticker/exchange association files have no
guaranteed accuracy or scope, preventing their use as a historical roster.

The retained quarterly indexes are authentic complete index editions within
their stated dissemination scope, but their current 2026 receipt does not prove
historical pre-decision availability. They cannot reconstruct the opening
roster, daily changes, ticker reuse, transfers, trading suspensions, or every
session's eligibility. A mechanically complete XNYS additions/removals ledger,
canonical US5A, and overall PIT admission therefore remain **NO-GO**. The
offline auditor preserves this classification and fails on altered editions,
bytes, inventory, availability claims, or the known Twitter index row.

## Twitter SEC submission probe

The two indexed Twitter submissions were retained under
`/home/egx-agent/research-data/er1c-us-pilot/sec-twitter-submission-probe-20260912T191134Z`.
The April 18 Form 8-A12B registers **Preferred Stock Purchase Rights** on the
New York Stock Exchange. It is not a common-stock listing event and cannot
establish the first eligible date for Twitter common stock.

The October 28 Form 25-NSE identifies Twitter CIK `0001418091`, common stock,
and New York Stock Exchange LLC. Its exhibit distinguishes the October 27
merger effective date, suspension before the October 28 market open, and
removal from listing and registration at the November 8 opening. This supports
the issue-specific rejection of Tiingo's zero-volume October 28 row as an
executable bar. It does not establish a complete listing-change ledger or
exact-date universe, and its current receipt does not prove historical
pre-decision availability. Canonical US5A and overall PIT admission remain
**NO-GO**.

The submission auditor now binds each artifact to its exact retained SEC
accession URL and requires a valid explicit UTC receipt no later than audit
time, plus the acquired plain-text content type. Rehashed manifests with wrong
sources, malformed/naive/future receipts, or unexpected media types fail closed.
These metadata checks do not authenticate a claimed retrieval independently or
prove historical availability; the original bytes and independent source review
remain necessary. The unchanged authentic probe passes; 23 focused and
listing-index dependency tests pass. No new acquisition was performed.

## Return-independent SEC form sample declaration

A bounded six-record submission sample was frozen under
`/home/egx-agent/research-data/er1c-us-pilot/sec-form-sample-20260912T194500Z`
before inspecting any selected submission content. For each retained Q2, Q3 and
Q4 2022 form index and each unamended Form 8-A12B and Form 25-NSE group, the
selection takes the lexicographically smallest SHA256 of the fixed seed,
a NUL separator and the indexed submission locator. This makes selection
reproducible from exact retained index bytes and independent of issuer returns,
security class, exchange, or filing contents. The declaration SHA256 is
`cbe07c48d823755350f31ea0ac4384386451727f1919701567ff409375820a63`.

The offline declaration auditor recomputes all six choices, index hashes, line
numbers, accession URLs and output names. It rejects a changed index edition,
selection seed, record, locator or sidecar. This verifies the selection process;
it does not authenticate submission bytes or establish that the sample is
representative of every filing.

Two attempts to retrieve the first selected submission from its accession URL
in the official SEC archive, using Python urllib and curl with descriptive user
agents, each received HTTP 403. A later bounded probe used the distinct direct
submission locator published in the retained SEC quarterly index and also
received HTTP 403. Its immutable attempt record has SHA256
`6dd754aae8e489947e65a16ddf07c441f2892589c07dde1b90fdb121b1048d74`.
No rejection body was retained as evidence and the remaining five records were
not requested.

The direct-attempt auditor binds that negative result to the frozen declaration,
the first deterministic record, its exact index locator, explicit UTC request
bounds, HTTP status and response-byte disposition. It rejects sample or locator
substitution and does not treat HTTP rejection as source evidence. Consequently,
venue/security-class extraction has not been tested on this sample. Historical
XNYS universe completeness, listing-ledger completeness, canonical US5A and
overall PIT admission remain **NO-GO**. The failed retrieval is an acquisition
constraint, not evidence about the filing or historical availability.

## Forward-session audit claim correction

The offline forward-session auditor checks general NYSE holiday-scope, Labor
Day and core-hours text anchors and basic PDF structure. Those checks do not
parse a complete exceptions calendar or prove the session state of September
14, 2026. Its earlier target-date absence statement was unsupported and has
been removed. The report now identifies only text-anchor presence and returns
`target_date_session_status: UNKNOWN`. Even a page containing an additional
target-date closure or early-close notice cannot produce a regular-session
claim from these checks.

The package also contains no watchlist or ledger evidence, so whether a watchlist
was frozen by its cutoff is `UNKNOWN`, rather than a global negative assertion.
Canonical US2 remains **NO_GO** and shadow scoring remains **NOT_READY**.
This correction neither changes retained source bytes nor admits a session,
marks one missed, or creates a recommendation. EXPERIMENTAL / PAPER ONLY.

## Twitter submission identity integrity

The offline Twitter submission auditor now binds both retained SEC artifacts to
their submission headers, including accession number, acceptance timestamp,
submission type, public-document count, Twitter CIK and SEC file number. A
rehashed payload assembled from the previously checked event phrases can no
longer satisfy the audit without the exact retained filing identity.

This strengthens artifact identity only. It does not prove historical
pre-decision availability, complete Twitter action coverage, a complete XNYS
listing-change ledger or historical-universe completeness. The issue-specific
merger, suspension and removal facts retain their existing bounded scope, and
canonical US4 and PIT admission remain **NO-GO**.

The submission audit additionally binds semantic facts to the exact SEC document
envelopes present in each retained submission. The rights classification must be
inside the sole sequence-1 `8-A12B` document. The removal filing must contain the
sequence-1 `25-NSE` primary XML and sequence-2 `EX-99.25` notice with their exact
filenames; issuer, class, exchange and effective-date facts must occur in the
primary document, while merger, suspension and removal language must occur in the
notice. Rehashing a bundle after moving a phrase outside that notice or relabeling
the notice cannot satisfy the audit. This strengthens artifact attribution only
and does not expand the filing's issue-specific scope.

The auditor also binds the manifest to its exact root fields and bounded declared
purpose. A rehashed manifest cannot add an apparent completeness flag or rewrite
the probe as complete corporate-action coverage. This is scope-integrity
hardening only; the retained bytes and all **NO-GO** decisions are unchanged.

## Nasdaq halt-source qualification

Retained three official documentation/terms artifacts and qualified the
[Nasdaq halt-feed scope](ER1C_NASDAQ_HALT_QUALIFICATION.md). The documented
current-day query can omit older unresolved halts: absence cannot clear a
suspension. No feed data was fetched or admitted. Historical coverage, exact-date
identity, PIT availability, retention/transformation scope and canonical status
remain unresolved. This is a free-source qualification milestone, with no
empirical performance or readiness claim.

The linked official field definitions were subsequently retained and audited.
They distinguish initial halt clocks from scheduled quotation and trade
resumption clocks, and the current-halts reference labels displayed halt times
as Eastern Time. Scheduled resumption is not proof of actual execution. The
market category only says `NASDAQ` or `Non-NASDAQ`, so it cannot establish XNYS
identity. No feed observation was acquired; all canonical status and execution
gates remain **NO-GO**.
