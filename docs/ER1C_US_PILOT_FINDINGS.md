# ER1C US Free PIT Pilot Findings

Status: offline acquisition audit complete; canonical PIT admission remains
**NO-GO**. This report does not modify the frozen predeclaration and does not
claim strategy validation.

## Retained acquisition

Bundle: `/home/egx-agent/research-data/er1c-us-pilot/20260912T152213Z`

The audit recomputed every byte size and SHA256 recorded in both manifests.
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

## Reproduction

Run the offline audit with the authorized shared environment:

```bash
PYTHONDONTWRITEBYTECODE=1 /home/egx-agent/work/egx-trading-platform/.venv/bin/python \
  tools/audit_er1c_us_pilot.py \
  /home/egx-agent/research-data/er1c-us-pilot/20260912T152213Z
```
