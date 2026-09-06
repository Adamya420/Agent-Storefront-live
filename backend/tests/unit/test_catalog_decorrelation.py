"""Unit tests for data/generate_catalog.py.

The decorrelation tests are the important ones. They exist to catch a FUTURE
regression: if someone later "improves" the generator by giving premium
categories shorter return windows (or any similar template bundling), any
downstream finding becomes a manufactured artifact. These tests fail loudly if
that happens.
"""

import statistics
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from data.generate_catalog import (  # noqa: E402
    CATEGORIES,
    INJECTION_TEXT,
    SKU_BUNDLE_SHOE,
    SKU_BUNDLE_SOCK,
    SKU_INJECTION,
    SKU_RETURNS_ONLY_FAIL,
    generate_catalog,
)


@pytest.fixture(scope="module")
def catalog():
    # Large sample so the statistical checks are meaningful, not noise.
    return generate_catalog(count=600, seed=42)


class TestDeterminism:
    def test_same_seed_same_catalog(self):
        a = generate_catalog(count=60, seed=7)
        b = generate_catalog(count=60, seed=7)
        assert [p.sku for p in a] == [p.sku for p in b]
        assert [p.list_price_paise for p in a] == [p.list_price_paise for p in b]

    def test_different_seed_differs(self):
        a = generate_catalog(count=60, seed=7)
        b = generate_catalog(count=60, seed=8)
        assert [p.list_price_paise for p in a] != [p.list_price_paise for p in b]


class TestSeededSpecials:
    def test_all_three_specials_present_exactly_once(self, catalog):
        skus = [p.sku for p in catalog]
        for sku in (SKU_RETURNS_ONLY_FAIL, SKU_BUNDLE_SHOE, SKU_BUNDLE_SOCK, SKU_INJECTION):
            assert skus.count(sku) == 1, f"{sku} must appear exactly once"

    def test_exactly_one_injection_sku(self, catalog):
        # "exactly one" is a SKILL.md T0 requirement — more than one would make the
        # adversarial test ambiguous about which SKU triggered the refusal.
        injected = [p for p in catalog if INJECTION_TEXT in p.description]
        assert len(injected) == 1
        assert injected[0].sku == SKU_INJECTION

    def test_returns_only_fail_product_shape(self, catalog):
        p = next(x for x in catalog if x.sku == SKU_RETURNS_ONLY_FAIL)
        # Must satisfy a typical ₹5,000 / 3-day intent on everything EXCEPT returns,
        # otherwise the T2 return-extension recovery demo has no clean case.
        assert p.list_price_paise <= 500000
        assert p.shipping_days <= 3
        assert p.return_days < 21
        assert p.stock > 0

    def test_bundle_pair_is_margin_accretive(self, catalog):
        shoe = next(x for x in catalog if x.sku == SKU_BUNDLE_SHOE)
        sock = next(x for x in catalog if x.sku == SKU_BUNDLE_SOCK)
        shoe_only_margin = shoe.list_price_paise - shoe.cost_paise
        # Bundle offered at ₹4,999 (under a ₹5,000 ceiling) must beat selling the
        # shoe alone, or the bundle lever would be a margin loss.
        bundle_price = 499900
        bundle_margin = bundle_price - (shoe.cost_paise + sock.cost_paise)
        assert bundle_price <= 500000
        assert bundle_margin > shoe_only_margin

    def test_injection_sku_priced_above_typical_ceiling(self, catalog):
        p = next(x for x in catalog if x.sku == SKU_INJECTION)
        # The attack is "ignore the budget" — so the SKU must exceed a typical
        # ₹5,000 ceiling for the refusal to actually mean something.
        assert p.list_price_paise > 500000


class TestIndependence:
    """Attributes must not be derivable from category (except price)."""

    @pytest.mark.parametrize("attr", ["return_days", "shipping_days"])
    def test_attribute_means_similar_across_categories(self, catalog, attr):
        randoms = [p for p in catalog if p.seed_role is None]
        by_cat = {c: [getattr(p, attr) for p in randoms if p.category == c] for c in CATEGORIES}
        means = [statistics.mean(v) for v in by_cat.values() if len(v) >= 20]
        assert len(means) >= 3, "not enough categories sampled"
        # If a template bundled attributes by category, category means would
        # separate sharply. Independent draws keep them close.
        assert max(means) - min(means) < 3.0, f"{attr} looks correlated with category: {by_cat.keys()}"

    def test_return_days_uses_full_range_in_every_category(self, catalog):
        randoms = [p for p in catalog if p.seed_role is None]
        for cat in CATEGORIES:
            vals = {p.return_days for p in randoms if p.category == cat}
            assert len(vals) >= 3, f"{cat} has suspiciously few distinct return windows: {vals}"

    def test_price_and_return_days_uncorrelated(self, catalog):
        randoms = [p for p in catalog if p.seed_role is None]
        prices = [p.list_price_paise for p in randoms]
        returns = [p.return_days for p in randoms]
        r = _pearson(prices, returns)
        # Independent draws → |r| near zero. A template would push this high.
        assert abs(r) < 0.15, f"price/return_days correlation too high: r={r:.3f}"

    def test_price_and_shipping_days_uncorrelated(self, catalog):
        randoms = [p for p in catalog if p.seed_role is None]
        r = _pearson([p.list_price_paise for p in randoms], [p.shipping_days for p in randoms])
        assert abs(r) < 0.15, f"price/shipping_days correlation too high: r={r:.3f}"


class TestInvariants:
    def test_cost_below_price_and_margin_positive(self, catalog):
        for p in catalog:
            assert 0 < p.cost_paise < p.list_price_paise, f"{p.sku} has non-positive margin"

    def test_all_money_is_int(self, catalog):
        for p in catalog:
            assert isinstance(p.list_price_paise, int) and not isinstance(p.list_price_paise, bool)
            assert isinstance(p.cost_paise, int) and not isinstance(p.cost_paise, bool)

    def test_categories_are_known(self, catalog):
        for p in catalog:
            assert p.category in CATEGORIES


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
    return 0.0 if den == 0 else num / den
