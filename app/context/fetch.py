"""Polite, bounded HTTP acquisition plus content-addressed raw evidence.

- HTTPS only; responses capped at ``max_bytes``.
- Per-host minimum spacing (rate-limit awareness, e.g. GDELT ≥ 5 s, SEC ≤ 10/s).
- Retries only on transient failures (network errors, 429, 5xx) with bounded
  exponential backoff; ``Retry-After`` is honoured up to ``max_backoff``.
  Other 4xx fail immediately (a 403 is a policy answer, not a glitch).
- Every payload is stored before parsing under
  ``<data-root>/context/raw/<source>/<sha256>.<ext>`` (never overwritten;
  corruption detected), so re-runs are idempotent and auditable.
"""
from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_UA = "EGX-US-PaperShadow-Research/1.0 (personal non-commercial research)"
TRANSIENT_STATUS = {429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class Fetcher:
    def __init__(self, *, user_agent=DEFAULT_UA, spacing=None, max_attempts=3, backoff=2.0, max_backoff=60.0,
                 max_bytes=12 * 1024 * 1024, timeout=30, sleep=time.sleep, clock=time.monotonic, opener=None):
        self.user_agent, self.spacing = user_agent, dict(spacing or {})
        self.max_attempts, self.backoff, self.max_backoff = max_attempts, backoff, max_backoff
        self.max_bytes, self.timeout = max_bytes, timeout
        self._sleep, self._clock = sleep, clock
        self._opener = opener or urllib.request.build_opener()
        self._last = {}
        self.log = []

    def _wait_for_host(self, host):
        gap = self.spacing.get(host, 0)
        if host in self._last and gap:
            remaining = gap - (self._clock() - self._last[host])
            if remaining > 0:
                self._sleep(remaining)

    def get(self, url, *, headers=None, data=None, method=None):
        """Bytes and response headers; raises FetchError with a stable code."""
        if not url.startswith("https://"):
            raise FetchError("HTTPS_REQUIRED")
        host = urlparse(url).hostname
        merged = {"User-Agent": self.user_agent, "Accept": "*/*", **(headers or {})}
        last = "UNKNOWN"
        for attempt in range(1, self.max_attempts + 1):
            self._wait_for_host(host)
            self._last[host] = self._clock()
            delay = self.backoff * 2 ** (attempt - 1)
            try:
                request = urllib.request.Request(url, headers=merged, data=data, method=method)
                with self._opener.open(request, timeout=self.timeout) as response:
                    payload = response.read(self.max_bytes + 1)
                    if len(payload) > self.max_bytes:
                        raise FetchError("RESPONSE_TOO_LARGE")
                    self.log.append({"host": host, "status": response.status, "attempt": attempt})
                    return payload, dict(response.headers.items())
            except urllib.error.HTTPError as exc:
                last = f"HTTP_{exc.code}"
                self.log.append({"host": host, "status": exc.code, "attempt": attempt})
                if exc.code not in TRANSIENT_STATUS:
                    raise FetchError(last) from None
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                if retry_after and retry_after.isdigit():
                    delay = max(delay, float(retry_after))
            except FetchError:
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                last = f"NETWORK_{type(exc).__name__}"
                self.log.append({"host": host, "status": None, "attempt": attempt})
            if attempt < self.max_attempts:
                self._sleep(min(delay, self.max_backoff))
        raise FetchError(last)


def store_raw(data_root, source_id, payload, ext="bin"):
    """Content-addressed raw evidence; returns (sha256, path)."""
    digest = hashlib.sha256(payload).hexdigest()
    folder = Path(data_root) / "context" / "raw" / source_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{digest}.{ext}"
    try:
        with path.open("xb") as stream:
            stream.write(payload)
    except FileExistsError:
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise FetchError("RAW_STORE_CORRUPTION") from None
    return digest, str(path)


def provenance(source_id, url, digest, *, retrieved_at=None, headers=None):
    retrieved_at = retrieved_at or datetime.now(timezone.utc)
    record = {"source_id": source_id, "url": url, "raw_sha256": digest, "retrieved_at": retrieved_at.isoformat()}
    if headers:
        for key in ("Last-Modified", "ETag", "Date"):
            if headers.get(key):
                record[key.lower().replace("-", "_")] = headers[key]
    return record
