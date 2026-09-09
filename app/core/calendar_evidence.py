from __future__ import annotations

from datetime import date

from app.core.calendar_verification import (
    OfficialIndexEvidence,
)


class OfficialIndexEvidenceRepository:
    PROVIDER = "egx_official_public"
    REQUIRED = (
        "CASE30",
        "EGX70_EWI",
        "EGX100_EWI",
    )

    def __init__(self, database) -> None:
        self.database = database

    def load(
        self,
        market_date: date,
    ) -> list[OfficialIndexEvidence]:
        placeholders = ",".join(
            "?" for _ in self.REQUIRED
        )

        sql = f"""
            SELECT
                provider,
                symbol,
                source_snapshot_date,
                newest_market_date,
                status
            FROM canonical_data_artifacts
            WHERE provider = ?
              AND asset_type = 'INDEX_BARS'
              AND granularity = 'D1'
              AND source_snapshot_date = ?
              AND symbol IN ({placeholders})
            ORDER BY symbol, artifact_id
        """

        params = (
            self.PROVIDER,
            market_date.isoformat(),
            *self.REQUIRED,
        )

        with self.database.connect() as connection:
            rows = connection.execute(
                sql,
                params,
            ).fetchall()

        return [
            OfficialIndexEvidence(
                provider=row["provider"],
                index_name=row["symbol"],
                source_snapshot_date=date.fromisoformat(
                    row["source_snapshot_date"]
                ),
                newest_market_date=(
                    date.fromisoformat(
                        row["newest_market_date"]
                    )
                    if row["newest_market_date"]
                    else None
                ),
                validated=(
                    row["status"] == "VALIDATED"
                ),
            )
            for row in rows
        ]
