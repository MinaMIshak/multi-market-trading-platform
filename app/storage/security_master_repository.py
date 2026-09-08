from __future__ import annotations

import json
from datetime import (
    datetime,
    timezone,
)

from app.data.security_master import (
    CanonicalInstrument,
)
from app.storage.database import Database


def _utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _normalize_alias(
    value: str,
) -> str:
    return value.strip().upper()


class SecurityMasterRepository:
    def __init__(
        self,
        database: Database,
    ) -> None:
        self.database = database

    def replace_provider_snapshot(
        self,
        *,
        provider: str,
        instruments: list[
            CanonicalInstrument
        ],
    ) -> None:
        provider = (
            provider.strip().lower()
        )

        connection = (
            self.database.connect()
        )

        try:
            connection.execute(
                "BEGIN IMMEDIATE"
            )

            old_ids = connection.execute(
                """
                SELECT instrument_id
                FROM canonical_instruments
                WHERE source_provider = ?
                """,
                (provider,),
            ).fetchall()

            for row in old_ids:
                connection.execute(
                    """
                    DELETE FROM instrument_aliases
                    WHERE instrument_id = ?
                    """,
                    (
                        row[
                            "instrument_id"
                        ],
                    ),
                )

            connection.execute(
                """
                DELETE FROM canonical_instruments
                WHERE source_provider = ?
                """,
                (provider,),
            )

            for instrument in instruments:
                connection.execute(
                    """
                    INSERT INTO canonical_instruments (
                        instrument_id,
                        instrument_type,
                        canonical_ticker,
                        name_en,
                        name_ar,
                        short_name_en,
                        short_name_ar,
                        source_provider,
                        source_symbol_code,
                        reuters_raw,
                        reuters_normalized,
                        source_sha256,
                        source_market_date,
                        normalization_notes_json,
                        updated_at
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?,
                        ?, ?, ?
                    )
                    """,
                    (
                        str(
                            instrument
                            .instrument_id
                        ),
                        (
                            instrument
                            .instrument_type
                            .value
                        ),
                        (
                            instrument
                            .canonical_ticker
                        ),
                        instrument.name_en,
                        instrument.name_ar,
                        (
                            instrument
                            .short_name_en
                        ),
                        (
                            instrument
                            .short_name_ar
                        ),
                        (
                            instrument
                            .source_provider
                        ),
                        (
                            instrument
                            .source_symbol_code
                        ),
                        (
                            instrument
                            .reuters_raw
                        ),
                        (
                            instrument
                            .reuters_normalized
                        ),
                        (
                            instrument
                            .source_sha256
                        ),
                        (
                            instrument
                            .source_market_date
                        ),
                        json.dumps(
                            instrument
                            .normalization_notes,
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        _utc_now(),
                    ),
                )

                aliases: list[
                    tuple[
                        str,
                        str,
                        str,
                    ]
                ] = [
                    (
                        "egid",
                        "EGID_SYMBOL_CODE",
                        (
                            instrument
                            .source_symbol_code
                        ),
                    ),
                    (
                        "canonical",
                        "CANONICAL_TICKER",
                        (
                            instrument
                            .canonical_ticker
                        ),
                    ),
                ]

                if instrument.reuters_raw:
                    aliases.append(
                        (
                            "egid",
                            "REUTERS_RAW",
                            (
                                instrument
                                .reuters_raw
                            ),
                        )
                    )

                if (
                    instrument
                    .reuters_normalized
                ):
                    aliases.append(
                        (
                            "egid",
                            "REUTERS_NORMALIZED",
                            (
                                instrument
                                .reuters_normalized
                            ),
                        )
                    )

                for (
                    alias_provider,
                    alias_type,
                    alias_value,
                ) in aliases:
                    connection.execute(
                        """
                        INSERT INTO instrument_aliases (
                            instrument_id,
                            provider,
                            alias_type,
                            alias_value,
                            normalized_value,
                            created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(
                                instrument
                                .instrument_id
                            ),
                            alias_provider,
                            alias_type,
                            alias_value,
                            _normalize_alias(
                                alias_value
                            ),
                            _utc_now(),
                        ),
                    )

            connection.commit()

        except Exception:
            connection.rollback()
            raise

        finally:
            connection.close()

    def resolve(
        self,
        value: str,
        *,
        provider: str | None = None,
    ) -> dict:
        normalized = (
            _normalize_alias(
                value
            )
        )

        params: list[str] = [
            normalized
        ]

        provider_filter = ""

        if provider is not None:
            provider_filter = (
                " AND a.provider = ? "
            )

            params.append(
                provider
                .strip()
                .lower()
            )

        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    i.*,
                    a.provider
                        AS matched_provider,
                    a.alias_type
                        AS matched_alias_type,
                    a.alias_value
                        AS matched_alias_value

                FROM instrument_aliases AS a

                JOIN canonical_instruments AS i
                  ON i.instrument_id
                     = a.instrument_id

                WHERE
                    a.normalized_value = ?
                    {provider_filter}

                ORDER BY
                    i.instrument_id,
                    a.alias_type
                """,
                tuple(params),
            ).fetchall()

        if not rows:
            raise KeyError(
                f"instrument not found: "
                f"{value}"
            )

        instrument_ids = {
            row["instrument_id"]
            for row in rows
        }

        if len(instrument_ids) > 1:
            raise ValueError(
                "ambiguous instrument alias: "
                f"{value}"
            )

        return dict(rows[0])

    def count_instruments(
        self,
    ) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM canonical_instruments
                """
            ).fetchone()

        return int(
            row["count"]
        )

    def count_aliases(
        self,
    ) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM instrument_aliases
                """
            ).fetchone()

        return int(
            row["count"]
        )

    def type_counts(
        self,
    ) -> dict[str, int]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    instrument_type,
                    COUNT(*) AS count

                FROM canonical_instruments

                GROUP BY instrument_type

                ORDER BY instrument_type
                """
            ).fetchall()

        return {
            row["instrument_type"]:
                int(row["count"])
            for row in rows
        }
