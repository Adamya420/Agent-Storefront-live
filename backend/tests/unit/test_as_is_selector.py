"""Unit tests for the T1 as-is offer selector (pure, no DB)."""

from backend.acp.schemas import CatalogProduct, select_as_is


def _ceiling(max_price=500000, min_return=21, max_delivery=3, qty=1, category="running_shoes"):
    return {
        "constraints": {
            "category": category,
            "max_price_paise": max_price,
            "min_return_days": min_return,
            "max_delivery_days": max_delivery,
            "quantity": qty,
        }
    }


def _p(sku, price, cost, stock=10, ret=21, ship=2, cat="running_shoes"):
    return CatalogProduct(sku, sku, cat, price, cost, stock, 1, ret, ship)


class TestSelectAsIs:
    def test_picks_highest_margin_satisfying(self):
        products = [
            _p("A", 400000, 340000),   # 15% margin
            _p("B", 400000, 300000),   # 25% margin — should win
            _p("C", 400000, 360000),   # 10% margin
        ]
        best, reason = select_as_is(products, _ceiling())
        assert reason == "OK" and best.sku == "B"

    def test_no_match_in_category(self):
        best, reason = select_as_is([_p("A", 100000, 80000, cat="socks")], _ceiling())
        assert best is None and reason == "NO_MATCH_IN_CATEGORY"

    def test_out_of_stock(self):
        best, reason = select_as_is([_p("A", 400000, 300000, stock=0)], _ceiling())
        assert best is None and reason == "OUT_OF_STOCK"

    def test_price_above_ceiling(self):
        best, reason = select_as_is([_p("A", 600000, 400000)], _ceiling(max_price=500000))
        assert best is None and reason == "PRICE_ABOVE_CEILING"

    def test_return_below_ceiling(self):
        # affordable + in stock, but return window too short → the RETURNS_ONLY_FAIL case
        best, reason = select_as_is([_p("A", 400000, 300000, ret=14)], _ceiling(min_return=21))
        assert best is None and reason == "RETURN_BELOW_CEILING"

    def test_delivery_above_ceiling(self):
        best, reason = select_as_is([_p("A", 400000, 300000, ship=7)], _ceiling(max_delivery=3))
        assert best is None and reason == "DELIVERY_ABOVE_CEILING"

    def test_diagnosis_precedence_is_deterministic(self):
        # A product failing BOTH price and returns reports PRICE first (checked earlier).
        best, reason = select_as_is([_p("A", 600000, 400000, ret=7)], _ceiling(max_price=500000, min_return=21))
        assert best is None and reason == "PRICE_ABOVE_CEILING"
