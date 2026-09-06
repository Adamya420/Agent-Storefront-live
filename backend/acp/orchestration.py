"""Checkout orchestration (Execution plane wiring).

This is the ONLY place side effects happen. It:
  1. fetches real state (verified authorization, nonce freshness, inventory,
     existing payment),
  2. builds a pure GateContext and calls the pure gate,
  3. on PASS, ATOMICALLY consumes the nonce, then settles, then writes the
     receipt — in that order, so a failed settlement never leaves a consumed
     nonce that would wrongly block a legitimate retry, and a stale-stock retry
     never burns the authorization (inventory is a gate check that runs first).

The gate itself stays pure (backend/gateway/gate.py). Keeping the irreversible
nonce consume here — after PASS and immediately before settlement — is the fix
for the reference-repo double-charge class (DECISIONS.md 2026-09-01).

This module is integration-tested (needs DB + settlement provider). Its pure
helper `build_gate_context` is unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from backend.gateway.gate import GateContext, GateResult, ReasonCode, authorize
from backend.payments.settlement import idempotency_key


@dataclass
class CompleteInputs:
    """Pre-fetched, verified facts the orchestration passes to the gate."""

    authorization_active: bool
    authorization_expired: bool
    cart_sig_valid: bool
    token_sig_valid: bool
    token_expired: bool
    cart_nonce_fresh: bool
    token_nonce_fresh: bool
    merchant_in_scope: bool
    category_in_scope: bool
    cart_total_paise: int
    ceiling: dict
    cart_return_days: int
    cart_delivery_days: int
    cart_quantity: int
    concession_within_band: bool
    resulting_margin_bps: int
    margin_floor_bps: int
    cart_hash: str
    priced_offer_hash: str
    stock_available: int
    stock_version_at_offer: int
    stock_version_now: int
    token_max_amount_paise: int
    token_merchant_matches: bool
    token_consumed: bool
    existing_captured_payment: bool
    driving_sku_is_injection: bool
    recompute_price_paise: int | None = None
    recompute_cost_paise: int | None = None


def build_gate_context(inp: CompleteInputs) -> GateContext:
    """Pure: map fetched facts + the verified ceiling into the gate's context."""
    c = inp.ceiling["constraints"]
    return GateContext(
        authorization_active=inp.authorization_active,
        cart_sig_valid=inp.cart_sig_valid,
        token_sig_valid=inp.token_sig_valid,
        authorization_expired=inp.authorization_expired,
        token_expired=inp.token_expired,
        cart_nonce_fresh=inp.cart_nonce_fresh,
        token_nonce_fresh=inp.token_nonce_fresh,
        merchant_in_scope=inp.merchant_in_scope,
        category_in_scope=inp.category_in_scope,
        cart_total_paise=inp.cart_total_paise,
        ceiling_max_price_paise=c["max_price_paise"],
        cart_return_days=inp.cart_return_days,
        ceiling_min_return_days=c["min_return_days"],
        cart_delivery_days=inp.cart_delivery_days,
        ceiling_max_delivery_days=c["max_delivery_days"],
        cart_quantity=inp.cart_quantity,
        ceiling_quantity=c["quantity"],
        concession_within_band=inp.concession_within_band,
        resulting_margin_bps=inp.resulting_margin_bps,
        margin_floor_bps=inp.margin_floor_bps,
        cart_hash=inp.cart_hash,
        priced_offer_hash=inp.priced_offer_hash,
        stock_available=inp.stock_available,
        stock_version_at_offer=inp.stock_version_at_offer,
        stock_version_now=inp.stock_version_now,
        token_max_amount_paise=inp.token_max_amount_paise,
        token_merchant_matches=inp.token_merchant_matches,
        token_consumed=inp.token_consumed,
        existing_captured_payment=inp.existing_captured_payment,
        driving_sku_is_injection=inp.driving_sku_is_injection,
        recompute_price_paise=inp.recompute_price_paise,
        recompute_cost_paise=inp.recompute_cost_paise,
    )


