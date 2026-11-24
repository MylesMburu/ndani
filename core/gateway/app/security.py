"""API key auth, rate limiting and the audit log."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from collections import defaultdict
from typing import Any


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def check_key(raw_key: str | None, valid_hashes: list[str]) -> str | None:
    """Return a short key id if the key is valid, else None.

    Compares in constant time against every stored hash so timing does not
    reveal which (if any) key prefix matched.
    """
    if not raw_key or not valid_hashes:
        return None
    candidate = hash_key(raw_key)
    match = None
    for stored in valid_hashes:
        if hmac.compare_digest(candidate, stored.lower()):
            match = stored
    return match[:12] if match else None


class RateLimiter:
    """Token bucket per key id. In-memory: one gateway process per host."""

    def __init__(self, per_minute: int):
        self.capacity = max(per_minute, 1)
        self.refill_per_s = self.capacity / 60.0
        self._buckets: dict[str, tuple[float, float]] = defaultdict(
            lambda: (float(self.capacity), time.monotonic())
        )
        self._lock = threading.Lock()

    def allow(self, key_id: str) -> bool:
        with self._lock:
            tokens, last = self._buckets[key_id]
            now = time.monotonic()
            tokens = min(self.capacity, tokens + (now - last) * self.refill_per_s)
            if tokens < 1:
                self._buckets[key_id] = (tokens, now)
                return False
            self._buckets[key_id] = (tokens - 1, now)
            return True


class AuditLog:
    """Append-only JSONL. Content is hashed unless content logging is enabled."""

    def __init__(self, path: str, log_content: bool):
        self.path = path
        self.log_content = log_content
        self._lock = threading.Lock()
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)

    def record(self, *, key_id: str | None, endpoint: str, status: int,
               latency_ms: float, payload: Any = None, extra: dict | None = None) -> dict:
        entry: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "key_id": key_id,
            "endpoint": endpoint,
            "status": status,
            "latency_ms": round(latency_ms, 1),
        }
        if payload is not None:
            blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
            entry["payload_sha256"] = hashlib.sha256(blob).hexdigest()
            if self.log_content:
                entry["payload"] = payload
        if extra:
            entry.update(extra)
        line = json.dumps(entry, default=str)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return entry
