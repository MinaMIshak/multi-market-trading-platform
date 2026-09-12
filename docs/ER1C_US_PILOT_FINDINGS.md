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
