"""Bridges the DB (merchant config, catalog) to the pure Offer Engine and back.

All engine logic stays pure in backend/offer/; all DB access lives here. The
engine PROPOSES → bounds.validate_offer DISPOSES → only a validated offer is
stored. The stored Offer row carries the priced cart_hash AND the added_cost so
that at /complete the gate can (a) verify cart integrity and (b) recompute the
REALIZED margin on the actual captured amount — the coupon-stacking backstop.

Standing promos are modelled with no schema change: a Product may carry
`effective_price_paise` and `promo_code` in its `attributes` JSONB.
"""

from __future__ import annotations

from sqlalchemy import select

from backend.gateway.cart import compute_cart_hash
from backend.models import LeverType, Merchant, MerchantConfig, Offer as OfferModel, Product
from backend.offer.bounds import validate_offer
from backend.offer.engine import (
    EngineProduct, NoOffer, Offer, build_offer, build_offer_asis_only,
    parse_band, parse_ceiling,
)


def active_merchant(db):
    return db.execute(select(Merchant).order_by(Merchant.created_at.desc()).limit(1)).scalars().first()


def active_config(db, merchant=None):
    """The active merchant's OWN latest config — not the globally highest version.

    Deep-review: without the merchant filter this returned whichever merchant had
    the highest version number anywhere, which only happened to be correct because
    the demo merchant had been PUT-updated more than any other. A stray merchant
    with a higher version would silently swap the policy that gates real checkouts.
    """
    merchant = merchant or active_merchant(db)
    if merchant is None:
        return None
    return db.execute(
        select(MerchantConfig)
        .where(MerchantConfig.merchant_id == merchant.id)
        .order_by(MerchantConfig.version.desc()).limit(1)
    ).scalars().first()


def _engine_product(p: Product) -> EngineProduct:
    attrs = p.attributes or {}
    return EngineProduct(
        sku=p.sku, category=p.category, list_price_paise=p.list_price_paise,
        cost_paise=p.cost_paise, stock=p.stock, stock_version=p.stock_version,
        return_days=p.return_days, shipping_days=p.shipping_days, title=p.title,
        effective_price_paise=int(attrs.get("effective_price_paise", 0) or 0),
        promo_code=str(attrs.get("promo_code", "") or ""),
    )


def _catalog(db, merchant_id=None) -> list[EngineProduct]:
    q = select(Product)
    if merchant_id is not None:
        q = q.where(Product.merchant_id == merchant_id)
    return [_engine_product(p) for p in db.execute(q).scalars().all()]


def _config_dict(cfg: MerchantConfig) -> dict:
    return {
        "margin_floor_bps": cfg.margin_floor_bps, "discount_budget_bps": cfg.discount_budget_bps,
        "return_band_min_days": cfg.return_band_min_days, "return_band_max_days": cfg.return_band_max_days,
        "shipping_upgrade_allowed": cfg.shipping_upgrade_allowed,
        "shipping_upgrade_max_cost_paise": cfg.shipping_upgrade_max_cost_paise,
        "bundle_enabled": cfg.bundle_enabled,
        "bundle_max_addon_categories": cfg.bundle_max_addon_categories,
        # allow_promo_stacking has no column yet -> defaults False (safe)
        "allow_promo_stacking": bool(getattr(cfg, "allow_promo_stacking", False)),
    }


def run_engine(db, ceiling_dict: dict, *, passive: bool):
    """Run the engine (or as-is-only for baseline). Returns (offer_or_none, reason, cart_hash)."""
    ceiling = parse_ceiling(ceiling_dict)
    cfg = active_config(db)
    merchant = active_merchant(db)
    catalog = _catalog(db, merchant.id if merchant else None)

    if passive or cfg is None:
        result = build_offer_asis_only(ceiling, catalog)
        band = None
    else:
        band = parse_band(_config_dict(cfg))
        result = build_offer(ceiling, band, catalog)

    if isinstance(result, NoOffer):
        return None, result.reason, None

    if band is not None:
        list_total = result.total_paise + result.total_discount_paise
        v = validate_offer(result, ceiling, band, list_price_total_paise=list_total)
        if not v.ok:
            return None, f"BOUNDS_REJECTED_{v.reason}", None

    cart_hash = compute_cart_hash(
        items=[{"sku": i.sku, "qty": i.qty, "unit_price_paise": i.unit_price_paise} for i in result.items],
        total_paise=result.total_paise, tax_paise=0, shipping_paise=0,
        return_terms_days=result.return_terms_days,
    )
    return result, "OK", cart_hash


def store_offer(db, session_id, offer: Offer, cart_hash: str):
    lever = LeverType(offer.lever_type) if offer.lever_type in LeverType._value2member_map_ else LeverType.NONE
    t = dict(offer.transformation)
    t.update({
        "cart_hash": cart_hash, "total_paise": offer.total_paise,
        "return_terms_days": offer.return_terms_days, "added_cost_paise": offer.added_cost_paise,
        "total_discount_paise": offer.total_discount_paise,
        "items": [{"sku": i.sku, "qty": i.qty, "unit_price_paise": i.unit_price_paise} for i in offer.items],
    })
    row = OfferModel(
        session_id=session_id, base_sku=offer.base_sku, lever_type=lever, transformation=t,
        computed_cost_paise=offer.computed_cost_paise, resulting_margin_bps=offer.resulting_margin_bps,
        within_bounds=True, chosen=True, reason=offer.reason)
    db.add(row)
    db.flush()
    return row


def load_chosen_offer(db, session_id):
    return db.execute(
        select(OfferModel).where(OfferModel.session_id == session_id, OfferModel.chosen == True)  # noqa: E712
        .order_by(OfferModel.created_at.desc()).limit(1)).scalars().first()
