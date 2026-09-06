"""Money handling for ACG.

HARD RULE (AGENTS.md §4): all money is integer paise. Never floats.
₹1 = 100 paise. Every helper here is pure and takes/returns ints, except
formatting helpers which return strings for display only.

Basis points (bps) are used for margins and discount budgets:
    100 bps = 1.00%,  1500 bps = 15%.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

PAISE_PER_RUPEE = 100
BPS_DENOMINATOR = 10_000


class MoneyError(ValueError):
    """Raised when a money operation would be lossy or nonsensical."""


def rupees_to_paise(rupees: str | int | Decimal) -> int:
    """Convert a rupee amount to integer paise.

    Accepts str/int/Decimal only — NEVER float, because float cannot represent
    decimal currency exactly and this is the single most common money bug.
    """
    if isinstance(rupees, float):
        raise MoneyError("float is not accepted for money; pass str, int or Decimal")
    value = Decimal(str(rupees)) * PAISE_PER_RUPEE
    quantized = value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(quantized)


def paise_to_rupees_str(paise: int) -> str:
    """Format integer paise as a rupee string for DISPLAY ONLY (e.g. '4799.00')."""
    _require_int(paise, "paise")
    sign = "-" if paise < 0 else ""
    whole, frac = divmod(abs(paise), PAISE_PER_RUPEE)
    return f"{sign}{whole}.{frac:02d}"


def format_inr(paise: int) -> str:
    """Human display, e.g. '₹4,799.00'. Display only — never parsed back."""
    _require_int(paise, "paise")
    sign = "-" if paise < 0 else ""
    whole, frac = divmod(abs(paise), PAISE_PER_RUPEE)
    return f"{sign}₹{whole:,}.{frac:02d}"


def margin_bps(price_paise: int, cost_paise: int) -> int:
    """Margin in basis points of the selling price.

    margin_bps = (price - cost) / price * 10000, floored toward -inf so that a
    borderline case never rounds UP across a floor check (fail safe: we would
    rather reject a marginal offer than accept one below the floor).
    """
    _require_int(price_paise, "price_paise")
    _require_int(cost_paise, "cost_paise")
    if price_paise <= 0:
        raise MoneyError("price_paise must be positive to compute margin")
    numerator = (price_paise - cost_paise) * BPS_DENOMINATOR
    # floor division on a possibly-negative numerator floors toward -inf: correct here.
    return numerator // price_paise


def apply_bps(amount_paise: int, bps: int) -> int:
    """Return `bps` basis points of `amount_paise`, rounded DOWN.

    Rounding down is deliberate: used for discount budgets and cost ceilings,
    where rounding up would let an offer exceed an authorized bound by a paisa.
    """
    _require_int(amount_paise, "amount_paise")
    _require_int(bps, "bps")
    if amount_paise < 0:
        raise MoneyError("amount_paise must be non-negative")
    if bps < 0:
        raise MoneyError("bps must be non-negative")
    return (amount_paise * bps) // BPS_DENOMINATOR


def pct_to_bps(pct: str | int | Decimal) -> int:
    """Convert a percentage (e.g. '15' or 15) to basis points (1500)."""
    if isinstance(pct, float):
        raise MoneyError("float is not accepted; pass str, int or Decimal")
    value = Decimal(str(pct)) * 100
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _require_int(value: object, name: str) -> None:
    # bool is a subclass of int; reject it explicitly to catch argument-order bugs.
    if isinstance(value, bool) or not isinstance(value, int):
        raise MoneyError(f"{name} must be an int (paise/bps), got {type(value).__name__}")
