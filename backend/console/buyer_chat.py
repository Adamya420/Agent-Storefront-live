"""Buyer lens chat backend (backend/console/buyer_chat.py).

The Buyer lens is a chat surface: the user types requirements the way they'd prompt
a real shopping agent ("running shoes under ₹5,000, at least 18-day returns"), and
the stand-in agent shops, receives the engine's offer, and returns an invoice.

Design boundary that keeps this safe:
  * extract_intent() is PURE and deterministic — it turns text into a structured
    intent (unit-tested). This is the ONLY new logic here.
  * the actual purchase runs on the EXISTING, tested ACP flow (/acp/checkout_sessions
    → /complete → /poll). The chat does not re-implement the money path, so the pure
    decide_on_offer rule and the 11-check gate still bound every settlement — an
    injected prompt cannot buy outside the signed space.
  * Gemini only narrates the turn (optional, off the money path). rule #8: this is
    the buyer stand-in; its reasoning stays in this lens.
"""

from __future__ import annotations

import re

# ₹4,999 / Rs 4999 / 4999 rupees / "under 5000"
_PRICE = re.compile(r"(?:₹|rs\.?|inr)?\s*([0-9][0-9,]{2,})\s*(?:rupees|rs|inr)?", re.I)
_QTY = re.compile(r"\b([0-9]{1,2})\s*(?:pairs?|units?|items?|qty)\b", re.I)

_CATEGORIES = {
    "running_shoes": ["running shoe", "running shoes", "runner", "running"],
    "training_shoes": ["training shoe", "trainer", "gym shoe", "cross train"],
    "apparel": ["apparel", "shirt", "jacket", "clothing", "tee"],
    "socks": ["sock", "socks"],
    "accessories": ["accessor", "cap", "band", "bottle"],
}
_TOL = {
    "return": ["return", "returns", "refund"],
    "bundle": ["bundle", "combo", "add-on", "addon", "with socks"],
    "discount": ["discount", "deal", "cheaper", "lower price", "off"],
    "shipping_upgrade": ["faster", "expedite", "quicker", "rush", "sooner"],
}


_RETURN_PATTS = [
    r"([0-9]{1,3})\s*[- ]?day[s]?\s*(?:return|returns|refund)",      # "18-day returns"
    r"(?:return|returns|refund)[a-z ,]*?([0-9]{1,3})\s*day",         # "return should be 5 days"
    r"(?:min(?:imum)?\.?\s*return[a-z ]*?|return[a-z ]*?(?:of|window|period)[a-z ]*?)([0-9]{1,3})",
]
_DELIVERY_PATTS = [
    r"(?:deliver|delivery|ship|shipping|arrive)[a-z ,]*?([0-9]{1,2})\s*day",  # "delivered in 2 days"
    r"([0-9]{1,2})\s*[- ]?day[s]?\s*(?:delivery|shipping)",                   # "2-day delivery"
]


def _first(patterns, text):
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return int(m.group(1))
    return None


def detect_fields(text: str) -> dict:
    """Return ONLY the constraint fields this message actually mentions (partial).

    This is what makes conversational editing work: a follow-up like 'no, min
    return should be 5 days' detects just {min_return_days: 5} and leaves the rest
    of the standing intent untouched. Fresh parses start from defaults and apply
    this; edits apply it onto the prior intent.
    """
    t = (text or "").lower()
    out: dict = {}

    for cat, kws in _CATEGORIES.items():
        if any(k in t for k in kws):
            out["category"] = cat
            break

    prices = [int(m.group(1).replace(",", "")) for m in _PRICE.finditer(t)]
    prices = [p for p in prices if p >= 100]  # ignore days/qty
    if prices:
        out["max_price_paise"] = max(prices) * 100

    ret = _first(_RETURN_PATTS, t)
    if ret is not None:
        out["min_return_days"] = ret

    deliv = _first(_DELIVERY_PATTS, t)
    if deliv is not None:
        out["max_delivery_days"] = deliv

    qm = _QTY.search(t)
    if qm:
        out["quantity"] = max(1, int(qm.group(1)))

    tol = [name for name, kws in _TOL.items() if any(k in t for k in kws)]
    if tol:
        out["tolerate_add"] = tol  # additive; merged as a union
    return out


def _defaults() -> dict:
    return {"category": "running_shoes", "max_price_paise": 500000, "min_return_days": 0,
            "max_delivery_days": 7, "quantity": 1, "tolerate": ["return"]}


def _finalize(intent: dict) -> dict:
    """Derive tolerances from fields (a return constraint implies return tolerance)."""
    tol = list(intent.get("tolerate", []))
    if intent.get("min_return_days") and "return" not in tol:
        tol.append("return")
    intent["tolerate"] = tol or ["return"]
    return intent


def extract_intent(text: str) -> dict:
    """Fresh parse: defaults overlaid with whatever the message mentions."""
    intent = _defaults()
    det = detect_fields(text)
    add = det.pop("tolerate_add", [])
    intent.update(det)
    for a in add:
        if a not in intent["tolerate"]:
            intent["tolerate"].append(a)
    return _finalize(intent)


def merge_intent(prior: dict, text: str) -> dict:
    """Apply only the fields this message mentions ONTO the prior draft intent.

    Fixes the follow-up-edit bug: 'no, min return should be 5 days' updates just
    that field and keeps the standing price/category/etc. Tolerances are unioned.
    """
    merged = dict(prior)
    det = detect_fields(text)
    add = det.pop("tolerate_add", [])
    merged.update(det)
    tol = list(merged.get("tolerate", []))
    for a in add:
        if a not in tol:
            tol.append(a)
    merged["tolerate"] = tol
    return _finalize(merged)


def narrate(intent: dict) -> str:
    """A deterministic, human-readable read-back of the parsed intent (used when
    Gemini is not configured; the endpoint prefers a live Gemini narration)."""
    rupees = intent["max_price_paise"] // 100
    bits = [f"Looking for {intent['category'].replace('_', ' ')} under ₹{rupees:,}"]
    if intent["min_return_days"]:
        bits.append(f"with at least {intent['min_return_days']}-day returns")
    if intent["max_delivery_days"] < 7:
        bits.append(f"delivered within {intent['max_delivery_days']} days")
    if intent["quantity"] > 1:
        bits.append(f"×{intent['quantity']}")
    return " ".join(bits) + ". I'll only accept an offer inside this signed budget."
