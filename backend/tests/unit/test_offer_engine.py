"""Unit tests for the deterministic Offer Engine (TRD §8) + anti-stacking. Pure."""

import pytest

from backend.offer.engine import (
    Ceiling, EngineProduct, MerchantBand, NoOffer, Offer, Tolerances,
    build_offer, realized_margin_bps,
)


def band(**o):
    base = dict(margin_floor_bps=1500, discount_budget_bps=800, return_band_min_days=14,
                return_band_max_days=18, shipping_upgrade_allowed=True,
                shipping_upgrade_max_cost_paise=15000, bundle_enabled=True,
                bundle_addon_categories=("socks",), allow_promo_stacking=False)
    base.update(o)
    return MerchantBand(**base)


def ceiling(**o):
    tol = o.pop("tol", {})
    base = dict(category="running_shoes", max_price_paise=500000, min_return_days=18,
                max_delivery_days=3, quantity=1)
    base.update(o)
    t = Tolerances(return_ok=tol.get("return_ok", True), bundle_ok=tol.get("bundle_ok", True),
                   discount_ok=tol.get("discount_ok", True),
                   shipping_upgrade_ok=tol.get("shipping_upgrade_ok", True))
    return Ceiling(tolerances=t, **base)


def prod(sku, price, cost, ret=21, ship=2, stock=10, cat="running_shoes", eff=0, promo=""):
    return EngineProduct(sku, cat, price, cost, stock, 1, ret, ship, title=sku,
                         effective_price_paise=eff, promo_code=promo)


class TestAsIs:
    def test_picks_highest_margin_satisfying(self):
        cat = [prod("A", 400000, 340000), prod("B", 400000, 300000), prod("C", 400000, 360000)]
        o = build_offer(ceiling(tol={"bundle_ok": False}), band(bundle_enabled=False), cat)
        assert isinstance(o, Offer) and o.base_sku == "B" and o.lever_type == "NONE"

    def test_asis_zero_concession(self):
        o = build_offer(ceiling(tol={"bundle_ok": False}), band(bundle_enabled=False),
                        [prod("A", 400000, 300000)])
        assert o.computed_cost_paise == 0 and o.reason == "as_is"


class TestReturnExtension:
    def test_reachable_extension_recovers(self):
        o = build_offer(ceiling(min_return_days=18), band(), [prod("R", 479900, 370000, ret=14)])
        assert isinstance(o, Offer) and o.lever_type == "RETURN_EXTENSION"
        assert o.return_terms_days == 18 and o.computed_cost_paise == 3839
        assert o.resulting_margin_bps == 2210 and o.added_cost_paise == 3839

    def test_unreachable_21_vs_band18(self):
        o = build_offer(ceiling(min_return_days=21), band(return_band_max_days=18),
                        [prod("R", 479900, 370000, ret=14)])
        assert isinstance(o, NoOffer) and o.reason == "RETURN_UNSERVABLE_IN_BAND"

    def test_unreachable_30_vs_band18(self):
        o = build_offer(ceiling(min_return_days=30), band(return_band_max_days=18),
                        [prod("R", 479900, 370000, ret=14)])
        assert isinstance(o, NoOffer) and o.reason == "RETURN_UNSERVABLE_IN_BAND"

    def test_extension_not_tolerated(self):
        o = build_offer(ceiling(min_return_days=18, tol={"return_ok": False}), band(),
                        [prod("R", 479900, 370000, ret=14)])
        assert isinstance(o, NoOffer)


class TestDiscount:
    def test_within_budget_recovers(self):
        o = build_offer(ceiling(max_price_paise=500000, min_return_days=18),
                        band(discount_budget_bps=1200), [prod("D", 550000, 400000)])
        assert isinstance(o, Offer) and o.lever_type == "DISCOUNT"
        assert o.total_paise == 500000 and o.transformation["discount_paise"] == 50000
        assert o.resulting_margin_bps == 2000  # margin on the reduced price, not double-counted

    def test_over_budget_no_offer(self):
        o = build_offer(ceiling(max_price_paise=500000, min_return_days=18),
                        band(discount_budget_bps=800), [prod("D", 550000, 400000)])
        assert isinstance(o, NoOffer)

    def test_discount_not_tolerated(self):
        o = build_offer(ceiling(max_price_paise=500000, min_return_days=18, tol={"discount_ok": False}),
                        band(discount_budget_bps=1200), [prod("D", 550000, 400000)])
        assert isinstance(o, NoOffer)


