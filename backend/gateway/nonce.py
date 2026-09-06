"""Nonce / replay protection (Authorization Gate check 4).

THE 2 AM BUG THIS IS DESIGNED AGAINST (reference repo, ERRORS.md history):
    A previous build constructed a FRESH, empty nonce store per HTTP request.
    Its unit test reused one store within a single function, so replay protection
    passed in tests — but over real HTTP every request saw an empty store, so a
    replayed request was never detected, and a double-charge slipped through.

Two lessons baked in here:
  1. The store MUST be a single shared, persistent instance across requests
     (Upstash in prod; one module-level singleton in tests). `get_nonce_store()`
     returns a singleton — never construct a store per request.
  2. The consume operation MUST be ATOMIC (check-and-set in one round trip), or
     two concurrent replays both see "fresh". Upstash `SET key val NX EX ttl`
     gives exactly this: it returns OK only if the key did not exist.

Semantics:
  * is_fresh(nonce)  -> read-only; True if never consumed. Used by the pure gate
                        for an early, clean reason code. NOT authoritative.
  * consume(nonce)   -> atomic; True if this call claimed it (first use), False
                        if it was already consumed (replay). AUTHORITATIVE. Called
                        exactly once, at settlement, after the gate passes.

Splitting is_fresh (read) from consume (atomic write) is deliberate: it keeps the
gate a pure function while making the irreversible claim happen at the money step.
"""

from __future__ import annotations

import os
import threading
import time
from abc import ABC, abstractmethod
from functools import lru_cache

DEFAULT_TTL_SECONDS = 3600  # a nonce cannot be replayed within an hour; mandates expire faster


class NonceStore(ABC):
    """A shared, persistent one-time-use code store. Never instantiate per request."""

    @abstractmethod
    def is_fresh(self, nonce: str) -> bool:
        """Read-only: True if the nonce has not been consumed. Not authoritative."""

    @abstractmethod
    def consume(self, nonce: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> bool:
        """Atomically claim the nonce. True on first use, False on replay."""


class InMemoryNonceStore(NonceStore):
    """For unit tests only. Thread-safe, atomic, and PERSISTENT for its lifetime.

    Because it persists for the life of the instance, a test that shares one
    instance across simulated requests behaves like prod. A test that builds a
    new instance per request would (correctly) fail to detect replays — that is
    the very bug we guard against, exercised in test_nonce_store.py.
    """

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def _purge_expired(self, now: float) -> None:
        expired = [n for n, exp in self._seen.items() if exp <= now]
        for n in expired:
            del self._seen[n]

    def is_fresh(self, nonce: str) -> bool:
        now = time.time()
        with self._lock:
            self._purge_expired(now)
            return nonce not in self._seen

    def consume(self, nonce: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> bool:
        now = time.time()
        with self._lock:
            self._purge_expired(now)
            if nonce in self._seen:
                return False
            self._seen[nonce] = now + ttl_seconds
            return True


class UpstashNonceStore(NonceStore):
    """Production nonce store on Upstash Redis (REST).

    Atomicity comes from Redis `SET key value NX EX ttl`, which sets the key only
    if it does not already exist. That single command is the whole replay defense.
    """

    def __init__(self, url: str, token: str, prefix: str = "acg:nonce:") -> None:
        # Imported lazily so unit tests never need the upstash package installed.
        from upstash_redis import Redis

        self._redis = Redis(url=url, token=token)
        self._prefix = prefix

    def _key(self, nonce: str) -> str:
        return f"{self._prefix}{nonce}"

    def is_fresh(self, nonce: str) -> bool:
        return self._redis.get(self._key(nonce)) is None

    def consume(self, nonce: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> bool:
        # SET NX EX: returns truthy only if the key was newly created.
        result = self._redis.set(self._key(nonce), "1", nx=True, ex=ttl_seconds)
        return bool(result)


@lru_cache(maxsize=1)
def get_nonce_store() -> NonceStore:
    """Singleton accessor. In prod returns one Upstash store for the whole process.

    NEVER call the store constructors per request — use this. The lru_cache is the
    structural guarantee that there is exactly one store instance.
    """
    url = os.getenv("UPSTASH_REDIS_REST_URL")
    token = os.getenv("UPSTASH_REDIS_REST_TOKEN")
    if url and token:
        return UpstashNonceStore(url, token)
    # No credentials: fall back to in-memory so local/unit runs work. This is
    # process-local and fine for tests, but MUST NOT be relied on in prod — a
    # multi-process deployment would not share it. Logged as a known limitation.
    return InMemoryNonceStore()
