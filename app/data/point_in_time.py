"""Fail-closed offline market-input boundary for future engine consumers.

Uses existing immutable daily bytes and canonical semantics. Neither current
security-master aliases nor provider-adjusted close establishes historical truth.
"""
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Context, Decimal, localcontext
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from app.data.daily_canonical import CanonicalDailyBar, DailyBarSemanticClass
from app.data.daily_canonical_pipeline import DailyCanonicalPipeline
from app.data.daily_ingestion import DailyBarIngestionResult
from app.data.reference import aware
from app.storage.reference_repository import ReferenceRepository, strict_json


@dataclass(frozen=True)
class SplitAdjustedObservation:
    """Indicator-only values, never executable prices."""
    market_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    price_factor: Decimal
    volume_factor: Decimal
    event_ids: tuple[str, ...]


@dataclass(frozen=True)
class PointInTimeDailyDataset:
    rows: tuple[CanonicalDailyBar, ...]
    split_adjusted: tuple[SplitAdjustedObservation, ...]
    provenance_ids: tuple[str, ...]
    audit_id: str
    dq_status: str = "VALIDATED"


class PointInTimeDailyRepository:
    def __init__(self, references: ReferenceRepository):
        self.references = references

    def load(self, *, raw_path: str, universe_date: date, expected_market_date: date,
             as_of: datetime) -> PointInTimeDailyDataset:
        aware(as_of)
        manifest = self.references.ingestions.get_manifest_by_raw_path(raw_path)
        try:
            if manifest is None:
                raise ValueError("daily ingestion missing")
            return self._load(manifest, universe_date, expected_market_date, as_of)
        except (ValueError, TypeError, KeyError, RuntimeError, OSError, ArithmeticError) as exc:
            self.references.issue(
                manifest.ingestion_id if manifest else None, "PIT_INPUT_REJECTED",
                {"raw_path": raw_path, "universe_date": str(universe_date),
                 "expected_market_date": str(expected_market_date), "as_of": as_of.isoformat()},
            )
            raise ValueError("point-in-time input rejected: " + str(exc)) from exc

    def _load(self, manifest, universe_date, expected_market_date, as_of):
        cutoff_date = as_of.astimezone(ZoneInfo("Africa/Cairo")).date()
        if universe_date > cutoff_date or expected_market_date > universe_date:
            raise ValueError("future market date")
        if manifest.received_at > as_of:
            raise ValueError("daily evidence was not known at cutoff")
        if manifest.market_date is None or manifest.market_date > cutoff_date:
            raise ValueError("future source snapshot")
        with self.references.database.connect() as con:
            source = con.execute("SELECT * FROM data_ingestions WHERE ingestion_id=?",
                                 (str(manifest.ingestion_id),)).fetchone()
        if source["status"] not in {"RECEIVED", "VALIDATED"} or not source["source_uri"]:
            raise ValueError("daily source rejected or missing provenance")
        metadata = json.loads(source["metadata_json"])
        with self.references.database.connect() as con:
            peers = con.execute(
                "SELECT * FROM data_ingestions WHERE asset_type='DAILY_BARS' "
                "AND provider=? AND symbol=? AND market_date=? AND ingestion_id!=?",
                (manifest.provider, manifest.symbol, str(manifest.market_date),
                 str(manifest.ingestion_id)),
            ).fetchall()
        if any(datetime.fromisoformat(p["received_at"]) <= as_of
               and p["sha256"] != manifest.sha256 for p in peers):
            raise ValueError("conflicting daily source observations")
        document = strict_json(self.references.raw_store.read_verified(manifest))
        if not isinstance(document, list) or any(not isinstance(r, dict) for r in document):
            raise ValueError("daily payload must be an array of objects")
        ingestion = DailyBarIngestionResult(
            provider=manifest.provider,
            canonical_symbol=metadata["canonical_symbol"],
            provider_symbol=metadata["provider_symbol"],
            instrument_id=metadata["instrument_id"],
            snapshot_date=date.fromisoformat(metadata["snapshot_date"]),
            requested_start_date=date.fromisoformat(metadata["requested_start_date"]),
            requested_end_date=date.fromisoformat(metadata["requested_end_date"]),
            manifest=manifest, record_count=manifest.record_count,
            response_metadata=metadata["response"],
        )
        reviews = self.references.require_review(
            manifest, "egx-daily-semantic-v1", as_of=as_of,
        )
        universe_row, universe, universe_reviews = self.references.universe(
            market_date=universe_date, as_of=as_of,
        )
        members = [m for m in universe.members
                   if m.instrument_id == UUID(ingestion.instrument_id)]
        if (len(members) != 1 or not members[0].eligible
                or members[0].symbol != ingestion.canonical_symbol):
            raise ValueError("instrument is not eligible in dated universe")
        rows = DailyCanonicalPipeline(raw_store=self.references.raw_store).canonicalize_ingestion(ingestion).rows
        rows = tuple(sorted(rows, key=lambda r: r.market_date))
        dates = [r.market_date for r in rows]
        if len(set(dates)) != len(dates):
            raise ValueError("duplicate/conflicting daily observations")
        if dates[-1] > manifest.market_date:
            raise ValueError("daily observation after source snapshot")
        if dates[-1] != expected_market_date:
            raise ValueError("stale or future daily history")
        if (dates[0] < ingestion.requested_start_date
                or dates[-1] > ingestion.requested_end_date):
            raise ValueError("daily observations outside requested bounds")
        if any(r.semantic_class != DailyBarSemanticClass.VALID_EXECUTABLE for r in rows):
            raise ValueError("quarantined daily observation")
        historical_provenance = set()
        for day in sorted(set(dates) - {universe_date}):
            row, history, history_reviews = self.references.universe(market_date=day, as_of=as_of)
            members_on_day = [m for m in history.members
                              if m.instrument_id == UUID(ingestion.instrument_id)]
            if (len(members_on_day) != 1 or not members_on_day[0].eligible
                    or members_on_day[0].symbol != ingestion.canonical_symbol):
                raise ValueError("historical membership or symbol mapping unavailable")
            historical_provenance.update((row["ingestion_id"], *history_reviews))
        action_row, actions, action_reviews = self.references.actions(
            instrument_id=UUID(ingestion.instrument_id), start=dates[0],
            end=universe_date, as_of=as_of,
        )
        relevant = [a for a in actions.actions if dates[0] <= a.effective_date <= universe_date]
        if any(a.action_type != "SPLIT" for a in relevant):
            raise ValueError("unsupported corporate action requires explicit semantics")
        adjusted = []
        # Explicit precision makes output independent of ambient Decimal context.
        with localcontext(Context(prec=34)):
            for bar in rows:
                events = sorted((a for a in relevant if bar.market_date < a.effective_date),
                                key=lambda a: (a.effective_date, a.event_id))
                price_factor = Decimal(1)
                volume_factor = Decimal(1)
                for event in events:
                    price_factor *= event.old_shares / event.new_shares
                    volume_factor *= event.new_shares / event.old_shares
                if price_factor <= 0 or volume_factor <= 0:
                    raise ValueError("unrepresentable split factor")
                adjusted.append(SplitAdjustedObservation(
                    market_date=bar.market_date, open=bar.open * price_factor,
                    high=bar.high * price_factor, low=bar.low * price_factor,
                    close=bar.close * price_factor, volume=bar.volume * volume_factor,
                    price_factor=price_factor, volume_factor=volume_factor,
                    event_ids=tuple(a.event_id for a in events),
                ))
        provenance = tuple(sorted({str(manifest.ingestion_id), universe_row["ingestion_id"],
                                   action_row["ingestion_id"], *reviews,
                                   *universe_reviews, *action_reviews, *historical_provenance}))
        audit = {
            "contract": "egx-pit-daily-v1", "transformation": "split-only-v1-decimal34",
            "as_of": as_of.isoformat(), "universe_date": str(universe_date),
            "expected_market_date": str(expected_market_date),
            "provenance_ids": provenance, "raw_sha256": manifest.sha256,
            "rows": [r.model_dump(mode="json") for r in rows],
            "split_adjusted": [dict(vars(r)) for r in adjusted],
        }
        payload = json.dumps(audit, sort_keys=True, separators=(",", ":"), default=str)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        audit_id = str(uuid5(NAMESPACE_URL, "egx-pit-daily-v1:" + digest))
        with self.references.database.connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO audit_events "
                "(event_id,event_key,event_type,entity_type,entity_id,market_date,created_at,payload_json) "
                "VALUES (?,?,'PIT_DATA_VALIDATED','data_ingestion',?,?,?,?)",
                (audit_id, audit_id, str(manifest.ingestion_id), str(universe_date),
                 datetime.now(timezone.utc).isoformat(), payload),
            )
        return PointInTimeDailyDataset(rows, tuple(adjusted), provenance, audit_id)
