#!/usr/bin/env python3
"""Generate the three ES256 keypairs for the local key registry.

Usage:
    python data/generate_keys.py            # creates any missing keys
    python data/generate_keys.py --force    # regenerate all (invalidates old mandates)

Private keys are written to data/keys/*.pem and are GITIGNORED. Never commit them.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

ROLES = ("user-test-1", "agent-test-1", "merchant-test-1")
KEYS_DIR = Path(__file__).resolve().parent / "keys"


def generate_keypair(kid: str, keys_dir: Path, force: bool = False) -> bool:
    """Write <kid>.pem / <kid>.pub.pem. Returns True if written."""
    priv_path = keys_dir / f"{kid}.pem"
    pub_path = keys_dir / f"{kid}.pub.pem"
    if priv_path.exists() and not force:
        return False

    # SECP256R1 (P-256) is the curve ES256 requires.
    private_key = ec.generate_private_key(ec.SECP256R1())

    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    keys_dir.mkdir(parents=True, exist_ok=True)
    priv_path.write_bytes(priv_pem)
    pub_path.write_bytes(pub_pem)
    priv_path.chmod(0o600)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate ACG ES256 keypairs")
    parser.add_argument("--force", action="store_true", help="regenerate even if keys exist")
    parser.add_argument("--keys-dir", default=str(KEYS_DIR))
    args = parser.parse_args(argv)

    keys_dir = Path(args.keys_dir)
    for kid in ROLES:
        written = generate_keypair(kid, keys_dir, force=args.force)
        status = "created" if written else "exists (skipped)"
        # Never print key material — only the kid and status.
        print(f"  {kid}: {status}")
    print(f"\nKeys in {keys_dir}. Private keys are gitignored — never commit them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