class TestMarginFloor:
    def test_discount_below_floor_blocked(self):
        o = build_offer(ceiling(max_price_paise=500000, min_return_days=18),
                        band(discount_budget_bps=2000, margin_floor_bps=1500),
                        [prod("T", 520000, 480000)])
        assert isinstance(o, NoOffer)  # margin_bps(500000,480000)=400 < 1500


class TestBundle:
    def test_accretive_bundle_preferred(self):
        cat = [prod("SHOE", 479900, 370000), prod("SOCK", 40000, 12000, cat="socks")]
        o = build_offer(ceiling(min_return_days=18), band(), cat)
        assert isinstance(o, Offer) and o.lever_type == "BUNDLE" and o.total_paise <= 500000
        assert o.total_paise - (370000 + 12000) > 479900 - 370000

    def test_non_accretive_bundle_not_chosen(self):
        cat = [prod("SHOE", 479900, 370000), prod("SOCK", 40000, 39000, cat="socks")]
        assert build_offer(ceiling(min_return_days=18), band(), cat).lever_type == "NONE"

    def test_bundle_not_tolerated(self):
        cat = [prod("SHOE", 479900, 370000), prod("SOCK", 40000, 12000, cat="socks")]
        o = build_offer(ceiling(min_return_days=18, tol={"bundle_ok": False}), band(), cat)
        assert o.lever_type == "NONE"


class TestNoOfferDiagnosis:
    def test_no_category_match(self):
        o = build_offer(ceiling(), band(), [prod("X", 100000, 80000, cat="socks")])
        assert isinstance(o, NoOffer) and o.reason == "NO_MATCH_IN_CATEGORY"

    def test_out_of_stock(self):
        assert build_offer(ceiling(), band(), [prod("X", 400000, 300000, stock=0)]).reason == "OUT_OF_STOCK"

    def test_cheapest_recovery_wins(self):
        cat = [prod("CHEAP", 479900, 370000, ret=16), prod("PRICEY", 479900, 370000, ret=10)]
        assert build_offer(ceiling(min_return_days=18), band(), cat).base_sku == "CHEAP"


class TestAntiStacking:
    """The revenue-loss cases: promo stacking, discount-on-discounted, coupon backstop."""

    def test_promo_item_asis_prices_off_effective(self):
        # shoe list ₹5,499 but on promo to ₹4,799 (effective) -> as-is fits ₹5,000 ceiling
        o = build_offer(ceiling(min_return_days=18, tol={"bundle_ok": False}), band(bundle_enabled=False),
                        [prod("P", 549900, 370000, eff=479900, promo="SUMMER")])
        assert isinstance(o, Offer) and o.lever_type == "NONE"
        assert o.total_paise == 479900                       # priced off effective, not list
        assert o.existing_discount_paise == 70000            # standing promo tracked
        assert o.transformation.get("promo_code") == "SUMMER"

    def test_no_discount_stack_on_promo_item_by_default(self):
        # effective ₹5,300 still over ₹5,000 ceiling; item already on promo -> engine must
        # NOT stack a fresh discount (default policy) -> NO_OFFER PROMO_STACK_BLOCKED
        o = build_offer(ceiling(max_price_paise=500000, min_return_days=18),
                        band(discount_budget_bps=3000),
                        [prod("P", 599900, 400000, eff=530000, promo="SUMMER")])
        assert isinstance(o, NoOffer) and o.reason == "PROMO_STACK_BLOCKED"

    def test_stacking_allowed_but_total_over_budget_blocked(self):
        # opt-in stacking, but promo(₹70) + needed discount would exceed the single budget
        # list 599900, effective 530000 (promo 69900), ceiling 500000 -> need +30000 = 99900 total
        # budget = 800 bps of 599900 = 47992 -> 99900 > 47992 -> blocked
        o = build_offer(ceiling(max_price_paise=500000, min_return_days=18),
                        band(discount_budget_bps=800, allow_promo_stacking=True),
                        [prod("P", 599900, 400000, eff=530000, promo="SUMMER")])
        assert isinstance(o, NoOffer) and o.reason == "STACKED_DISCOUNT_OVER_BUDGET"

    def test_stacking_allowed_within_budget_recovers(self):
        # same but a generous budget: promo 69900 + new 30000 = 99900 <= 2000 bps of 599900 = 119980
        o = build_offer(ceiling(max_price_paise=500000, min_return_days=18),
                        band(discount_budget_bps=2000, margin_floor_bps=1000, allow_promo_stacking=True),
                        [prod("P", 599900, 400000, eff=530000, promo="SUMMER")])
        assert isinstance(o, Offer) and o.lever_type == "DISCOUNT"
        assert o.total_discount_paise == 99900 and o.total_paise == 500000

    def test_non_price_lever_still_ok_on_promo_item(self):
        # promo'd item that fits price but fails returns -> return extension is fine
        # (doesn't touch price, so no stacking concern)
        o = build_offer(ceiling(min_return_days=18),
                        band(bundle_enabled=False),
                        [prod("P", 549900, 370000, ret=14, eff=479900, promo="SUMMER")])
        assert isinstance(o, Offer) and o.lever_type == "RETURN_EXTENSION"
        assert o.total_paise == 479900

    def test_realized_margin_after_coupon_backstop(self):
        # engine priced a return-extension offer at ₹4,799 (margin ~22%). A checkout
        # coupon later knocks ₹1,500 off -> realized margin on the ACTUAL captured
        # amount collapses below floor. This is what the gate recomputes.
        cogs = 370000
        added = 3839  # return extension cost
        priced_margin = realized_margin_bps(479900, cogs, added)
        assert priced_margin >= 1500                          # fine as priced
        realized_after_coupon = realized_margin_bps(479900 - 150000, cogs, added)
        assert realized_after_coupon < 1500                   # coupon stack -> below floor -> gate denies


