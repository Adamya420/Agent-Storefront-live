"""Bounds validator (Commerce plane, TRD §8) — the engine proposes, this disposes.

Every Offer is re-checked against buyer ceiling ∩ merchant band ∩ margin floor
BEFORE it leaves the module, including the ANTI-STACKING cap: the total price
concession (standing promo + engine discount) must fit inside the single
discount budget. Belt-and-suspenders with the engine and with gate checks 6/7.

DETERMINISM RULE: no LLM. Pure.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.common.money import apply_bps
from backend.offer.engine import Ceiling, MerchantBand, NoOffer, Offer


@dataclass(frozen=True)
class BoundsResult:
    ok: bool
    reason: str

    def __bool__(self) -> bool:
        return self.ok


def validate_offer(offer, ceiling: Ceiling, band: MerchantBand,
                   list_price_total_paise: int | None = None) -> BoundsResult:
    if offer is None or isinstance(offer, NoOffer):
        return BoundsResult(False, "NO_OFFER")

    # --- buyer signed ceiling ---
    if offer.total_paise > ceiling.max_price_paise:
        return BoundsResult(False, "OVER_MANDATE_PRICE")
    if offer.return_terms_days < ceiling.min_return_days:
        return BoundsResult(False, "RETURN_BELOW_CEILING")
    if offer.delivery_days > ceiling.max_delivery_days:
        return BoundsResult(False, "DELIVERY_ABOVE_CEILING")
    base_qty = sum(i.qty for i in offer.items if i.sku == offer.base_sku)
    if base_qty > ceiling.quantity:
        return BoundsResult(False, "QUANTITY_ABOVE_CEILING")

    # --- merchant band ---
    # Bounds the engine's MANUFACTURED return extensions. A product's native return
    # window (as-is / bundle) is the merchant's own policy and, being more generous
    # to the buyer than requested, is never a rejection reason (deep-review).
    if offer.lever_type in ("RETURN_EXTENSION", "MULTI") and offer.return_terms_days > band.return_band_max_days:
        return BoundsResult(False, "RETURN_ABOVE_BAND")

    # --- anti-stacking: TOTAL price concession within the single budget ---
    if offer.total_discount_paise > 0:
        # budget is on LIST price; caller passes list total when it differs from cart total
        list_total = list_price_total_paise or (offer.total_paise + offer.total_discount_paise)
        budget = apply_bps(list_total, band.discount_budget_bps)
        if offer.total_discount_paise > budget:
            return BoundsResult(False, "STACKED_DISCOUNT_OVER_BUDGET")

    # --- tolerances (engine should already respect these; verify) ---
    t = ceiling.tolerances
    if offer.lever_type == "RETURN_EXTENSION" and not t.return_ok:
        return BoundsResult(False, "RETURN_LEVER_NOT_TOLERATED")
    if offer.lever_type == "DISCOUNT" and not t.discount_ok:
        return BoundsResult(False, "DISCOUNT_LEVER_NOT_TOLERATED")
    if offer.lever_type == "SHIPPING_UPGRADE" and not t.shipping_upgrade_ok:
        return BoundsResult(False, "SHIPPING_LEVER_NOT_TOLERATED")
    if offer.lever_type == "BUNDLE" and not t.bundle_ok:
        return BoundsResult(False, "BUNDLE_NOT_TOLERATED")

    # --- margin floor (the no-loss guarantee) ---
    if offer.resulting_margin_bps < band.margin_floor_bps:
        return BoundsResult(False, "MARGIN_FLOOR_BLOCK")

    return BoundsResult(True, "OK")
