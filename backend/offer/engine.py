"""Deterministic Offer Engine (Commerce plane, TRD §8) — with anti-stacking.

DETERMINISM RULE (AGENTS.md §4): no LLM anywhere in backend/offer/. Pure function
of (ceiling, merchant band, catalog) → Offer | NoOffer. Purity is what lets us
claim the revenue lever is *provably bounded*: the engine cannot emit an offer
that loses money or exceeds the buyer's signed authority — every guarantee here is
unit-tested with no DB and no network.

ANTI-STACKING (the 2 AM failure this design refuses to have):
  A margin floor computed against list price is a lie if the item is already on a
  standing promo, or if a checkout coupon lands on top of the engine's offer, or if
  the agent tries to discount an already-discounted item. Each source looks
  individually harmless and together they sell below cost. Defenses:

    1. ONE BUDGET. `discount_budget_bps` caps the SUM of all price concessions
       (standing promo + engine discount), measured against LIST price — never
       per-source.
    2. PROMO-AWARE. The engine prices off `effective_price_paise` (post standing
       promo) and treats (list - effective) as already-spent budget. By default it
       will NOT stack a discount lever on an already-promo'd item
       (allow_promo_stacking=False → NoOffer PROMO_STACK_BLOCKED); non-price levers
       (return/shipping) are still allowed because they don't touch price.
    3. GATE BACKSTOP. `realized_margin_bps(final_total, cogs, added_cost)` is what
       the gate recomputes on the ACTUAL captured amount at /complete, so a
       checkout coupon the engine never saw still cannot push a capture below the
       floor — it becomes a clean deny.

Levers: as-is · BUNDLE (accretive upsell) · RETURN_EXTENSION · SHIPPING_UPGRADE ·
DISCOUNT. Cost model is deterministic and config-overridable (no randomness).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from backend.common.money import apply_bps, margin_bps

RETURN_COST_BPS_PER_DAY = int(os.getenv("ACG_RETURN_COST_BPS_PER_DAY", "20"))
SHIPPING_UPGRADE_FLAT_PAISE = int(os.getenv("ACG_SHIPPING_UPGRADE_PAISE", "8000"))


# ---------------------------------------------------------------- types


@dataclass(frozen=True)
class EngineProduct:
    sku: str
    category: str
    list_price_paise: int          # MSRP / reference for the discount budget
    cost_paise: int
    stock: int
    stock_version: int
    return_days: int
    shipping_days: int
    title: str = ""
    effective_price_paise: int = 0  # current sell price (after any standing promo); 0 -> == list
    promo_code: str = ""

    @property
    def sell_price_paise(self) -> int:
        return self.effective_price_paise or self.list_price_paise

    @property
    def existing_discount_paise(self) -> int:
        return max(0, self.list_price_paise - self.sell_price_paise)


@dataclass(frozen=True)
class Tolerances:
    return_ok: bool = False
    bundle_ok: bool = False
    discount_ok: bool = False
    shipping_upgrade_ok: bool = False
    substitution_ok: bool = False


@dataclass(frozen=True)
class Ceiling:
    category: str
    max_price_paise: int
    min_return_days: int
    max_delivery_days: int
    quantity: int
    tolerances: Tolerances


@dataclass(frozen=True)
class MerchantBand:
    margin_floor_bps: int
    discount_budget_bps: int        # TOTAL price-concession budget (promo + engine), on LIST
    return_band_min_days: int
    return_band_max_days: int
    shipping_upgrade_allowed: bool
    shipping_upgrade_max_cost_paise: int
    bundle_enabled: bool
    bundle_addon_categories: tuple[str, ...] = ()
    allow_promo_stacking: bool = False   # default: never stack discount on a promo'd item


@dataclass(frozen=True)
class OfferItem:
    sku: str
    qty: int
    unit_price_paise: int


@dataclass(frozen=True)
class Offer:
    base_sku: str
    lever_type: str
    items: tuple[OfferItem, ...]
    total_paise: int
    return_terms_days: int
    delivery_days: int
    computed_cost_paise: int         # ranking cost (merchant give-up, incl. discount revenue)
    resulting_margin_bps: int
    added_cost_paise: int = 0        # real added costs (return/shipping) -> the gate needs this
    existing_discount_paise: int = 0  # standing promo already on the item
    total_discount_paise: int = 0    # promo + engine discount combined
    transformation: dict = field(default_factory=dict)
    reason: str = "OK"


@dataclass(frozen=True)
class NoOffer:
    reason: str


# ---------------------------------------------------------------- parsing


def parse_ceiling(ceiling_dict: dict) -> Ceiling:
    c = ceiling_dict["constraints"]
    t = ceiling_dict.get("tolerances", {})
    return Ceiling(
        category=c["category"], max_price_paise=c["max_price_paise"],
        min_return_days=c["min_return_days"], max_delivery_days=c["max_delivery_days"],
        quantity=c.get("quantity", 1),
        tolerances=Tolerances(
            return_ok=t.get("return_ok", False), bundle_ok=t.get("bundle_ok", False),
            discount_ok=t.get("discount_ok", False),
            shipping_upgrade_ok=t.get("shipping_upgrade_ok", False),
            substitution_ok=t.get("substitution_ok", False)),
    )


def parse_band(cfg: dict) -> MerchantBand:
    return MerchantBand(
        margin_floor_bps=cfg["margin_floor_bps"], discount_budget_bps=cfg["discount_budget_bps"],
        return_band_min_days=cfg["return_band_min_days"], return_band_max_days=cfg["return_band_max_days"],
        shipping_upgrade_allowed=cfg["shipping_upgrade_allowed"],
        shipping_upgrade_max_cost_paise=cfg["shipping_upgrade_max_cost_paise"],
        bundle_enabled=cfg["bundle_enabled"],
        bundle_addon_categories=tuple(cfg.get("bundle_max_addon_categories", []) or []),
        allow_promo_stacking=bool(cfg.get("allow_promo_stacking", False)),
    )


# ---------------------------------------------------------------- cost helpers


def expected_return_cost(price_paise: int, extra_days: int) -> int:
    if extra_days <= 0:
        return 0
    return apply_bps(price_paise, RETURN_COST_BPS_PER_DAY * extra_days)


def shipping_upgrade_cost() -> int:
    return SHIPPING_UPGRADE_FLAT_PAISE


def realized_margin_bps(final_total_paise: int, cogs_paise: int, added_cost_paise: int = 0) -> int:
    """Margin on the ACTUAL captured amount. The gate recomputes this on the real
    signed cart total (which already reflects any checkout coupon) so no stacked
    concession the engine never saw can push a capture below the floor."""
    return margin_bps(final_total_paise, cogs_paise + added_cost_paise)


# ---------------------------------------------------------------- as-is + bundle


def _satisfies_asis(p: EngineProduct, c: Ceiling) -> bool:
    return (
        p.category == c.category
        and p.stock >= c.quantity
        and p.sell_price_paise * c.quantity <= c.max_price_paise
        and p.return_days >= c.min_return_days
        and p.shipping_days <= c.max_delivery_days
    )


def _asis_offer(p: EngineProduct, c: Ceiling) -> Offer:
    total = p.sell_price_paise * c.quantity
    return Offer(
        base_sku=p.sku, lever_type="NONE",
        items=(OfferItem(p.sku, c.quantity, p.sell_price_paise),),
        total_paise=total, return_terms_days=p.return_days, delivery_days=p.shipping_days,
        computed_cost_paise=0, resulting_margin_bps=margin_bps(total, p.cost_paise * c.quantity),
        added_cost_paise=0, existing_discount_paise=p.existing_discount_paise * c.quantity,
        total_discount_paise=p.existing_discount_paise * c.quantity,
        transformation={"promo_code": p.promo_code} if p.promo_code else {}, reason="as_is",
    )


def _best_bundle(p: EngineProduct, c: Ceiling, band: MerchantBand, catalog: list[EngineProduct]) -> Offer | None:
    if not (band.bundle_enabled and c.tolerances.bundle_ok):
        return None
    addon_cats = band.bundle_addon_categories or ("socks", "accessories")
    addons = [a for a in catalog if a.category in addon_cats and a.stock >= 1 and a.sku != p.sku]
    if not addons:
        return None
    shoe_total = p.sell_price_paise * c.quantity
    shoe_margin_abs = shoe_total - p.cost_paise * c.quantity
    best: Offer | None = None
    for a in addons:
        combined = shoe_total + a.sell_price_paise
        bundle_total = min(combined, c.max_price_paise)   # cap at ceiling -> the shown saving
        bundle_cost = p.cost_paise * c.quantity + a.cost_paise
        margin_val = margin_bps(bundle_total, bundle_cost)
        clamp_discount = max(0, combined - bundle_total)  # the saving handed to the buyer
        if (bundle_total - bundle_cost) > shoe_margin_abs and margin_val >= band.margin_floor_bps:
            cand = Offer(
                base_sku=p.sku, lever_type="BUNDLE",
                items=(OfferItem(p.sku, c.quantity, p.sell_price_paise), OfferItem(a.sku, 1, a.sell_price_paise)),
                total_paise=bundle_total, return_terms_days=p.return_days, delivery_days=p.shipping_days,
                computed_cost_paise=clamp_discount, resulting_margin_bps=margin_val,
                # record the clamp as a discount so validate_offer's anti-stacking budget
                # check actually bounds it (deep-review: the clamp bypassed the budget)
                total_discount_paise=clamp_discount,
                added_cost_paise=0, transformation={"addon_sku": a.sku, "list_total": combined},
                reason="bundle_accretive")
            if best is None or cand.resulting_margin_bps > best.resulting_margin_bps:
                best = cand
    return best


# ---------------------------------------------------------------- recovery


def _try_recover(p: EngineProduct, c: Ceiling, band: MerchantBand) -> Offer | tuple[None, str]:
    """Recover a near-miss within the authorized space. Returns an Offer, or
    (None, reason) where reason explains the block (for diagnosis)."""
    qty = c.quantity
    sell = p.sell_price_paise
    final_price = sell * qty
    final_return = p.return_days
    final_delivery = p.shipping_days
    added_cost = 0          # return/shipping: real added cost -> affects margin
    ranking_cost = 0        # merchant give-up for ranking (incl. discount revenue)
    existing_disc = p.existing_discount_paise * qty
    new_discount = 0
    levers: list[str] = []
    transform: dict = {}
    if p.promo_code:
        transform["promo_code"] = p.promo_code

    # price blocker -> DISCOUNT (price reduction; anti-stacking applies here)
    if sell * qty > c.max_price_paise:
        if not c.tolerances.discount_ok:
            return None, "DISCOUNT_NOT_TOLERATED"
        # never stack a fresh discount on an already-promo'd item unless allowed
        if existing_disc > 0 and not band.allow_promo_stacking:
            return None, "PROMO_STACK_BLOCKED"
        new_discount = sell * qty - c.max_price_paise
        total_discount = existing_disc + new_discount
        budget = apply_bps(p.list_price_paise * qty, band.discount_budget_bps)  # budget on LIST
        if total_discount > budget:
            return None, "STACKED_DISCOUNT_OVER_BUDGET"
        final_price = c.max_price_paise
        ranking_cost += new_discount
        levers.append("DISCOUNT")
        transform["discount_paise"] = new_discount

    # return blocker -> RETURN_EXTENSION (added cost, doesn't touch price)
    if p.return_days < c.min_return_days:
        if not c.tolerances.return_ok:
            return None, "RETURN_NOT_TOLERATED"
        target = c.min_return_days
        if target > band.return_band_max_days:
            return None, "RETURN_UNSERVABLE_IN_BAND"
        cost = expected_return_cost(sell, target - p.return_days)
        added_cost += cost
        ranking_cost += cost
        final_return = target
        levers.append("RETURN_EXTENSION")
        transform["return_to_days"] = target

    # delivery blocker -> SHIPPING_UPGRADE (added cost)
    if p.shipping_days > c.max_delivery_days:
        if not (c.tolerances.shipping_upgrade_ok and band.shipping_upgrade_allowed):
            return None, "SHIPPING_NOT_SERVABLE"
        cost = shipping_upgrade_cost()
        if cost > band.shipping_upgrade_max_cost_paise:
            return None, "SHIPPING_OVER_CAP"
        added_cost += cost
        ranking_cost += cost
        final_delivery = c.max_delivery_days
        levers.append("SHIPPING_UPGRADE")
        transform["delivery_to_days"] = c.max_delivery_days

    if not levers:
        return None, "NO_BLOCKER"

    total_cost = p.cost_paise * qty + added_cost
    resulting_margin = margin_bps(final_price, total_cost)
    if resulting_margin < band.margin_floor_bps:
        return None, "MARGIN_FLOOR_BLOCK"

    lever_type = levers[0] if len(levers) == 1 else "MULTI"
    return Offer(
        base_sku=p.sku, lever_type=lever_type,
        items=(OfferItem(p.sku, qty, final_price // qty if qty else final_price),),
        total_paise=final_price, return_terms_days=final_return, delivery_days=final_delivery,
        computed_cost_paise=ranking_cost, resulting_margin_bps=resulting_margin,
        added_cost_paise=added_cost, existing_discount_paise=existing_disc,
        total_discount_paise=existing_disc + new_discount, transformation=transform, reason="recovered",
    )


# ---------------------------------------------------------------- diagnosis


def _diagnose(c: Ceiling, catalog: list[EngineProduct], recover_reasons: list[str]) -> str:
    in_cat = [p for p in catalog if p.category == c.category]
    if not in_cat:
        return "NO_MATCH_IN_CATEGORY"
    in_stock = [p for p in in_cat if p.stock >= c.quantity]
    if not in_stock:
        return "OUT_OF_STOCK"
    # prefer a specific recovery-block reason if all candidates shared one
    for specific in ("PROMO_STACK_BLOCKED", "STACKED_DISCOUNT_OVER_BUDGET",
                     "MARGIN_FLOOR_BLOCK", "RETURN_UNSERVABLE_IN_BAND"):
        if specific in recover_reasons:
            return specific
    affordable = [p for p in in_stock if p.sell_price_paise * c.quantity <= c.max_price_paise]
    if not affordable:
        return "PRICE_ABOVE_CEILING"
    good_returns = [p for p in affordable if p.return_days >= c.min_return_days]
    if not good_returns:
        return "RETURN_UNSERVABLE_IN_BAND"
    return "DELIVERY_UNSERVABLE"


# ---------------------------------------------------------------- entry points


def _offer_clears_band(o: Offer, band: MerchantBand) -> bool:
    """Does an as-is/bundle candidate comply with the merchant's own band?

    Deep-review fixes: (1) as-is selection never checked the margin floor before
    ranking by margin, so a below-floor top pick could win then be rejected
    downstream with no fallback; (2) the return-band cap must bound only the
    engine's MANUFACTURED extensions — a product's NATIVE return window (more
    generous than the buyer asked for) is the merchant's own policy and must not
    be a rejection reason; (3) a ceiling-clamped bundle's discount must stay in
    the single discount budget.
    """
    if o.resulting_margin_bps < band.margin_floor_bps:
        return False
    if o.lever_type in ("RETURN_EXTENSION", "MULTI") and o.return_terms_days > band.return_band_max_days:
        return False
    if o.lever_type != "NONE" and o.total_discount_paise > 0:
        # budget bounds ENGINE-added concessions (bundle clamp, discount levers).
        # A pure as-is sale of a standing-promo item is the merchant's own baseline
        # pricing, not a stack, so it is not budget-checked here.
        list_total = o.total_paise + o.total_discount_paise
        if o.total_discount_paise > apply_bps(list_total, band.discount_budget_bps):
            return False
    return True


def build_offer(ceiling: Ceiling, band: MerchantBand, catalog: list[EngineProduct]):
    """The Offer Engine. Pure. Returns Offer or NoOffer."""
    satisfying = [p for p in catalog if _satisfies_asis(p, ceiling)]
    if satisfying:
        offers = []
        for p in satisfying:
            offers.append(_asis_offer(p, ceiling))
            b = _best_bundle(p, ceiling, band, catalog)
            if b is not None:
                offers.append(b)
        # Only rank band+floor-compliant candidates. This is the fallback the
        # reviewer flagged as missing: a high-margin but non-compliant top pick
        # no longer collapses an otherwise-servable request into NO_OFFER — the
        # engine simply chooses the best candidate that actually clears the band.
        legal = [o for o in offers if _offer_clears_band(o, band)]
        if legal:
            return max(legal, key=lambda o: o.resulting_margin_bps)
        # no as-is/bundle candidate clears the band → fall through to the recovery
        # levers (which may serve a different, near-miss product legally)

    candidates = [p for p in catalog if p.category == ceiling.category and p.stock >= ceiling.quantity]
    legal: list[Offer] = []
    reasons: list[str] = []
    for p in candidates:
        res = _try_recover(p, ceiling, band)
        if isinstance(res, Offer):
            legal.append(res)
        else:
            reasons.append(res[1])
    if not legal:
        return NoOffer(_diagnose(ceiling, catalog, reasons))
    return min(legal, key=lambda o: (o.computed_cost_paise, -o.resulting_margin_bps))


def build_offer_asis_only(ceiling: Ceiling, catalog: list[EngineProduct]):
    """Baseline 'passive merchant' path: best as-is satisfying product, or NoOffer."""
    satisfying = [p for p in catalog if _satisfies_asis(p, ceiling)]
    if not satisfying:
        return NoOffer(_diagnose(ceiling, catalog, []))
    best = max(satisfying, key=lambda p: margin_bps(p.sell_price_paise, p.cost_paise))
    return _asis_offer(best, ceiling)