class TestBandFilterAndFallback:
    """Deep-review: as-is pick must clear band+floor, with fallback, and a native
    over-band return window must not collapse a servable request to NO_OFFER."""

    def test_native_return_above_band_does_not_kill_servable_request(self):
        # The live repro: a high-margin SKU with a NATIVE 30d return (> band max 18)
        # must not win-then-get-rejected into NO_OFFER when in-band answers exist.
        cat = [
            prod("CHEAP", 350000, 260000, ret=14),        # ~25.7% margin, in band
            prod("MID", 479900, 370000, ret=14),          # ~22.9% margin, in band
            prod("INJECT", 899900, 520000, ret=30),       # ~42.2% margin, native 30d return
        ]
        o = build_offer(ceiling(max_price_paise=1000000, min_return_days=10, tol={"bundle_ok": False}),
                        band(bundle_enabled=False), cat)
        assert isinstance(o, Offer)                       # NOT NoOffer
        # the 30d native-return SKU is legal as-is (native policy, more generous than asked)
        # and highest margin, so it's a valid pick; the key point is we did NOT collapse.
        assert o.lever_type == "NONE"

    def test_below_floor_asis_pick_is_not_selected_over_compliant_one(self):
        # top margin candidate is below floor; a compliant one exists → pick the compliant.
        cat = [
            prod("LOWMARGIN", 400000, 395000, ret=18),    # ~1.25% margin, below 15% floor
            prod("GOOD", 400000, 300000, ret=18),         # 25% margin, clears floor
        ]
        o = build_offer(ceiling(tol={"bundle_ok": False}), band(bundle_enabled=False), cat)
        assert isinstance(o, Offer) and o.base_sku == "GOOD"

    def test_all_asis_below_floor_falls_through_not_below_floor_offer(self):
        cat = [prod("A", 400000, 396000, ret=18), prod("B", 400000, 395000, ret=18)]
        o = build_offer(ceiling(tol={"bundle_ok": False}), band(bundle_enabled=False), cat)
        # must not return a below-floor as-is offer; recovery can't lift native margin → NoOffer
        assert isinstance(o, NoOffer)

    def test_native_return_extension_lever_still_bounded_by_band(self):
        # a manufactured RETURN_EXTENSION beyond band max is still rejected (unchanged).
        from backend.offer.bounds import validate_offer
        c = ceiling(min_return_days=25)
        b = band(return_band_max_days=18)
        # product needs extending 14→25 but band caps at 18 → recovery can't reach → NoOffer
        o = build_offer(c, b, [prod("A", 400000, 300000, ret=14)])
        assert isinstance(o, NoOffer)


class TestWebhookParseHardening:
    def test_notes_null_does_not_crash(self):
        from backend.payments.webhook import parse_event
        payload = {"event": "payment.captured",
                   "payload": {"payment": {"entity": {"id": "pay_x", "status": "captured",
                                                       "amount": 4799, "notes": None}}}}
        evt = parse_event(payload)  # must not raise
        assert evt.payment_id == "pay_x" and evt.amount_paise == 4799

    def test_session_id_read_from_link_notes(self):
        from backend.payments.webhook import parse_event
        payload = {"event": "payment_link.paid",
                   "payload": {"payment_link": {"entity": {"id": "plink_1", "status": "paid",
                               "amount": 5000, "notes": {"session_id": "abc-123"}}}}}
        evt = parse_event(payload)
        assert evt.session_id == "abc-123"

    def test_empty_payload_is_safe(self):
        from backend.payments.webhook import parse_event
        evt = parse_event({})
        assert evt.session_id is None and evt.payment_link_id is None
