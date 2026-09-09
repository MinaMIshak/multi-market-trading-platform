import pytest

from app.core.runtime_secrets import (
    RuntimeSecretError,
    read_runtime_secret,
)


def test_reads_secret_without_newline(
    tmp_path,
):
    path = tmp_path / "secret"
    path.write_text(
        "private-value\n",
        encoding="utf-8",
    )
    path.chmod(0o600)

    assert (
        read_runtime_secret(path)
        == "private-value"
    )


def test_missing_secret_fails_closed(
    tmp_path,
):
    with pytest.raises(
        RuntimeSecretError,
        match="missing",
    ):
        read_runtime_secret(
            tmp_path / "missing"
        )


def test_directory_secret_fails_closed(
    tmp_path,
):
    path = tmp_path / "secret"
    path.mkdir()

    with pytest.raises(
        RuntimeSecretError,
        match="not a file",
    ):
        read_runtime_secret(path)


def test_empty_secret_fails_closed(
    tmp_path,
):
    path = tmp_path / "secret"
    path.write_text(
        "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        RuntimeSecretError,
        match="empty",
    ):
        read_runtime_secret(path)


def test_oversized_secret_fails_closed(
    tmp_path,
):
    path = tmp_path / "secret"
    path.write_text(
        "x" * 32,
        encoding="utf-8",
    )

    with pytest.raises(
        RuntimeSecretError,
        match="too large",
    ):
        read_runtime_secret(
            path,
            max_bytes=16,
        )
