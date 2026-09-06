"""Mandate Verifier (Execution plane).

DETERMINISM RULE (AGENTS.md §4): no LLM may ever be imported or called in this
module or anywhere under backend/gateway/. Verification is pure logic.

TIER SCOPE — read this before extending:
    T0 (this file, now): steps 1-2-3-5 of TRD §6.2 as PURE functions —
        schema, signature, expiry, scope. No DB, no network, fully unit-testable.
    T1 (next): step 4 (nonce/replay via Upstash) + persistence of the
        intent_mandate/authorization rows + audit row emission. Those need
        external services, so they are integration-tested by Claude Code /
        Antigravity, not here (agent.md).

Splitting it this way is deliberate: keeping the decision logic pure is what
makes the money path testable by the planner agent at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from backend.common.keys import KeyNotFoundError, resolve_public_key
from backend.common.mandates import (
    IntentMandatePayload,
    MandateMalformedError,
    MandateSignatureError,
    parse_intent_mandate,
    peek_header,
)


class VerifyReason:
    """Fixed reason codes for mandate verification (subset of TRD §7.3).

    Never return free text — every failure maps to exactly one of these.
    """

    OK = "OK"
    MANDATE_MALFORMED = "MANDATE_MALFORMED"
    SIGNATURE_INVALID = "SIGNATURE_INVALID"
    MANDATE_EXPIRED = "MANDATE_EXPIRED"
    MERCHANT_SCOPE = "MERCHANT_SCOPE"
    CATEGORY_SCOPE = "CATEGORY_SCOPE"
    SIGNER_NOT_AUTHORIZED = "SIGNER_NOT_AUTHORIZED"
    # NONCE_REPLAY is emitted in T1 by the stateful layer, not here.


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    reason_code: str
    payload: IntentMandatePayload | None = None
    detail: str = ""

    def __bool__(self) -> bool:  # convenience in tests
        return self.ok


KeyResolver = Callable[[str | None], str]


# Cap how far a mandate's expiry may sit in the future. Must not exceed the
# nonce store's replay window (nonce.py DEFAULT_TTL_SECONDS=3600), or a long-
# lived mandate could be replayed after its nonce silently expires.
MAX_MANDATE_TTL_SECONDS = int(os.getenv("ACG_MAX_MANDATE_TTL_SECONDS", "3600"))


def verify_intent_mandate(
    token: str,
    *,
    merchant_id: str,
    allowed_categories: list[str] | tuple[str, ...],
    now: datetime | None = None,
    key_resolver: KeyResolver = resolve_public_key,
    expected_signer_kid: str | None = None,
    max_ttl_seconds: int = MAX_MANDATE_TTL_SECONDS,
) -> VerifyResult:
    """Pure structural verification of an Intent Mandate.

    Order is load-bearing and must not be reordered without a DECISIONS.md entry:
      1. schema/format  → 1b. signer role  → 2. signature  → 3. expiry  → 5. scope
    We verify the SIGNATURE BEFORE reading any field for a decision, so we never
    make a trust decision on unverified data. (Step 4, nonce/replay, is stateful
    and lives in T1.)

    `expected_signer_kid` pins WHO may originate a mandate: only the user wallet
    signs Intent Mandates. Without this, an agent or merchant could self-sign its
    own ceiling — buyer consent would be decorative. `max_ttl_seconds` bounds how
    far the expiry may sit in the future so a long-lived mandate can't outlive the
    nonce store's replay window (see backend/gateway/nonce.py).
    """
    now = now or datetime.now(timezone.utc)

    # --- 1. format: read the header (unverified) only to pick a key
    try:
        header = peek_header(token)
    except MandateMalformedError as exc:
        return VerifyResult(False, VerifyReason.MANDATE_MALFORMED, detail=str(exc))

    kid = header.get("kid")

    # --- 1b. signer role: an Intent Mandate may ONLY be signed by the user wallet.
    # Checked before signature so a self-signed agent/merchant mandate is rejected
    # as an unauthorized signer, not silently trusted.
    if expected_signer_kid is not None and kid != expected_signer_kid:
        return VerifyResult(
            False, VerifyReason.SIGNER_NOT_AUTHORIZED,
            detail=f"intent mandate signer {kid!r} is not the authorized user",
        )

    try:
        public_key = key_resolver(kid)
    except KeyNotFoundError as exc:
        # An unknown signer is a signature failure, not a "malformed" one.
        return VerifyResult(False, VerifyReason.SIGNATURE_INVALID, detail=str(exc))

    # --- 2. signature (and schema validation, which happens after verification)
    try:
        payload = parse_intent_mandate(token, public_key)
    except MandateSignatureError as exc:
        return VerifyResult(False, VerifyReason.SIGNATURE_INVALID, detail=str(exc))
    except MandateMalformedError as exc:
        return VerifyResult(False, VerifyReason.MANDATE_MALFORMED, detail=str(exc))

    # --- 3. expiry (past, and too-far-future)
    if payload.is_expired(now):
        return VerifyResult(False, VerifyReason.MANDATE_EXPIRED, payload, "mandate expiry has passed")
    if max_ttl_seconds and payload.expiry > now + timedelta(seconds=max_ttl_seconds):
        return VerifyResult(
            False, VerifyReason.MANDATE_EXPIRED, payload,
            f"mandate expiry exceeds the maximum allowed TTL ({max_ttl_seconds}s)",
        )

    # --- 5. scope
    if payload.allowed_merchants and merchant_id not in payload.allowed_merchants:
        return VerifyResult(
            False, VerifyReason.MERCHANT_SCOPE, payload, "this merchant is not in allowed_merchants"
        )
    if allowed_categories and payload.constraints.category not in allowed_categories:
        return VerifyResult(
            False, VerifyReason.CATEGORY_SCOPE, payload, "category is not sold by this merchant"
        )

    return VerifyResult(True, VerifyReason.OK, payload)


def build_ceiling(payload: IntentMandatePayload) -> dict:
    """Snapshot the verified constraints+tolerances as the authorization ceiling.

    This dict is stored on the `authorization` row and is the ONLY thing the
    Offer Engine and Gate may treat as the buyer's authority. Nothing downstream
    may read the raw mandate again — the ceiling is the single source of truth.
    """
    return {
        "constraints": payload.constraints.model_dump(mode="json"),
        "tolerances": payload.tolerances.model_dump(mode="json"),
        "buyer_id": payload.buyer_id,
        "agent_id": payload.agent_id,
        "expiry": payload.expiry.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "nonce": payload.nonce,
    }
