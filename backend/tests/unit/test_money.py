"""Unit tests for backend/common/money.py — pure, no DB, no network."""

from decimal import Decimal

import pytest

from backend.common.money import (
    MoneyError,
    apply_bps,
    format_inr,
    margin_bps,
    paise_to_rupees_str,
    pct_to_bps,
    rupees_to_paise,
)


class TestRupeesToPaise:
    def test_int_and_str_and_decimal(self):
        assert rupees_to_paise(4799) == 479900
        assert rupees_to_paise("4799.00") == 479900
        assert rupees_to_paise(Decimal("4799.50")) == 479950

    def test_float_is_rejected(self):
        # The whole point of the paise rule: floats must never enter money paths.
        with pytest.raises(MoneyError, match="float is not accepted"):
            rupees_to_paise(4799.00)

    def test_half_up_rounding(self):
        assert rupees_to_paise("0.005") == 1
        assert rupees_to_paise("0.004") == 0


class TestFormatting:
    def test_paise_to_rupees_str(self):
        assert paise_to_rupees_str(479900) == "4799.00"
        assert paise_to_rupees_str(5) == "0.05"
        assert paise_to_rupees_str(-150) == "-1.50"

    def test_format_inr_groups_thousands(self):
        assert format_inr(479900) == "₹4,799.00"
        assert format_inr(0) == "₹0.00"


class TestMarginBps:
    def test_known_margin(self):
        # ₹4,799 price, ₹3,700 cost:
        #   (479900-370000)*10000 // 479900 = 1_099_000_000 // 479900 = 2290 bps (22.90%)
        assert margin_bps(479900, 370000) == 2290

    def test_zero_margin(self):
        assert margin_bps(100000, 100000) == 0

    def test_negative_margin_is_negative(self):
        # Selling below cost must report a NEGATIVE margin so a floor check rejects it.
        assert margin_bps(100000, 120000) < 0

    def test_floors_rather_than_rounds_up(self):
        # Fail-safe: a borderline margin must never round UP across a floor.
        m = margin_bps(300001, 255000)
        assert m == (300001 - 255000) * 10_000 // 300001

    def test_zero_price_rejected(self):
        with pytest.raises(MoneyError):
            margin_bps(0, 0)

    def test_bool_rejected(self):
        with pytest.raises(MoneyError):
            margin_bps(True, 1)


class TestApplyBps:
    def test_basic(self):
        assert apply_bps(479900, 800) == 38392  # 8% of ₹4,799

    def test_rounds_down(self):
        # Rounding down keeps a discount budget from being exceeded by a paisa.
        assert apply_bps(999, 1) == 0

    def test_negative_rejected(self):
        with pytest.raises(MoneyError):
            apply_bps(-1, 100)
        with pytest.raises(MoneyError):
            apply_bps(100, -1)


class TestPctToBps:
    def test_conversion(self):
        assert pct_to_bps(15) == 1500
        assert pct_to_bps("2.5") == 250

    def test_float_rejected(self):
        with pytest.raises(MoneyError):
            pct_to_bps(15.0)
