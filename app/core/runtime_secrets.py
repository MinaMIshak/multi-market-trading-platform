from __future__ import annotations

from pathlib import Path


class RuntimeSecretError(RuntimeError):
    pass


def read_runtime_secret(
    path: str | Path,
    *,
    max_bytes: int = 4096,
) -> str:
    secret_path = Path(path)

    if not secret_path.exists():
        raise RuntimeSecretError(
            "runtime secret file is missing"
        )

    if not secret_path.is_file():
        raise RuntimeSecretError(
            "runtime secret path is not a file"
        )

    size = secret_path.stat().st_size

    if size <= 0:
        raise RuntimeSecretError(
            "runtime secret file is empty"
        )

    if size > max_bytes:
        raise RuntimeSecretError(
            "runtime secret file is too large"
        )

    try:
        value = secret_path.read_text(
            encoding="utf-8"
        ).strip()
    except (OSError, UnicodeError) as exc:
        raise RuntimeSecretError(
            "runtime secret cannot be read"
        ) from exc

    if not value:
        raise RuntimeSecretError(
            "runtime secret is empty"
        )

    return value