@dataclass
class InitiateOutcome:
    """Result of gate + nonce consume + payment-link creation.

    'passed' here means the money action was AUTHORIZED and a payment link was
    created — NOT that money has arrived. Capture happens later in finalize
    (poll or webhook). This is the transaction split that fixes the
    audit-rollback bug: the gate decision + link creation commit here; a later
    settlement failure can never roll back this audit.
    """

    passed: bool
    reason_code: str
    payment_link_id: str | None = None
    payment_link_url: str | None = None
    gate: GateResult | None = None


def run_initiate(
    inp: CompleteInputs,
    *,
    session_id: str,
    cart_nonce: str,
    token_nonce: str,
    nonce_store,
    settlement_provider,
    audit,  # AuditWriter-like: .append(session_id, action, detail)
) -> InitiateOutcome:
    """Gate → (on pass) atomic nonce consume → create payment link → audit.

    Ordering (the money-safety core, unchanged from T1):
      * The gate runs FIRST and fully. Every decision (pass/deny) is audited.
      * Only on PASS do we ATOMICALLY consume both nonces. A lost race → DENY
        NONCE_REPLAY, and NO link is created. This is the authoritative replay
        defense; the gate's check-4 is only an early read.
      * Link creation is idempotent by (session_id + cart_hash): a repeated
        initiate returns the SAME link rather than a second one.

    The caller commits after this returns; capture is a separate transaction.
    """
    ctx = build_gate_context(inp)
    result = authorize(ctx)

    audit.append(session_id, f"GATE_{'PASS' if result.passed else 'DENY'}",
                 {"reason_code": result.reason_code, "check_index": result.check_index,
                  "cart_hash": inp.cart_hash})

    if not result.passed:
        return InitiateOutcome(passed=False, reason_code=result.reason_code, gate=result)

    # --- PASS: claim the nonces atomically before any money action is initiated ---
    # Verify BOTH are fresh before consuming EITHER. Consuming is destructive and
    # can't be undone, so consuming cart first and only then finding token stale
    # would permanently burn a good cart nonce and wrongly block a legitimate retry
    # of that exact cart (deep-review). Checking freshness up front catches the
    # common "token already used by a prior attempt" case before anything is burned.
    if not nonce_store.is_fresh(cart_nonce):
        audit.append(session_id, "GATE_DENY", {"reason_code": ReasonCode.NONCE_REPLAY,
                                               "stage": "precheck", "which": "cart"})
        return InitiateOutcome(passed=False, reason_code=ReasonCode.NONCE_REPLAY, gate=result)
    if not nonce_store.is_fresh(token_nonce):
        audit.append(session_id, "GATE_DENY", {"reason_code": ReasonCode.NONCE_REPLAY,
                                               "stage": "precheck", "which": "token"})
        return InitiateOutcome(passed=False, reason_code=ReasonCode.NONCE_REPLAY, gate=result)

    if not nonce_store.consume(cart_nonce):
        audit.append(session_id, "GATE_DENY", {"reason_code": ReasonCode.NONCE_REPLAY,
                                               "stage": "atomic_consume", "which": "cart"})
        return InitiateOutcome(passed=False, reason_code=ReasonCode.NONCE_REPLAY, gate=result)
    if not nonce_store.consume(token_nonce):
        # Lost a race between the precheck and here. Best-effort release of the cart
        # nonce we just consumed so a legitimate retry of this cart isn't blocked.
        _release = getattr(nonce_store, "release", None)
        if callable(_release):
            try:
                _release(cart_nonce)
            except Exception:  # noqa: BLE001 — release is best-effort
                pass
        audit.append(session_id, "GATE_DENY", {"reason_code": ReasonCode.NONCE_REPLAY,
                                               "stage": "atomic_consume", "which": "token"})
        return InitiateOutcome(passed=False, reason_code=ReasonCode.NONCE_REPLAY, gate=result)

    # --- create the payment link (idempotent by session+cart_hash) ---
    idem = idempotency_key(session_id, inp.cart_hash)
    link = settlement_provider.create_payment_link(
        inp.cart_total_paise, idem, notes={"session_id": session_id, "reference_id": idem})
    audit.append(session_id, "LINK_CREATED",
                 {"payment_link_id": link.id, "amount_paise": inp.cart_total_paise,
                  "status": link.status})

    return InitiateOutcome(passed=True, reason_code=ReasonCode.OK,
                           payment_link_id=link.id, payment_link_url=link.short_url, gate=result)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
