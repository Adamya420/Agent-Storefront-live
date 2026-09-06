"""Offer-envelope preview (Console, Merchant lens) — PURE, no DB, no LLM.

When a merchant onboards a SKU, the console shows the *bounded envelope* of offers
the deterministic engine could legally make on it: for a battery of representative
buyer intents, what does build_offer return? This is the mirror of the buyer's
Authorization Theater — the buyer view shows the space they signed; this shows the
space the merchant authorized the engine to move within. Same engine, same bounds,
nothing outside them.

DETERMINISM RULE: reuses the pure backend.offer.engine only. No LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.offer.engine import (
    Ceiling, EngineProduct, MerchantBand, NoOffer, Offer, Tolerances, build_offer,
)


@dataclass(frozen=True)
class EnvelopeEntry:
    label: str
    intent: dict
    lever: str                       # NONE|RETURN_EXTENSION|SHIPPING_UPGRADE|BUNDLE|DISCOUNT|MULTI|NO_OFFER
    reachable: bool
    total_paise: int | None = None
    return_days: int | None = None
    delivery_days: int | None = None
    margin_bps: int | None = None
    cost_paise: int | None = None
    reason: str = ""


def _all_tol() -> Tolerances:
    return Tolerances(return_ok=True, bundle_ok=True, discount_ok=True, shipping_upgrade_ok=True)


def _ceiling(cat, price, ret, deliv, qty=1) -> Ceiling:
    return Ceiling(category=cat, max_price_paise=price, min_return_days=ret,
                   max_delivery_days=deliv, quantity=qty, tolerances=_all_tol())


def default_intents(base: EngineProduct, band: MerchantBand) -> list[tuple[str, Ceiling]]:
    """A battery of representative buyer intents derived from the SKU itself, chosen
    to exercise each lever and the boundary where the engine must decline."""
    sell = base.sell_price_paise
    cat = base.category
    loose_del = max(base.shipping_days, 3)
    return [
        ("As-is buyer", _ceiling(cat, sell, base.return_days, loose_del)),
        ("Wants +returns (in band)", _ceiling(cat, sell, min(band.return_band_max_days, base.return_days + 4), loose_del)),
        ("Returns beyond band", _ceiling(cat, sell, band.return_band_max_days + 3, loose_del)),
        ("Tighter budget", _ceiling(cat, max(1, sell - 30000), base.return_days, loose_del)),
        ("Faster delivery", _ceiling(cat, sell, base.return_days, max(1, base.shipping_days - 1))),
        ("Bundle-friendly", _ceiling(cat, sell + 60000, base.return_days, loose_del)),
    ]


def compute_offer_envelope(base: EngineProduct, band: MerchantBand,
                           catalog: list[EngineProduct],
                           intents: list[tuple[str, Ceiling]] | None = None) -> list[EnvelopeEntry]:
    """For each representative intent, what legal offer can the engine make on `base`?
    `catalog` should include `base` plus any bundle addon SKUs so BUNDLE is reachable.
    """
    intents = intents or default_intents(base, band)
    out: list[EnvelopeEntry] = []
    for label, ceiling in intents:
        intent_view = {
            "max_price_paise": ceiling.max_price_paise,
            "min_return_days": ceiling.min_return_days,
            "max_delivery_days": ceiling.max_delivery_days,
        }
        res = build_offer(ceiling, band, catalog)
        if isinstance(res, NoOffer):
            out.append(EnvelopeEntry(label, intent_view, "NO_OFFER", False, reason=res.reason))
        elif isinstance(res, Offer) and res.base_sku == base.sku:
            out.append(EnvelopeEntry(
                label, intent_view, res.lever_type, True, total_paise=res.total_paise,
                return_days=res.return_terms_days, delivery_days=res.delivery_days,
                margin_bps=res.resulting_margin_bps, cost_paise=res.computed_cost_paise,
                reason=res.reason))
        else:
            # engine chose a different SKU for this intent (e.g. a cheaper recovery);
            # honest label: this SKU is not the one served here.
            out.append(EnvelopeEntry(label, intent_view, "OTHER_SKU_SERVED", False,
                                     reason=f"engine served {res.base_sku}"))
    return out
