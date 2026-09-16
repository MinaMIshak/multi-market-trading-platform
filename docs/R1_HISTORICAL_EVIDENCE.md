# R1.1 offline historical evidence contracts

Engineering contracts only. No real data acquired, no market evidence produced,
no strategy validation, no profitability claim, and no live-money readiness.
Model validation does not make historical data “verified”. All test inputs are
engineering fixtures.

## Public boundary

`app.research.historical_evidence` exposes these strict, frozen contracts, each
with its own literal `schema_version` ending in `-v1`:

- `HistoricalRawReceipt`: provider/source, locator metadata, SHA256 of exact
  original bytes, positive byte size, actual receipt, category and source edition.
  An unknown edition is representable with explicit `None`, but is inadmissible
  in a package. Provider names confer no authority.
- `HistoricalEvidenceAttachment`: locator, attachment SHA256, actual receipt,
  description and explicit source authority context.
- `HistoricalAttachmentReference`: deterministic attachment identity plus hash.
- `HistoricalAvailability`: `EXACT` instant or `BOUNDED_INTERVAL` inclusive bounds.
- `HistoricalAvailabilityEvidence`: exact receipt identity/hash/edition, explicit
  record/value selector (`covered_scope`), covered fields, revision semantics,
  availability and supporting attachment references.
- `HistoricalSourceReview`: reviewer, actual review time, methodology, explicit
  approval boolean, exact subject identity/hash, evidence identity and attachments.
- `HistoricalEvidencePackage`: one raw receipt, one availability evidence claim,
  one review and the exact attachment set. Its `identity` binds all these contents.

`require_historical_evidence(package, *, decision_at, research_built_at)` returns
an equal reconstructed canonical package after admission. It requires exact
canonical model types recursively, including tuple members, and reconstructs
nested contracts to detect unchecked copies, construction and corruption.
Mappings are not substitutes for models. Invalid inputs fail closed. The helper
never mutates inputs, fetches locators, reads bytes, writes storage, or calls a
provider. It has no current-clock, random or environment dependency.

## Five separate clocks

All timestamps must be explicit Python `datetime` objects with `timezone.utc`,
matching the M8 UTC policy. Strings, naive datetimes and other timezone objects
are rejected; callers must explicitly normalize before constructing contracts.

- `local_received_at`: actual local acquisition time of raw bytes or attachment.
- `reviewed_at`: actual completion of retrospective source review.
- Historical availability: supported time or interval when the **exact consumed
  value/version** became available at the source.
- `decision_at`: simulated historical decision cutoff.
- `research_built_at`: actual assembly/validation timestamp supplied by the caller.

Raw receipt, every attachment receipt and review must be at or before research
build. Local receipt and review may be later than the historical decision. No
clock substitutes for another, and this module cannot establish whether supplied
actual timestamps are truthful. It never backdates receipts or invents publication
times. Historical row dates alone are not availability evidence.

EXACT passes only when `exact_at <= decision_at`. BOUNDED_INTERVAL requires
`start <= end` and passes only when `end <= decision_at`. An interval crossing the
cutoff fails; equality passes. A zero-width interval remains an interval. Missing
or unknown availability fails. Alternative proof fields cannot coexist.

## Identity, review and corrections

Every derived `identity` is lowercase SHA256 of compact, sorted-key, ASCII-escaped
UTF-8 JSON from the canonical model content, including all nested schema versions.
Identities are properties, not caller-supplied claims. There is no random identity.
Covered fields, attachment references and package attachments are unordered sets
represented as sorted immutable tuples. Duplicate fields/attachment identities
are rejected. Ordering does not affect identity; meaningful content changes do.
Source locators are metadata bound into identity, never identity's sole basis.

Review and evidence must reference exactly the package attachment identities and
hashes: missing, extra, mismatched or duplicate references fail. A package holds
one declared scope and one proof; it does not select between competing proofs.
Scope selectors and revision descriptions are human-reviewed text, not an
executable selector language. Cross-package conflicts are not adjudicated here.

This is a trusted human/source review and attestation contract. It is **not proof
that the reviewer was truthful**, nor cryptographic proof of source authenticity.
SHA256 binds declared content; this metadata-only module cannot check original
bytes, source authority or the substance of attachments. Those are review duties.

Corrected bytes must have a new hash and source edition/version, with new evidence
and review binding that exact subject. Old proof references cannot be transferred
to a new subject. Old artifacts remain immutable. R1.1 does not maintain a lineage
registry or compare independent packages to police edition naming; revision
semantics must explicitly describe the exact edition under review.

## Operational isolation and deferred work

Operational `PointInTimeDailyRepository` and reference source-review behavior are
unchanged. Late receipt remains late for M3; retrospective review cannot rewrite
operational history. There is no fallback from missing operational evidence to
these packages, and a package is not an operational reference or daily input.
Existing M3 tests retain late-receipt and late-review rejection coverage.

No DB/schema change, acquisition, provider wiring, current-universe projection,
historical universe reconstruction, strategy/execution data or planning is added
by R1.1 itself. R1.2 retrospective PIT derivation is a separate, subsequently
implemented research boundary documented in `R1_RETROSPECTIVE_PIT.md`; R1.1
does not perform that derivation. M4–M8 behavior, holdout work and parameter
selection remain outside the R1.1 evidence-admission boundary.
