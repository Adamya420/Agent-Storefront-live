"""Audit-trail reconstruction (Console, Merchant lens) — PURE, no DB.

Powers the "Audit trail" drawer the merchant opens next to any transaction (for a
dispute or buyer complaint). The audit ledger is a GLOBAL hash chain — seq is
monotonic across every session — so tamper-evidence is a property of the whole
ledger, not one session. We therefore verify the entire chain and then extract the
rows for the session in question: a stronger claim than a per-session check.

rule #8 boundary: this reconstructs the MERCHANT-side record (mandate verify,
offer, gate decision, settlement, receipt). It never includes the buyer agent's
private reasoning — that lives only in the Buyer lens.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.audit.chain import AuditRow, GENESIS_HASH, build_receipt_chain, verify_chain


@dataclass(frozen=True)
class TrailStep:
    seq: int
    action: str
    actor: str
    detail: dict
    hash: str
    prev_hash: str


@dataclass(frozen=True)
class TrailView:
    session_id: str
    chain_verified: bool          # the WHOLE ledger chain verifies
    first_bad_seq: int | None
    ledger_head_hash: str
    steps: list[TrailStep] = field(default_factory=list)
    receipt_present: bool = False
    receipt_verified: bool = False
    receipt_head_hash: str | None = None


def reconstruct_trail(all_rows: list[AuditRow], session_id: str,
                      row_session_ids: list, receipt: dict | None = None) -> TrailView:
    """Verify the global chain, then extract the target session's steps.

    all_rows: every audit row, ordered by seq ascending.
    row_session_ids: parallel list of the session_id each row belongs to (or None).
    receipt: {"links": {...}, "chain_head_hash": "..."} for this session, if any.
    """
    ok, bad = verify_chain(all_rows)
    head = all_rows[-1].hash if all_rows else GENESIS_HASH

    steps = [
        TrailStep(seq=r.seq, action=r.action, actor=r.actor, detail=r.detail,
                  hash=r.hash, prev_hash=r.prev_hash)
        for r, sid in zip(all_rows, row_session_ids)
        if str(sid) == str(session_id)
    ]

    receipt_present = receipt is not None
    receipt_verified = False
    receipt_head = None
    if receipt_present:
        links = receipt.get("links", {})
        receipt_head = receipt.get("chain_head_hash")
        rebuilt = build_receipt_chain(
            intent_hash=links.get("intent_hash", ""),
            cart_hash=links.get("cart_hash", ""),
            payment_hash=links.get("payment_hash", ""))
        receipt_verified = rebuilt["chain_head_hash"] == receipt_head

    return TrailView(
        session_id=str(session_id), chain_verified=ok, first_bad_seq=bad,
        ledger_head_hash=head, steps=steps, receipt_present=receipt_present,
        receipt_verified=receipt_verified, receipt_head_hash=receipt_head)
