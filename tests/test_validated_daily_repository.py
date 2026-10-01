import hashlib
import json

import pytest

from app.data.validated_daily_repository import (
    ValidatedDailyArtifactError, ValidatedDailyArtifactRepository,
)


def artifact_for(tmp_path, rows, relative="p/daily_bars/A.json"):
    target = tmp_path / "data" / "canonical" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(rows).encode()
    target.write_bytes(payload)
    return {"canonical_path": relative, "sha256": hashlib.sha256(payload).hexdigest(),
            "artifact_id": "a1", "canonical_symbol": "A"}


ROWS = [
    {"market_date": "2026-09-29", "open": "2", "high": "3", "low": "1", "close": "2.5", "volume": "10",
     "semantic_class": "VALID_EXECUTABLE"},
    {"market_date": "2026-09-28", "open": "2", "high": "3", "low": "1", "close": "2.4", "volume": "9",
     "semantic_class": "VALID_EXECUTABLE"},
    {"market_date": "2026-09-27", "open": "2", "high": "1", "low": "3", "close": "2", "volume": "9",
     "semantic_class": "QUARANTINED_ANOMALY"},
]


def repo(tmp_path):
    return ValidatedDailyArtifactRepository(database_path=tmp_path / "x.db", data_root=tmp_path / "data")


def test_loads_only_valid_rows_sorted_without_exposing_paths(tmp_path):
    dataset = repo(tmp_path).load(artifact_for(tmp_path, ROWS))
    assert [b["date"] for b in dataset.bars] == ["2026-09-28", "2026-09-29"]
    assert dataset.quarantined == 1 and "canonical_path" not in dataset.artifact
    assert str(dataset.bars[-1]["close"]) == "2.5"


def test_hash_mismatch_is_rejected(tmp_path):
    artifact = artifact_for(tmp_path, ROWS)
    artifact["sha256"] = "0" * 64
    with pytest.raises(ValidatedDailyArtifactError, match="ARTIFACT_HASH_MISMATCH"):
        repo(tmp_path).load(artifact)


def test_paths_escaping_the_canonical_root_are_rejected(tmp_path):
    (tmp_path / "outside.json").write_text("[]")
    with pytest.raises(ValidatedDailyArtifactError, match="escapes canonical root"):
        repo(tmp_path).load({"canonical_path": "../../outside.json", "sha256": "x"})
    root = tmp_path / "data" / "canonical"
    root.mkdir(parents=True, exist_ok=True)
    (root / "link.json").symlink_to(tmp_path / "outside.json")
    with pytest.raises(ValidatedDailyArtifactError, match="escapes canonical root"):
        repo(tmp_path).load({"canonical_path": "link.json", "sha256": "x"})
