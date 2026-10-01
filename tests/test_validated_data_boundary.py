from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

APPROVED_CANONICAL_METADATA = {
    Path("app/data/index_canonical_store.py"),
    Path("app/data/validated_index_repository.py"),
    Path("app/data/validated_daily_repository.py"),
    Path("app/storage/database.py"),
    Path("app/storage/canonical_artifact_repository.py"),
    Path("app/storage/daily_canonical_artifact_repository.py"),
}

APPROVED_CANONICAL_IO = {
    Path("app/data/index_canonical_store.py"),
    Path("app/data/validated_index_repository.py"),
    Path("app/data/validated_daily_repository.py"),
}

CANONICAL_TOKENS = (
    "data/canonical",
    "/app/data/canonical",
    "canonical_root",
    "canonical_path",
)

FILE_IO_TOKENS = (
    "read_bytes(",
    "read_text(",
    ".open(",
    "open(",
)

CONSUMER_DIRECT_ACCESS_TOKENS = (
    "app.data.index_canonical_store",
    "CanonicalIndexStore",
    "data/canonical",
    "/app/data/canonical",
    "canonical_root",
    "canonical_path",
)


def app_python_files():
    return sorted(
        path
        for path in APP.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def relative(path: Path) -> Path:
    return path.relative_to(ROOT)


def test_canonical_metadata_references_are_allowlisted():
    violations = []

    for path in app_python_files():
        rel = relative(path)
        text = path.read_text(encoding="utf-8")

        if (
            any(token in text for token in CANONICAL_TOKENS)
            and rel not in APPROVED_CANONICAL_METADATA
        ):
            violations.append(str(rel))

    assert not violations, (
        "unapproved canonical metadata/path references: "
        + ", ".join(violations)
    )


def test_direct_canonical_file_io_is_allowlisted():
    violations = []

    for path in app_python_files():
        rel = relative(path)
        text = path.read_text(encoding="utf-8")

        has_canonical_reference = any(
            token in text
            for token in CANONICAL_TOKENS
        )

        has_file_io = any(
            token in text
            for token in FILE_IO_TOKENS
        )

        if (
            has_canonical_reference
            and has_file_io
            and rel not in APPROVED_CANONICAL_IO
        ):
            violations.append(str(rel))

    assert not violations, (
        "unapproved direct canonical file I/O: "
        + ", ".join(violations)
    )


def test_consumer_layers_cannot_bypass_validated_reader():
    consumer_roots = (
        APP / "strategies",
        APP / "core",
        APP / "risk",
        APP / "domain",
        APP / "research",
    )

    violations = []

    for consumer_root in consumer_roots:
        if not consumer_root.exists():
            continue

        for path in consumer_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")

            if any(
                token in text
                for token in CONSUMER_DIRECT_ACCESS_TOKENS
            ):
                violations.append(
                    str(relative(path))
                )

    assert not violations, (
        "consumer layer bypasses validated canonical "
        "reader: "
        + ", ".join(violations)
    )
