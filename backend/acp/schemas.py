"""ACP-shaped request/response schemas + the T1 as-is offer selector.

T1 has NO Offer Engine (that is T2). The "offer" here is simply the highest-margin
in-stock product that satisfies the mandate as-is, or NO_OFFER. This keeps the
execution spine (verify → gate → settle → audit) provable end-to-end before any
optimization logic exists.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from backend.common.money import margin_bps


# ---------------------------------------------------------------- API schemas


class CreateCheckoutRequest(BaseModel):
    intent_mandate_jws: str | None = None
    delegated_token_jws: str | None = None  # ACP-only path (dual input, TRD §6.5)


class OfferView(BaseModel):
    base_sku: str
    title: str
    unit_price_paise: int
    total_paise: int
    return_days: int
    shipping_days: int
    lever_type: str = "NONE"


class CreateCheckoutResponse(BaseModel):
    session_id: str
    status: str            # OPEN | DENIED
    reason_code: str
    offer: OfferView | None = None
    no_offer_reason: str | None = None


class CompleteCheckoutRequest(BaseModel):
    cart_mandate_jws: str
    delegated_token_jws: str


class CompleteCheckoutResponse(BaseModel):
    session_id: str
    status: str            # PENDING_PAYMENT | CONVERTED | DENIED
    reason_code: str
    razorpay_payment_id: str | None = None
    receipt_head_hash: str | None = None
    payment_link_id: str | None = None
    payment_link_url: str | None = None


# ---------------------------------------------------------------- as-is selector


@dataclass(frozen=True)
class CatalogProduct:
    sku: str
    title: str
    category: str
    list_price_paise: int
    cost_paise: int
    stock: int
    stock_version: int
    return_days: int
    shipping_days: int


def select_as_is(products: list[CatalogProduct], ceiling: dict) -> tuple[CatalogProduct | None, str]:
    """T1 offer: best in-stock product satisfying the mandate ceiling AS-IS.

    Returns (product, reason). If none satisfies, product is None and reason is a
    fixed taxonomy code describing the binding constraint — never free text.
    """
    c = ceiling["constraints"]
    satisfying = [
        p
        for p in products
        if p.category == c["category"]
        and p.stock >= c["quantity"]
        and p.list_price_paise <= c["max_price_paise"]
        and p.return_days >= c["min_return_days"]
        and p.shipping_days <= c["max_delivery_days"]
    ]
    if satisfying:
        best = max(satisfying, key=lambda p: margin_bps(p.list_price_paise, p.cost_paise))
        return best, "OK"

    # Diagnose the binding constraint for an honest NO_OFFER reason.
    in_category = [p for p in products if p.category == c["category"]]
    if not in_category:
        return None, "NO_MATCH_IN_CATEGORY"
    in_stock = [p for p in in_category if p.stock >= c["quantity"]]
    if not in_stock:
        return None, "OUT_OF_STOCK"
    affordable = [p for p in in_stock if p.list_price_paise <= c["max_price_paise"]]
    if not affordable:
        return None, "PRICE_ABOVE_CEILING"
    good_returns = [p for p in affordable if p.return_days >= c["min_return_days"]]
    if not good_returns:
        return None, "RETURN_BELOW_CEILING"
    return None, "DELIVERY_ABOVE_CEILING"
