"""Authorization Gate (Execution plane) — TRD §7.

DETERMINISM RULE (AGENTS.md §4): no LLM anywhere in this module. The gate is a
PURE function of its context: authorize(ctx) -> GateResult. No DB, no network, no
side effects. All state it needs (nonce freshness, inventory version, existing
payment, verified authorization) is fetched by the orchestration layer and passed
in. This is what makes every one of the 11 checks unit-testable by the planner
agent, and it is what lets us claim the money path is provably bounded.

Why pure + why side effects live elsewhere:
  * The nonce here is checked READ-ONLY (is it fresh?). The irreversible atomic
    CONSUME happens in the orchestration layer at settlement, AFTER a full PASS
    and AFTER inventory is confirmed — so a stale-stock retry never burns the
    authorization. This mirrors the reference repo's audited inventory-before-
    nonce fix and the TRD §7 note on check 9. (DECISIONS.md 2026-09-01.)

Check order is load-bearing (TRD §7) and must not be reordered without a
DECISIONS.md entry. Short-circuit on first failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.common.money import margin_bps


class ReasonCode:
    """Fixed taxonomy (TRD §7.3). Every deny maps to exactly one — never free text."""

    OK = "OK"
    # per-check failure codes, in check order
    MANDATE_NOT_VERIFIED = "MANDATE_NOT_VERIFIED"
    SIGNATURE_INVALID = "SIGNATURE_INVALID"
    MANDATE_EXPIRED = "MANDATE_EXPIRED"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    NONCE_REPLAY = "NONCE_REPLAY"
    SCOPE_VIOLATION = "SCOPE_VIOLATION"
    PRICE_ABOVE_CEILING = "PRICE_ABOVE_CEILING"
    RETURN_BELOW_CEILING = "RETURN_BELOW_CEILING"
    DELIVERY_ABOVE_CEILING = "DELIVERY_ABOVE_CEILING"
    QUANTITY_ABOVE_CEILING = "QUANTITY_ABOVE_CEILING"
    MERCHANT_BAND_VIOLATION = "MERCHANT_BAND_VIOLATION"
    MARGIN_FLOOR_BLOCK = "MARGIN_FLOOR_BLOCK"
    CART_INTEGRITY = "CART_INTEGRITY"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    INVENTORY_STALE = "INVENTORY_STALE"
    TOKEN_INVALID = "TOKEN_INVALID"
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"
    SESSION_MISMATCH = "SESSION_MISMATCH"
    SESSION_ALREADY_COMPLETED = "SESSION_ALREADY_COMPLETED"
    # The injection outcome is a specialization of PRICE_ABOVE_CEILING when the
    # driving product is the injection SKU — attributed by the orchestration layer.
    INJECTION_REFUSED = "INJECTION_REFUSED"


@dataclass(frozen=True)
class GateContext:
    """Everything the gate needs, pre-fetched. All money in paise.

    The gate NEVER reads a raw mandate or the DB — it reads this snapshot, whose
    `ceiling_*` fields come from the verified authorization row.
    """

    # check 1
    authorization_active: bool
    # check 2
    cart_sig_valid: bool
    token_sig_valid: bool
    # check 3
    authorization_expired: bool
    token_expired: bool
    # check 4 (read-only freshness; atomic consume happens post-PASS)
    cart_nonce_fresh: bool
    token_nonce_fresh: bool
    # check 5
    merchant_in_scope: bool
    category_in_scope: bool
    # check 6 — mandate ceiling
    cart_total_paise: int
    ceiling_max_price_paise: int
    cart_return_days: int
    ceiling_min_return_days: int
    cart_delivery_days: int
    ceiling_max_delivery_days: int
    cart_quantity: int
    ceiling_quantity: int
    # check 7 — merchant band + margin floor
    concession_within_band: bool
    resulting_margin_bps: int
    margin_floor_bps: int
    # check 8 — cart integrity
    cart_hash: str
    priced_offer_hash: str
    # check 9 — inventory
    stock_available: int
    stock_version_at_offer: int
    stock_version_now: int
    # check 10 — delegated token scope
    token_max_amount_paise: int
    token_merchant_matches: bool
    token_consumed: bool
    # check 11 — idempotency
    existing_captured_payment: bool
    # attribution only (not a check input): is the driving product the injection SKU?
    driving_sku_is_injection: bool = False
    # optional: precomputed price/cost for a defensive margin recompute
    recompute_price_paise: int | None = field(default=None)
    recompute_cost_paise: int | None = field(default=None)


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reason_code: str
    check_index: int  # 0 = OK/all passed; else the 1-based index of the failing check
    detail: str = ""

    def __bool__(self) -> bool:
        return self.passed


def _deny(reason: str, idx: int, detail: str = "") -> GateResult:
    return GateResult(passed=False, reason_code=reason, check_index=idx, detail=detail)


def authorize(ctx: GateContext) -> GateResult:
    """Run the 11 checks in TRD §7 order. Pure. Short-circuits on first failure."""

    # 1. MANDATE_VERIFIED — an ACTIVE authorization exists for this session.
    if not ctx.authorization_active:
        return _deny(ReasonCode.MANDATE_NOT_VERIFIED, 1, "no active authorization for session")

    # 2. SIGNATURE_OK — Cart Mandate + delegated token signatures valid.
    if not (ctx.cart_sig_valid and ctx.token_sig_valid):
        which = "cart" if not ctx.cart_sig_valid else "token"
        return _deny(ReasonCode.SIGNATURE_INVALID, 2, f"{which} signature invalid")

    # 3. NOT_EXPIRED — authorization + token not expired.
    if ctx.authorization_expired:
        return _deny(ReasonCode.MANDATE_EXPIRED, 3, "authorization/mandate expired")
    if ctx.token_expired:
        return _deny(ReasonCode.TOKEN_EXPIRED, 3, "delegated token expired")

    # 4. NONCE_FRESH — cart + token nonces unused (read-only; atomic consume later).
    if not (ctx.cart_nonce_fresh and ctx.token_nonce_fresh):
        return _deny(ReasonCode.NONCE_REPLAY, 4, "cart or token nonce already used")

    # 5. SCOPE_OK — merchant + category still in scope.
    if not (ctx.merchant_in_scope and ctx.category_in_scope):
        return _deny(ReasonCode.SCOPE_VIOLATION, 5, "merchant or category out of scope")

    # 6. WITHIN_MANDATE_CEILING — total/return/delivery/qty vs the signed ceiling.
    #    This is the check that refuses the injection SKU's over-ceiling attempt.
    if ctx.cart_total_paise > ctx.ceiling_max_price_paise:
        code = ReasonCode.INJECTION_REFUSED if ctx.driving_sku_is_injection else ReasonCode.PRICE_ABOVE_CEILING
        return _deny(code, 6, f"total {ctx.cart_total_paise} > ceiling {ctx.ceiling_max_price_paise}")
    if ctx.cart_return_days < ctx.ceiling_min_return_days:
        return _deny(ReasonCode.RETURN_BELOW_CEILING, 6, "return window below required minimum")
    if ctx.cart_delivery_days > ctx.ceiling_max_delivery_days:
        return _deny(ReasonCode.DELIVERY_ABOVE_CEILING, 6, "delivery slower than allowed")
    if ctx.cart_quantity > ctx.ceiling_quantity:
        return _deny(ReasonCode.QUANTITY_ABOVE_CEILING, 6, "quantity exceeds authorized")

    # 7. WITHIN_MERCHANT_BAND — concession inside band AND margin >= floor.
    if not ctx.concession_within_band:
        return _deny(ReasonCode.MERCHANT_BAND_VIOLATION, 7, "concession outside merchant band")
    margin = ctx.resulting_margin_bps
    if ctx.recompute_price_paise is not None and ctx.recompute_cost_paise is not None:
        # Defensive: recompute margin from price/cost and take the WORSE of the
        # two, so a mis-passed resulting_margin_bps cannot sneak under the floor.
        margin = min(margin, margin_bps(ctx.recompute_price_paise, ctx.recompute_cost_paise))
    if margin < ctx.margin_floor_bps:
        return _deny(ReasonCode.MARGIN_FLOOR_BLOCK, 7, f"margin {margin}bps < floor {ctx.margin_floor_bps}bps")

    # 8. CART_INTEGRITY — cart hash matches the offer the gate priced (no bait-and-switch).
    if ctx.cart_hash != ctx.priced_offer_hash:
        return _deny(ReasonCode.CART_INTEGRITY, 8, "cart changed since it was priced")

    # 9. INVENTORY_CONSISTENT — stock version unchanged and enough stock.
    if ctx.stock_version_now != ctx.stock_version_at_offer:
        return _deny(ReasonCode.INVENTORY_STALE, 9, "inventory changed since offer")
    if ctx.stock_available < ctx.cart_quantity:
        return _deny(ReasonCode.OUT_OF_STOCK, 9, "insufficient stock")

    # 10. TOKEN_SCOPE_OK — token amount covers total, merchant matches, not consumed.
    if ctx.token_consumed:
        return _deny(ReasonCode.TOKEN_INVALID, 10, "delegated token already consumed")
    if not ctx.token_merchant_matches:
        return _deny(ReasonCode.TOKEN_INVALID, 10, "token scoped to a different merchant")
    if ctx.token_max_amount_paise < ctx.cart_total_paise:
        return _deny(ReasonCode.TOKEN_INVALID, 10, "token max amount below cart total")

    # 11. IDEMPOTENT — no existing captured payment for this key.
    if ctx.existing_captured_payment:
        return _deny(ReasonCode.IDEMPOTENT_REPLAY, 11, "payment already captured for this key")

    return GateResult(passed=True, reason_code=ReasonCode.OK, check_index=0)
