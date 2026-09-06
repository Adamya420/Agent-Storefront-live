"""Authorization Gate unit tests — the highest-priority suite (SKILL.md T1).

Every one of the 11 checks (TRD §7) is exercised on BOTH its pass and fail path,
plus ordering (a failure at check N is reported, not a later one) and the
injection attribution. Pure — no DB, no network.
"""

import dataclasses

import pytest

from backend.gateway.gate import GateContext, ReasonCode, authorize


def _ok_ctx(**overrides) -> GateContext:
    """A context where every check PASSES. Tests flip one field at a time."""
    base = dict(
        authorization_active=True,
        cart_sig_valid=True,
        token_sig_valid=True,
        authorization_expired=False,
        token_expired=False,
        cart_nonce_fresh=True,
        token_nonce_fresh=True,
        merchant_in_scope=True,
        category_in_scope=True,
        cart_total_paise=479900,
        ceiling_max_price_paise=500000,
        cart_return_days=21,
        ceiling_min_return_days=21,
        cart_delivery_days=2,
        ceiling_max_delivery_days=3,
        cart_quantity=1,
        ceiling_quantity=1,
        concession_within_band=True,
        resulting_margin_bps=2000,
        margin_floor_bps=1500,
        cart_hash="abc",
        priced_offer_hash="abc",
        stock_available=10,
        stock_version_at_offer=1,
        stock_version_now=1,
        token_max_amount_paise=500000,
        token_merchant_matches=True,
        token_consumed=False,
        existing_captured_payment=False,
        driving_sku_is_injection=False,
    )
    base.update(overrides)
    return GateContext(**base)


class TestAllPass:
    def test_clean_context_passes(self):
        r = authorize(_ok_ctx())
        assert r.passed is True
        assert r.reason_code == ReasonCode.OK
        assert r.check_index == 0


class TestCheck1MandateVerified:
    def test_pass(self):
        assert authorize(_ok_ctx(authorization_active=True)).passed

    def test_fail(self):
        r = authorize(_ok_ctx(authorization_active=False))
        assert not r.passed and r.reason_code == ReasonCode.MANDATE_NOT_VERIFIED and r.check_index == 1


class TestCheck2Signatures:
    def test_pass(self):
        assert authorize(_ok_ctx(cart_sig_valid=True, token_sig_valid=True)).passed

    def test_fail_cart_sig(self):
        r = authorize(_ok_ctx(cart_sig_valid=False))
        assert r.reason_code == ReasonCode.SIGNATURE_INVALID and r.check_index == 2

    def test_fail_token_sig(self):
        r = authorize(_ok_ctx(token_sig_valid=False))
        assert r.reason_code == ReasonCode.SIGNATURE_INVALID and r.check_index == 2


class TestCheck3Expiry:
    def test_pass(self):
        assert authorize(_ok_ctx(authorization_expired=False, token_expired=False)).passed

    def test_fail_mandate_expired(self):
        r = authorize(_ok_ctx(authorization_expired=True))
        assert r.reason_code == ReasonCode.MANDATE_EXPIRED and r.check_index == 3

    def test_fail_token_expired(self):
        r = authorize(_ok_ctx(token_expired=True))
        assert r.reason_code == ReasonCode.TOKEN_EXPIRED and r.check_index == 3


class TestCheck4Nonce:
    def test_pass(self):
        assert authorize(_ok_ctx(cart_nonce_fresh=True, token_nonce_fresh=True)).passed

    def test_fail_cart_nonce(self):
        r = authorize(_ok_ctx(cart_nonce_fresh=False))
        assert r.reason_code == ReasonCode.NONCE_REPLAY and r.check_index == 4

    def test_fail_token_nonce(self):
        r = authorize(_ok_ctx(token_nonce_fresh=False))
        assert r.reason_code == ReasonCode.NONCE_REPLAY and r.check_index == 4


class TestCheck5Scope:
    def test_pass(self):
        assert authorize(_ok_ctx(merchant_in_scope=True, category_in_scope=True)).passed

    def test_fail_merchant(self):
        r = authorize(_ok_ctx(merchant_in_scope=False))
        assert r.reason_code == ReasonCode.SCOPE_VIOLATION and r.check_index == 5

    def test_fail_category(self):
        r = authorize(_ok_ctx(category_in_scope=False))
        assert r.reason_code == ReasonCode.SCOPE_VIOLATION and r.check_index == 5


