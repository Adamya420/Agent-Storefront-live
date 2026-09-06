"""Append-only, hash-chained audit log + receipt assembly (TRD §11).

The hashing and verification are PURE (no DB) so they can be fully unit-tested by
the planner agent. The DB writer is a thin, integration-tested wrapper.

Chain rule:
    hash_i = sha256( prev_hash + canonical_json(detail_i) )
    prev_hash_0 = GENESIS ("0" * 64)

Any inserted, deleted, reordered, or edited row breaks the chain: recomputing
hashes from the genesis forward will diverge at the tampered row. That is what
makes the log tamper-evident and a purchase reconstructable + trustworthy.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from backend.common.mandates import canonical_json

GENESIS_HASH = "0" * 64


def compute_hash(prev_hash: str, detail: dict) -> str:
    """Deterministic chain hash. canonical_json guarantees stable key order."""
    payload = f"{prev_hash}{canonical_json(detail)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class AuditRow:
    seq: int
    actor: str
    action: str
    detail: dict
    prev_hash: str
    hash: str


def link(prev_hash: str, seq: int, actor: str, action: str, detail: dict) -> AuditRow:
    """Build the next row in the chain from the previous hash. Pure."""
    h = compute_hash(prev_hash, detail)
    return AuditRow(seq=seq, actor=actor, action=action, detail=detail, prev_hash=prev_hash, hash=h)


def verify_chain(rows: list[AuditRow]) -> tuple[bool, int | None]:
    """Verify an ordered list of rows.

    Returns (ok, first_bad_seq). ok=True iff every row's prev_hash matches the
    previous row's hash AND every hash equals compute_hash(prev_hash, detail).
    On failure, first_bad_seq is the seq of the first divergent row.
    """
    expected_prev = GENESIS_HASH
    for row in rows:
        if row.prev_hash != expected_prev:
            return False, row.seq
        if row.hash != compute_hash(row.prev_hash, row.detail):
            return False, row.seq
        expected_prev = row.hash
    return True, None


def build_receipt_chain(intent_hash: str, cart_hash: str, payment_hash: str) -> dict:
    """Assemble the Intent -> Cart -> Payment consent chain for the receipt (TRD §11).

    This is the non-repudiable record: who authorized what, within what limits,
    what was charged. The head hash commits to all three links.
    """
    links = {
        "intent_hash": intent_hash,
        "cart_hash": cart_hash,
        "payment_hash": payment_hash,
    }
    head = compute_hash(GENESIS_HASH, links)
    return {"links": links, "chain_head_hash": head}


# ---------------------------------------------------------------- DB writer (integration)


class AuditWriter:
    """Persists chained rows. The .next_hash() logic is pure; the DB I/O is not.

    Usage (in orchestration): fetch the current head hash from the last audit row
    for this scope, then append. seq is a global monotonic counter (unique).
    """

    def __init__(self, session, actor: str) -> None:
        self._session = session
        self._actor = actor

    def _current_head(self) -> tuple[str, int]:
        from sqlalchemy import select

        from backend.models import AuditLog

        # seq is a single GLOBAL monotonic counter. Under concurrency, two writes
        # that read the same max seq both compute the same next value and one crashes
        # on the unique(seq) constraint, poisoning its transaction (deep-review).
        # Take a row lock on the current head so seq allocation is serialized on
        # Postgres. Guarded by dialect since SQLite/others don't support FOR UPDATE.
        stmt = select(AuditLog.hash, AuditLog.seq).order_by(AuditLog.seq.desc()).limit(1)
        try:
            if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
                stmt = stmt.with_for_update()
        except Exception:  # noqa: BLE001 — if we can't inspect the bind, proceed unlocked
            pass
        row = self._session.execute(stmt).first()
        if row is None:
            return GENESIS_HASH, 0
        return row[0], row[1]

    def append(self, session_id, action: str, detail: dict):
        """Append one chained audit row. Returns the created ORM row (uncommitted)."""
        from backend.models import AuditLog

        prev_hash, last_seq = self._current_head()
        seq = last_seq + 1
        h = compute_hash(prev_hash, detail)
        row = AuditLog(
            session_id=session_id,
            seq=seq,
            actor=self._actor,
            action=action,
            detail=detail,
            prev_hash=prev_hash,
            hash=h,
        )
        self._session.add(row)
        return row
