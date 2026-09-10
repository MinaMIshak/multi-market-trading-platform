"""Append-only reference catalog using the existing raw and ingestion layers."""
import hashlib
import json
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from app.data.ingestion_repository import DataIngestionRepository
from app.data.models import DataAssetType, RawArtifactManifest
from app.data.raw_store import ImmutableRawStore
from app.data.reference import ActionEvidence, UniverseEvidence, ValidationEvidence, aware
from app.storage.database import Database


CONTRACTS = {
    "egx-universe-v1": (UniverseEvidence, DataAssetType.SECURITY_MASTER),
    "egx-actions-v1": (ActionEvidence, DataAssetType.CORPORATE_ACTIONS),
    "egx-source-review-v1": (ValidationEvidence, DataAssetType.SECURITY_MASTER),
}


def strict_json(payload):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result
    return json.loads(payload, object_pairs_hook=unique)


class ReferenceRepository:
    def __init__(self, database: Database, raw_store: ImmutableRawStore):
        self.database = database
        self.raw_store = raw_store
        self.ingestions = DataIngestionRepository(database)

    def issue(self, ingestion_id, code, context):
        """One durable issue per evidence/context; retries do not multiply issues."""
        payload = json.dumps(context, sort_keys=True, separators=(",", ":"))
        identifier = str(uuid5(NAMESPACE_URL, f"m3:{ingestion_id}:{code}:{payload}"))
        with self.database.connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO data_quality_issues "
                "(issue_id,ingestion_id,severity,code,message,payload_json,created_at) "
                "VALUES (?,?,'ERROR',?,?,?,?)",
                (identifier, str(ingestion_id) if ingestion_id else None, code,
                 code, payload, datetime.now(timezone.utc).isoformat()),
            )

    def ingest(self, *, provider: str, payload: bytes, source_uri: str,
               contract: str) -> RawArtifactManifest:
        if contract not in CONTRACTS or not source_uri.strip():
            raise ValueError("explicit supported contract and source URI required")
        model, asset = CONTRACTS[contract]
        digest = hashlib.sha256(payload).hexdigest()
        manifest = self.raw_store.store_bytes(
            provider=provider, asset_type=asset, payload=payload,
            filename=f"{contract}-{digest}.json",
        )
        manifest = self.ingestions.save_manifest(
            manifest, source_uri=source_uri, metadata={"contract": contract},
        )
        try:
            document = model.model_validate(strict_json(self.raw_store.read_verified(manifest)))
            if document.contract != contract:
                raise ValueError("contract mismatch")
            published = (document.reviewed_at if isinstance(document, ValidationEvidence)
                         else document.published_at)
            if published > manifest.received_at:
                raise ValueError("evidence publication is after receipt")
        except (ValueError, TypeError) as exc:
            with self.database.connect() as con:
                con.execute("UPDATE data_ingestions SET status='REJECTED' WHERE ingestion_id=?",
                            (str(manifest.ingestion_id),))
            self.issue(manifest.ingestion_id, "REFERENCE_INVALID", {"contract": contract})
            raise ValueError("invalid reference evidence") from exc
        with self.database.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "INSERT OR IGNORE INTO reference_artifacts "
                "(ingestion_id,contract,effective_date,instrument_id,coverage_start,coverage_end,subject_ingestion_id) "
                "VALUES (?,?,?,?,?,?,?)",
                (str(manifest.ingestion_id), contract,
                 str(document.effective_date) if isinstance(document, UniverseEvidence) else None,
                 str(document.instrument_id) if isinstance(document, ActionEvidence) else None,
                 str(document.coverage_start) if isinstance(document, ActionEvidence) else None,
                 str(document.coverage_end) if isinstance(document, ActionEvidence) else None,
                 str(document.subject_ingestion_id) if isinstance(document, ValidationEvidence) else None),
            )
            # Structural validation is distinct from source review / promotion.
            con.execute("UPDATE data_ingestions SET status='VALIDATED', "
                        "completed_at=COALESCE(completed_at,?) WHERE ingestion_id=? AND status!='REJECTED'",
                        (datetime.now(timezone.utc).isoformat(), str(manifest.ingestion_id)))
        return manifest

    def _load(self, row):
        manifest = self.ingestions.get_manifest_by_raw_path(row["raw_path"])
        if manifest is None:
            raise ValueError("missing reference manifest")
        model, asset = CONTRACTS[row["contract"]]
        if (manifest.asset_type != asset or not row["source_uri"]
                or json.loads(row["metadata_json"]) != {"contract": row["contract"]}):
            raise ValueError("reference provenance mismatch")
        return model.model_validate(strict_json(self.raw_store.read_verified(manifest)))

    def records(self, contract, *, as_of, market_date=None, instrument_id=None,
                start=None, end=None, subject_ingestion_id=None):
        aware(as_of)
        clauses = ["r.contract=?"]
        parameters = [contract]
        if market_date is not None:
            clauses.append("r.effective_date=?")
            parameters.append(str(market_date))
        if instrument_id is not None:
            clauses.extend(("r.instrument_id=?", "r.coverage_start<=?", "r.coverage_end>=?"))
            parameters.extend((str(instrument_id), str(end), str(start)))
        if subject_ingestion_id is not None:
            clauses.append("r.subject_ingestion_id=?")
            parameters.append(str(subject_ingestion_id))
        with self.database.connect() as con:
            rows = con.execute(
                "SELECT r.contract,i.* FROM reference_artifacts r "
                "JOIN data_ingestions i USING(ingestion_id) WHERE " + " AND ".join(clauses)
                + " ORDER BY i.ingestion_id", parameters,
            ).fetchall()
        result = []
        for row in rows:
            if datetime.fromisoformat(row["received_at"]) > as_of:
                continue
            if row["status"] != "VALIDATED":
                raise ValueError("reference ingestion is not validated")
            if not row["completed_at"] or datetime.fromisoformat(row["completed_at"]) > as_of:
                continue
            try:
                document = self._load(row)
            except (ValueError, RuntimeError, OSError) as exc:
                self.issue(row["ingestion_id"], "REFERENCE_INTEGRITY", {"contract": contract})
                raise ValueError("reference integrity failure") from exc
            published = (document.reviewed_at if isinstance(document, ValidationEvidence)
                         else document.published_at)
            if published > as_of:
                continue
            result.append((row, document))
        return result

    def require_review(self, manifest, contract, *, as_of):
        reviews = []
        for row, review in self.records(
                "egx-source-review-v1", as_of=as_of, subject_ingestion_id=manifest.ingestion_id):
            if (review.subject_ingestion_id == manifest.ingestion_id
                    and review.subject_sha256 == manifest.sha256
                    and review.subject_provider == manifest.provider
                    and review.subject_contract == contract):
                reviews.append(row["ingestion_id"])
        if not reviews:
            self.issue(manifest.ingestion_id, "SOURCE_UNVALIDATED", {"contract": contract})
            raise ValueError("source validation evidence unavailable")
        return tuple(sorted(reviews))

    def universe(self, *, market_date, as_of):
        aware(as_of)
        if market_date > as_of.astimezone(ZoneInfo("Africa/Cairo")).date():
            raise ValueError("future universe date")
        candidates = []
        for row, doc in self.records("egx-universe-v1", as_of=as_of, market_date=market_date):
            if doc.effective_date == market_date:
                manifest = self.ingestions.get_manifest_by_raw_path(row["raw_path"])
                reviews = self.require_review(manifest, doc.contract, as_of=as_of)
                candidates.append((row, doc, reviews))
        # Never choose latest, merge sources, or project today's members backwards.
        if len(candidates) != 1:
            self.issue(None, "UNIVERSE_MISSING_OR_AMBIGUOUS",
                       {"market_date": market_date.isoformat(), "as_of": as_of.isoformat()})
            raise ValueError("exactly one dated universe required")
        return candidates[0]

    def actions(self, *, instrument_id, start, end, as_of):
        aware(as_of)
        if start > end or end > as_of.astimezone(ZoneInfo("Africa/Cairo")).date():
            raise ValueError("invalid or future action query interval")
        candidates = []
        for row, doc in self.records(
                "egx-actions-v1", as_of=as_of, instrument_id=instrument_id, start=start, end=end):
            if doc.instrument_id != instrument_id:
                continue
            # Even partial overlapping evidence makes selection ambiguous.
            if doc.coverage_start <= end and doc.coverage_end >= start:
                manifest = self.ingestions.get_manifest_by_raw_path(row["raw_path"])
                reviews = self.require_review(manifest, doc.contract, as_of=as_of)
                candidates.append((row, doc, reviews))
        if (len(candidates) != 1 or candidates[0][1].coverage_start > start
                or candidates[0][1].coverage_end < end):
            self.issue(None, "ACTIONS_MISSING_OR_AMBIGUOUS",
                       {"instrument_id": str(instrument_id), "start": str(start), "end": str(end),
                        "as_of": as_of.isoformat()})
            raise ValueError("complete unambiguous action coverage required")
        return candidates[0]