class TestCheck6MandateCeiling:
    def test_pass(self):
        assert authorize(_ok_ctx(cart_total_paise=500000, ceiling_max_price_paise=500000)).passed

    def test_fail_price_above_ceiling(self):
        r = authorize(_ok_ctx(cart_total_paise=500001, ceiling_max_price_paise=500000))
        assert r.reason_code == ReasonCode.PRICE_ABOVE_CEILING and r.check_index == 6

    def test_injection_attribution_on_price_breach(self):
        # Same breach, but the driving product is the injection SKU → special code.
        r = authorize(_ok_ctx(cart_total_paise=899900, ceiling_max_price_paise=500000,
                              driving_sku_is_injection=True))
        assert r.reason_code == ReasonCode.INJECTION_REFUSED and r.check_index == 6

    def test_fail_return_below_ceiling(self):
        r = authorize(_ok_ctx(cart_return_days=14, ceiling_min_return_days=21))
        assert r.reason_code == ReasonCode.RETURN_BELOW_CEILING and r.check_index == 6

    def test_fail_delivery_above_ceiling(self):
        r = authorize(_ok_ctx(cart_delivery_days=5, ceiling_max_delivery_days=3))
        assert r.reason_code == ReasonCode.DELIVERY_ABOVE_CEILING and r.check_index == 6

    def test_fail_quantity_above_ceiling(self):
        r = authorize(_ok_ctx(cart_quantity=2, ceiling_quantity=1))
        assert r.reason_code == ReasonCode.QUANTITY_ABOVE_CEILING and r.check_index == 6


class TestCheck7MerchantBandAndMargin:
    def test_pass(self):
        assert authorize(_ok_ctx(concession_within_band=True, resulting_margin_bps=1600,
                                margin_floor_bps=1500)).passed

    def test_fail_band(self):
        r = authorize(_ok_ctx(concession_within_band=False))
        assert r.reason_code == ReasonCode.MERCHANT_BAND_VIOLATION and r.check_index == 7

    def test_fail_margin_floor(self):
        r = authorize(_ok_ctx(resulting_margin_bps=1400, margin_floor_bps=1500))
        assert r.reason_code == ReasonCode.MARGIN_FLOOR_BLOCK and r.check_index == 7

    def test_margin_exactly_at_floor_passes(self):
        # Floor is a >= boundary: exactly at floor must PASS (not a loss).
        assert authorize(_ok_ctx(resulting_margin_bps=1500, margin_floor_bps=1500)).passed

    def test_defensive_recompute_catches_understated_margin(self):
        # resulting_margin_bps claims healthy, but price/cost recompute is below floor.
        # The gate takes the WORSE of the two and must block.
        r = authorize(_ok_ctx(resulting_margin_bps=3000, margin_floor_bps=1500,
                              recompute_price_paise=100000, recompute_cost_paise=90000))  # 1000 bps
        assert r.reason_code == ReasonCode.MARGIN_FLOOR_BLOCK and r.check_index == 7


class TestCheck8CartIntegrity:
    def test_pass(self):
        assert authorize(_ok_ctx(cart_hash="h1", priced_offer_hash="h1")).passed

    def test_fail(self):
        r = authorize(_ok_ctx(cart_hash="h1", priced_offer_hash="h2"))
        assert r.reason_code == ReasonCode.CART_INTEGRITY and r.check_index == 8


class TestCheck9Inventory:
    def test_pass(self):
        assert authorize(_ok_ctx(stock_available=5, cart_quantity=1,
                                stock_version_at_offer=3, stock_version_now=3)).passed

    def test_fail_stale_version(self):
        r = authorize(_ok_ctx(stock_version_at_offer=1, stock_version_now=2))
        assert r.reason_code == ReasonCode.INVENTORY_STALE and r.check_index == 9

    def test_fail_out_of_stock(self):
        r = authorize(_ok_ctx(stock_available=0, cart_quantity=1))
        assert r.reason_code == ReasonCode.OUT_OF_STOCK and r.check_index == 9


class TestCheck10TokenScope:
    def test_pass(self):
        assert authorize(_ok_ctx(token_max_amount_paise=500000, cart_total_paise=479900,
                                token_merchant_matches=True, token_consumed=False)).passed

    def test_fail_consumed(self):
        r = authorize(_ok_ctx(token_consumed=True))
        assert r.reason_code == ReasonCode.TOKEN_INVALID and r.check_index == 10

    def test_fail_wrong_merchant(self):
        r = authorize(_ok_ctx(token_merchant_matches=False))
        assert r.reason_code == ReasonCode.TOKEN_INVALID and r.check_index == 10

    def test_fail_amount_too_low(self):
        r = authorize(_ok_ctx(token_max_amount_paise=400000, cart_total_paise=479900))
        assert r.reason_code == ReasonCode.TOKEN_INVALID and r.check_index == 10


class TestCheck11Idempotency:
    def test_pass(self):
        assert authorize(_ok_ctx(existing_captured_payment=False)).passed

    def test_fail(self):
        r = authorize(_ok_ctx(existing_captured_payment=True))
        assert r.reason_code == ReasonCode.IDEMPOTENT_REPLAY and r.check_index == 11


class TestOrdering:
    def test_earlier_failure_reported_first(self):
        # Break checks 1 and 6 simultaneously; check 1 must win.
        r = authorize(_ok_ctx(authorization_active=False, cart_total_paise=999999999))
        assert r.check_index == 1

    def test_check6_before_check9(self):
        # Break 6 (price) and 9 (stock); 6 must win.
        r = authorize(_ok_ctx(cart_total_paise=999999999, stock_available=0))
        assert r.check_index == 6

    def test_result_is_immutable(self):
        r = authorize(_ok_ctx())
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.passed = False  # type: ignore[misc]
