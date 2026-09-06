"""Local key registry (stand-in for AP2's DID resolution).

In real AP2, a merchant resolves the signer's public key via a DID method. Here
we keep a small local registry of ES256 keypairs on disk:

    data/keys/<kid>.pem       (private — GITIGNORED, never committed)
    data/keys/<kid>.pub.pem   (public)

Three roles (TRD §6.4):
    user-test-1      → signs Intent Mandates (the stand-in user wallet)
    agent-test-1     → counter-signs Cart Mandates (human-not-present)
    merchant-test-1  → signs Cart Mandates and delegated tokens
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

DEFAULT_KEYS_DIR = Path(__file__).resolve().parents[2] / "data" / "keys"


class KeyNotFoundError(Exception):
    """Raised when a kid has no key in the registry — never fall back to 'trust anyway'."""


def keys_dir() -> Path:
    return Path(os.getenv("ACG_KEYS_DIR", str(DEFAULT_KEYS_DIR)))


def private_key_path(kid: str) -> Path:
    return keys_dir() / f"{kid}.pem"


def public_key_path(kid: str) -> Path:
    return keys_dir() / f"{kid}.pub.pem"


@lru_cache(maxsize=32)
def load_private_key(kid: str) -> str:
    path = private_key_path(kid)
    if not path.exists():
        raise KeyNotFoundError(f"no private key for kid={kid!r} at {path}")
    return path.read_text()


@lru_cache(maxsize=32)
def load_public_key(kid: str) -> str:
    path = public_key_path(kid)
    if not path.exists():
        raise KeyNotFoundError(f"no public key for kid={kid!r} at {path}")
    return path.read_text()


def resolve_public_key(kid: str | None) -> str:
    """Resolve a signer's public key by kid. Missing kid is a hard failure."""
    if not kid:
        raise KeyNotFoundError("JWS header has no 'kid'; cannot resolve signer key")
    return load_public_key(kid)


def clear_cache() -> None:
    """Test helper: drop cached keys after regenerating or pointing at a temp dir."""
    load_private_key.cache_clear()
    load_public_key.cache_clear()
