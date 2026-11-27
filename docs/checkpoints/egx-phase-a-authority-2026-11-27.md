# Phase A evidence and source-authority interpretation

Recorded against HEAD db32f01a7360e964fd421c728aad7b6824623ae5.
Phase A remains open. PAPER/SHADOW only; LIVE_MONEY=false.

## Evidence accepted within its scope

The supervisor's `HOST_ACTION_RESULT.md` reports PASS for
`RUN_SCHEMA_V13_REHEARSAL`. The retained report is
`/home/egx-agent/er1-autopilot/state/EVIDENCE/EGX_PHASE_A/schema_v13_rehearsal/20261127T141311Z/REHEARSAL_REPORT.json`.
It records schema 9 -> 13 on a copy, canonical identities 319 -> 319,
expected/retained backfill 319/319, missing/unexpected 0/0, no duplicate dated
universes, integrity ok, no foreign-key violations, and an idempotent migration
retry. Source hashes match; production_db_mutated and live_money are false.
This supports milestone checks 1-6 and 13-14. No repeat rehearsal is needed
for this documentation change.

Publisher retry and separate-review regressions are committed at e746ff7 and
db32f01. The latest host scanner_product PASS names pre-commit HEAD e746ff7
and working-tree fingerprint
`7139e341bc587770ed69813a11929b89dfa7f5a5bf67f7da8c67ca72455d5bb8`.
Claude approved the separate-review regression. These records support contract
behavior on synthetic fixtures, not a real source approval. A local rerun at
db32f01 passed all 34 publisher, builder and reviewed-changes tests.

## Explicit authority and membership interpretation

Current source authority: **NOT_ESTABLISHED by the evidence reconciled here**.
Current authoritative exchange membership: **NOT_ESTABLISHED**.
No review approval is issued by this document.

`publish_egx_universe` consumes an exact `egid` admission and its retained
post-commit observation. It binds snapshot date, artifact digest, observation
receipt and canonical identities, then publishes under
`egx-universe-producer-v1`. That provider and its `internal://security-master/`
URI identify an internal transformation; they do not independently establish
the upstream source's authority or the completeness of exchange membership.

The builder's `complete=True` describes its candidate document. Ordinary-share
classification gives mechanical eligibility under the scanner policy; it does
not prove active listing, suspension status, tradability or source completeness.
The snapshot date is the candidate's effective date; the retained observation
clock controls availability and must not be backdated to imply earlier knowledge.
An addition or absence between snapshots does not by itself prove a listing or
delisting. Symbol reassignment must not be interpreted as a rename. Suspension
and relisting require explicit supporting evidence.

Neither 319 migrated identities, the 224 baseline, ranking scopes, price-data
availability nor synthetic fixtures establish an authoritative population.
Current EGX universe/data-ready/scanned/status counts remain unverified.
U.S. counts remain unverified and Phase B remains gated.

## Remaining acceptance evidence

Use an isolated disposable copy and retained inputs for the next acceptance
exercise; preserve the completed schema rehearsal artifacts unchanged. Record
the exact snapshot date, admission artifact SHA-256, observation SHA-256,
published universe SHA-256, ingestion ID and receipt/completion times. Prove
that exact retry preserves retained bytes, IDs, timestamps and row counts, and
that the publisher creates no `egx-source-review-v1` rows. Demonstrate PIT
refusal before review.

Independently assess the retained upstream artifact's identity, provenance,
effective date, observation time, population coverage, exclusions, and meaning
of membership. Record evidence supporting source authority and permitted use;
public accessibility alone is insufficient. Retain that assessment with an
evidence URI and methodology. If authority or completeness cannot be supported,
record the gap and keep PIT closed; do not manufacture an approved review to
complete the exercise.

Only a separate review action may ingest `egx-source-review-v1`, binding the
exact subject ingestion ID, SHA-256, provider and contract. Record reviewer,
review time and evidence URI. Prove PIT succeeds after the valid review is
available, while a pre-review cutoff still refuses even after review receipt.
The current reader enforces binding and temporal availability; its nonempty
reviewer/methodology fields do not themselves authenticate reviewer independence
or prove source authority. Those require retained procedural evidence.

The supervisor currently exposes only RUN_SCHEMA_V13_REHEARSAL as a host
action. Do not submit another action name or shell commands through that
protocol. Continue repository-side retained-input rehearsal preparation as
needed, then use the normal review/trusted-validation/commit workflow.
Deployment and scheduler are unchanged; no operational DB mutation is authorized
by this checkpoint. No Phase B work is included.

## Publisher-input preflight at ef79413

Local read-only inspection of the retained schema rehearsal copy is recorded in
`egx-phase-a-publisher-input-preflight-2026-11-27.json`. Its SHA-256 before and
after inspection matches the host report's post-upgrade copy hash. The copy has
319 canonical identities but **zero admissions and zero observations**. Its
three reference artifacts comprise one actions document and two existing source
reviews; there is no universe artifact. Those existing reviews are not evidence
of a publisher-created review or of approval for a new universe.

Consequently this copy cannot supply the exact publisher prerequisites. Do not
fabricate historical admissions or observation times from migrated identities.
Next prepare an isolated admission from an exact retained upstream artifact and
manifest, checking date/provenance suitability first; any new post-commit
observation must use its actual clock. Then exercise retry and pre-review PIT
refusal. Source authority must still be assessed independently before approving
a separate review. No additional host action name is authorized.

At ef79413 the 34 focused publisher/builder/reviewed-changes tests passed locally
in 2.39s. The latest trusted scanner_product PASS names db32f01 with fingerprint
`b873873632305f0f8c0af6b740a9bbe2d4094eab2143cd6377cce65bfd51ef1d`.
This preflight is local evidence awaiting review, not trusted publisher acceptance.
No code, operational database, deployment or scheduler was changed.
