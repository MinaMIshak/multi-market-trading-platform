from __future__ import annotations

import json
from hashlib import sha256

from datetime import (
    datetime,
    timezone,
)

from app.data.models import DataAssetType, RawArtifactManifest
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
        admission_manifest: RawArtifactManifest | None = None,
    ) -> None:
        provider = (
            provider.strip().lower()
        )

        # Freeze caller-owned mutable models before opening the write transaction.
        instruments = [CanonicalInstrument.model_validate(i.model_dump()) for i in instruments]
        manifest = None
        if admission_manifest is not None:
            manifest = RawArtifactManifest.model_validate(admission_manifest.model_dump())
            if (provider != 'egid' or manifest.provider != provider
                    or manifest.asset_type != DataAssetType.SECURITY_MASTER
                    or manifest.market_date is None or manifest.symbol is not None
                    or manifest.granularity is not None
                    or manifest.record_count != len(instruments)
                    or any(c not in '0123456789abcdef' for c in manifest.sha256)
                    or any(i.source_provider != provider or i.source_sha256 != manifest.sha256
                           or i.source_market_date != manifest.market_date.isoformat()
                           for i in instruments)):
                raise ValueError('admission manifest does not bind snapshot')

        connection = (
            self.database.connect()
        )

        try:
            connection.execute(
                "BEGIN IMMEDIATE"
            )

            if manifest is not None:
                self._record_admission(connection, provider, instruments, manifest)

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
                        provider,
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
                            provider,
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
                            provider,
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

    @staticmethod
    def _record_admission(connection, provider, instruments, manifest):
        # Repeat existing refresh completeness gates under the same writer lock
        # as replacement, so another admitted writer cannot race the count check.
        from app.data.security_master_refresh_job import MIN_RETAINED_FRACTION
        latest = connection.execute(
            'SELECT MAX(snapshot_date) FROM security_master_admissions WHERE provider = ?',
            (provider,),
        ).fetchone()[0]
        if latest is not None and latest > manifest.market_date.isoformat():
            raise ValueError('historical admission cannot replace newer snapshot')
        previous = connection.execute(
            'SELECT COUNT(*) FROM canonical_instruments WHERE source_provider = ?',
            (provider,),
        ).fetchone()[0]
        if (not any(i.instrument_type.value == 'EQUITY' for i in instruments)
                or len(instruments) < previous * MIN_RETAINED_FRACTION):
            raise ValueError('incomplete admitted snapshot')
        snapshot = sorted((i.model_dump(mode='json') for i in instruments),
                          key=lambda i: i['instrument_id'])
        content = {
            'schema_version': 'security-master-admission-v1',
            'provider': provider, 'snapshot_date': manifest.market_date.isoformat(),
            'artifact_sha256': manifest.sha256, 'byte_size': manifest.byte_size,
            'record_count': manifest.record_count, 'instrument_count': len(instruments),
            'instruments': snapshot,
            'policy': 'EGID_EQUITY_NONEMPTY_RETAIN_90_PERCENT_V1',
            'live_money': False,
        }
        recorded_at = _utc_now()
        if manifest.received_at > datetime.fromisoformat(recorded_at):
            raise ValueError('future security master receipt')
        existing = connection.execute(
            'SELECT * FROM security_master_admissions WHERE provider = ? AND snapshot_date = ?',
            (provider, manifest.market_date.isoformat()),
        ).fetchone()
        if existing is not None:
            saved = SecurityMasterRepository._decode_admission(existing)
            comparable = {k: v for k, v in saved.items()
                          if k not in ('recorded_at', 'received_at', 'previous_instrument_count')}
            if comparable != content:
                raise ValueError('immutable security master admission conflict')
            return
        content.update(recorded_at=recorded_at, received_at=manifest.received_at.isoformat(),
                       previous_instrument_count=previous)
        payload = json.dumps(content, sort_keys=True, separators=(',', ':'), allow_nan=False)
        connection.execute(
            'INSERT INTO security_master_admissions VALUES (?, ?, ?, ?, ?)',
            (provider, manifest.market_date.isoformat(), manifest.sha256, payload,
             sha256(payload.encode()).hexdigest()),
        )

    @staticmethod
    def _decode_admission(row):
        payload = row['receipt_json']
        if sha256(payload.encode()).hexdigest() != row['receipt_sha256']:
            raise ValueError('security master admission integrity mismatch')
        result = json.loads(payload)
        if (result['provider'] != row['provider']
                or result['snapshot_date'] != row['snapshot_date']
                or result['artifact_sha256'] != row['artifact_sha256']):
            raise ValueError('security master admission identity mismatch')
        return result

    def get_admission(self, *, provider, snapshot_date, artifact_sha256):
        """Exact historical receipt only; never fall back to the current master.

        recorded_at is transaction recording time, not proof of commit visibility
        at that instant. A trusted post-commit observation is needed for PIT use.
        """
        with self.database.connect() as connection:
            row = connection.execute(
                'SELECT * FROM security_master_admissions '
                'WHERE provider = ? AND snapshot_date = ? AND artifact_sha256 = ?',
                (provider, snapshot_date.isoformat(), artifact_sha256),
            ).fetchone()
        if row is None:
            raise KeyError('historical security master admission unavailable')
        return self._decode_admission(row)

    def observe_admission(self, *, provider, snapshot_date, artifact_sha256):
        """Read committed history on a fresh connection, then sample availability.

        This observation is not backdated to recorded_at. The caller must retain
        the returned envelope in trusted evidence storage for later PIT use.
        No observation is automatically persisted or inferred for older reads.
        """
        receipt = self.get_admission(provider=provider, snapshot_date=snapshot_date,
                                     artifact_sha256=artifact_sha256)
        observed_at = _utc_now()
        if datetime.fromisoformat(receipt['recorded_at']) > datetime.fromisoformat(observed_at):
            raise ValueError('admission observation clock precedes recording')
        payload = json.dumps(receipt, sort_keys=True, separators=(',', ':'), allow_nan=False)
        return {
            'schema_version': 'security-master-observation-v1',
            'observed_at': observed_at,
            'receipt_json': payload,
            'receipt_sha256': sha256(payload.encode()).hexdigest(),
            'live_money': False,
        }

    def retain_admission_observation(self, *, provider, snapshot_date, artifact_sha256):
        """Persist first post-read evidence, never caller-supplied timestamps.

        A crash before commit creates no historical observation. Concurrent retries
        return the winner's unchanged envelope. Database writers are trusted;
        the digest is not a cryptographic signature.
        """
        args = dict(provider=provider, snapshot_date=snapshot_date,
                    artifact_sha256=artifact_sha256)
        observation = self.observe_admission(**args)
        payload = json.dumps(observation, sort_keys=True, separators=(',', ':'), allow_nan=False)
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = self._observation_row(connection, **args)
            if row is None:
                connection.execute(
                    'INSERT INTO security_master_observations VALUES (?, ?, ?, ?, ?)',
                    (provider, snapshot_date.isoformat(), artifact_sha256, payload,
                     sha256(payload.encode()).hexdigest()))
            else:
                self._decode_observation(row, dict(observation, observed_at=_utc_now()))
        return self.get_admission_observation(**args)

    @staticmethod
    def _observation_row(connection, *, provider, snapshot_date, artifact_sha256):
        return connection.execute(
            'SELECT * FROM security_master_observations '
            'WHERE provider = ? AND snapshot_date = ? AND artifact_sha256 = ?',
            (provider, snapshot_date.isoformat(), artifact_sha256),
        ).fetchone()

    @staticmethod
    def _decode_observation(row, current):
        payload = row['envelope_json']
        if sha256(payload.encode()).hexdigest() != row['envelope_sha256']:
            raise ValueError('security master observation integrity mismatch')
        envelope = json.loads(payload)
        if (type(envelope) is not dict or set(envelope) != set(current)
                or any(envelope[k] != current[k] for k in current if k != 'observed_at')
                or envelope['live_money'] is not False
                or json.dumps(envelope, sort_keys=True, separators=(',', ':'),
                              allow_nan=False) != payload):
            raise ValueError('security master observation binding mismatch')
        observed = datetime.fromisoformat(envelope['observed_at'])
        recorded = datetime.fromisoformat(json.loads(current['receipt_json'])['recorded_at'])
        now = datetime.fromisoformat(current['observed_at'])
        if observed.utcoffset() is None or not recorded <= observed <= now:
            raise ValueError('security master observation clock mismatch')
        return envelope

    def get_admission_observation(self, *, provider, snapshot_date, artifact_sha256):
        """Read exact retained evidence; absence never synthesizes availability."""
        args = dict(provider=provider, snapshot_date=snapshot_date,
                    artifact_sha256=artifact_sha256)
        with self.database.connect() as connection:
            row = self._observation_row(connection, **args)
        if row is None:
            raise KeyError('retained security master observation unavailable')
        return self._decode_observation(row, self.observe_admission(**args))

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

    def count_provider_instruments(
        self,
        provider: str,
    ) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM canonical_instruments
                WHERE source_provider = ?
                """,
                (
                    provider.strip().lower(),
                ),
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

    def list_equity_tickers(
        self,
    ) -> tuple[str, ...]:
        """Distinct EQUITY canonical tickers across all provider snapshots.

        Multiple providers may each hold a row for the same real instrument;
        this is scope attribution only, never dated membership evidence.
        """
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT canonical_ticker
                FROM canonical_instruments
                WHERE instrument_type = ?
                ORDER BY canonical_ticker
                """,
                ("EQUITY",),
            ).fetchall()

        return tuple(
            row["canonical_ticker"]
            for row in rows
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
