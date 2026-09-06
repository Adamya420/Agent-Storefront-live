"""Unit tests for the bounds validator and the pure buyer decision rule."""

import pytest

from backend.agent.buyer import decide_on_offer
from backend.offer.bounds import validate_offer
from backend.offer.engine import Ceiling, MerchantBand, NoOffer, Offer, OfferItem, Tolerances


def _ceiling(**o):
    tol = o.pop("tol", {})
    base = dict(category="running_shoes", max_price_paise=500000, min_return_days=18,
                max_delivery_days=3, quantity=1)
    base.update(o)
    t = Tolerances(return_ok=tol.get("return_ok", True), bundle_ok=tol.get("bundle_ok", True),
                   discount_ok=tol.get("discount_ok", True),
                   shipping_upgrade_ok=tol.get("shipping_upgrade_ok", True))
    return Ceiling(tolerances=t, **base)


def _band(**o):
    base = dict(margin_floor_bps=1500, discount_budget_bps=800, return_band_min_days=14,
                return_band_max_days=18, shipping_upgrade_allowed=True,
                shipping_upgrade_max_cost_paise=15000, bundle_enabled=True,
                bundle_addon_categories=("socks",), allow_promo_stacking=False)
    base.update(o)
    return MerchantBand(**base)


def _offer(**o):
    base = dict(base_sku="A", lever_type="NONE", items=(OfferItem("A", 1, 479900),),
                total_paise=479900, return_terms_days=18, delivery_days=2, computed_cost_paise=0,
                resulting_margin_bps=2000, added_cost_paise=0, existing_discount_paise=0,
                total_discount_paise=0, transformation={}, reason="ok")
    base.update(o)
    return Offer(**base)


class TestBoundsValidator:
    def test_valid_passes(self):
        assert validate_offer(_offer(), _ceiling(), _band()).ok

    def test_no_offer_fails(self):
        assert not validate_offer(NoOffer("X"), _ceiling(), _band())
        assert not validate_offer(None, _ceiling(), _band())

    def test_over_price(self):
        r = validate_offer(_offer(total_paise=500001), _ceiling(max_price_paise=500000), _band())
        assert not r.ok and r.reason == "OVER_MANDATE_PRICE"

    def test_return_below_ceiling(self):
        r = validate_offer(_offer(return_terms_days=14), _ceiling(min_return_days=18), _band())
        assert not r.ok and r.reason == "RETURN_BELOW_CEILING"

    def test_return_above_band(self):
        r = validate_offer(_offer(return_terms_days=25, lever_type="RETURN_EXTENSION"),
                           _ceiling(min_return_days=18), _band(return_band_max_days=18))
        assert not r.ok and r.reason == "RETURN_ABOVE_BAND"

    def test_delivery_above_ceiling(self):
        r = validate_offer(_offer(delivery_days=5), _ceiling(max_delivery_days=3), _band())
        assert not r.ok and r.reason == "DELIVERY_ABOVE_CEILING"

    def test_margin_floor_block(self):
        r = validate_offer(_offer(resulting_margin_bps=1400), _ceiling(), _band(margin_floor_bps=1500))
        assert not r.ok and r.reason == "MARGIN_FLOOR_BLOCK"

    def test_margin_at_floor_passes(self):
        assert validate_offer(_offer(resulting_margin_bps=1500), _ceiling(), _band(margin_floor_bps=1500)).ok

    def test_stacked_discount_over_budget(self):
        # total discount 100000 on a 600000 list; budget 800 bps = 48000 -> rejected
        off = _offer(lever_type="DISCOUNT", total_paise=500000, items=(OfferItem("A", 1, 500000),),
                     total_discount_paise=100000, transformation={"discount_paise": 30000})
        r = validate_offer(off, _ceiling(max_price_paise=500000), _band(discount_budget_bps=800),
                           list_price_total_paise=600000)
        assert not r.ok and r.reason == "STACKED_DISCOUNT_OVER_BUDGET"

    def test_stacked_discount_within_budget_ok(self):
        off = _offer(lever_type="DISCOUNT", total_paise=500000, items=(OfferItem("A", 1, 500000),),
                     total_discount_paise=40000, transformation={"discount_paise": 30000})
        assert validate_offer(off, _ceiling(max_price_paise=500000), _band(discount_budget_bps=800),
                              list_price_total_paise=600000).ok

    def test_lever_not_tolerated(self):
        r = validate_offer(_offer(lever_type="RETURN_EXTENSION"),
                           _ceiling(tol={"return_ok": False}), _band())
        assert not r.ok and r.reason == "RETURN_LEVER_NOT_TOLERATED"


class TestBuyerDecisionRule:
    def test_accepts_in_space(self):
        d = decide_on_offer(_offer(), _ceiling())
        assert d.accepted and d.reason == "IN_SPACE"

    def test_abandons_no_offer(self):
        assert decide_on_offer(NoOffer("X"), _ceiling()).action == "ABANDON"
        assert decide_on_offer(None, _ceiling()).action == "ABANDON"

    def test_never_signs_over_ceiling(self):
        d = decide_on_offer(_offer(total_paise=999999), _ceiling(max_price_paise=500000))
        assert d.action == "ABANDON" and d.reason == "OVER_CEILING"

    def test_abandons_return_too_short(self):
        d = decide_on_offer(_offer(return_terms_days=10), _ceiling(min_return_days=18))
        assert d.action == "ABANDON" and d.reason == "RETURN_TOO_SHORT"

    def test_abandons_delivery_too_slow(self):
        d = decide_on_offer(_offer(delivery_days=7), _ceiling(max_delivery_days=3))
        assert d.action == "ABANDON" and d.reason == "DELIVERY_TOO_SLOW"

    def test_abandons_quantity(self):
        d = decide_on_offer(_offer(items=(OfferItem("A", 3, 100000),), total_paise=300000),
                            _ceiling(quantity=1))
        assert d.action == "ABANDON" and d.reason == "QUANTITY_EXCEEDED"

    def test_accepts_recovered_offer(self):
        d = decide_on_offer(_offer(lever_type="RETURN_EXTENSION", return_terms_days=18),
                            _ceiling(min_return_days=18))
        assert d.accepted
