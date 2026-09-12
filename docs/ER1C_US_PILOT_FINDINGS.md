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
| Public evidence manifest | 1,624 | `bba60b448873fbe010c6d477c70e41eb3d0387b964152bc87148cbbe3229fa4f` |

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
