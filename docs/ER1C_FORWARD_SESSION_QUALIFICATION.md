# ER1C forward session qualification

Status: bounded official-source qualification acquired at 2026-09-13T00:51:46Z.
This is not canonical US2 admission, a frozen watchlist, or an empirical result.

## Scope and retained evidence

The immutable package is stored outside the repository at
`/home/egx-agent/research-data/er1c-us-pilot/forward-session-truth-20260913T005146Z`.
It contains the official NYSE 2026 yearly trading calendar and the official NYSE
holidays and trading-hours page. The manifest records each source locator, actual
UTC receipt interval, byte size, and SHA256. Raw source bytes are not committed.
The offline auditor independently pins the reviewed manifest and both artifact
editions, so rewriting the manifest and its self-described sidecar cannot alter
receipt metadata or bless substituted calendar or hours-page bytes. The reviewed
manifest is 1,089 bytes with SHA256
`7716804d38c3a713be635cdb950831b46d9b29de92cd1eb6e11f26b0893c5239`.
The package root must be a real directory and every declared entry must be a
regular file stored directly inside it with a link count of one. Symbolic links,
substituted directories, hard links, and undeclared entries fail closed, so an
external path cannot satisfy or mutate the retained package's custody boundary.

| Artifact | Bytes | SHA256 |
| --- | ---: | --- |
| `nyse_2026_calendar.pdf` | 232670 | `70f5577eb43e60a9dbbecaae3cec23d0f02028c05c7f175013bb3e97816d394f` |
| `nyse_hours_calendars.html` | 109180 | `49ee8a651ec01ef2866e347842c0fb11309541f247d17aeaaf7ad9d6a513b1ed` |

The official page lists September 7 as the 2026 Labor Day closure and publishes
NYSE core hours as 9:30 a.m. through 4:00 p.m. Eastern Time. September 14 is
absent from the retained holiday and early-close exceptions. This supports a
current schedule observation only.

## Admission and shadow result

Canonical US2 remains **NO-GO**. The artifacts do not make an exact per-date XNYS
session assertion, publish the session clocks as exact UTC values, or carry an
approved review binding the receipts into a `HistoricalEvidencePackage`. Their
2026-09-13 receipt must not be presented as earlier availability.

The requested first US shadow date, 2026-09-14, is **NOT_READY / NOT SCORED** at
this milestone. This package contains no candidate, information cutoff, entry
condition, evidence set, or durably frozen watchlist. A later recommendation
must not be reconstructed for this cutoff. If no admissible watchlist is frozen
before the applicable decision cutoff, the session must be recorded as
`MISSED / NOT SCORED` and forward collection must begin at the next eligible
session.

Run the offline verifier with:

```bash
PYTHONDONTWRITEBYTECODE=1 /home/egx-agent/work/egx-trading-platform/.venv/bin/python \
  tools/audit_er1c_forward_session_truth.py \
  /home/egx-agent/research-data/er1c-us-pilot/forward-session-truth-20260913T005146Z
```

This qualification does not establish security identity, issue-specific status,
corporate-action completeness, liquidity, costs, candidate evidence, paper
performance, strategy validity, or live readiness. The platform remains
**EXPERIMENTAL / PAPER ONLY**.
