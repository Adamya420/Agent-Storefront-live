"""Cart hashing + Cart Mandate assembly (TRD §6).

cart_hash binds a payment to an EXACT cart. It is what gate check 8
(CART_INTEGRITY) compares, defeating bait-and-switch between the moment an offer
is priced and the moment it is completed. The hash must be deterministic and
independent of dict ordering, so it uses canonical_json.
"""

from __future__ import annotations

import hashlib

from backend.common.mandates import canonical_json


def compute_cart_hash(
    *,
    items: list[dict],
    total_paise: int,
    tax_paise: int,
    shipping_paise: int,
    return_terms_days: int,
) -> str:
    """Deterministic hash over the price-and-contents-defining fields of a cart.

    Items are normalized (sorted by sku, only the fields that define value) so an
    equivalent cart always hashes the same and a changed cart never does.
    """
    normalized_items = sorted(
        (
            {"sku": i["sku"], "qty": int(i["qty"]), "unit_price_paise": int(i["unit_price_paise"])}
            for i in items
        ),
        key=lambda i: i["sku"],
    )
    payload = {
        "items": normalized_items,
        "total_paise": int(total_paise),
        "tax_paise": int(tax_paise),
        "shipping_paise": int(shipping_paise),
        "return_terms_days": int(return_terms_days),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def build_cart_payload(
    *,
    session_id: str,
    items: list[dict],
    total_paise: int,
    tax_paise: int,
    shipping_paise: int,
    return_terms_days: int,
    nonce: str,
) -> dict:
    """The Cart Mandate payload the merchant signs (and the agent counter-signs)."""
    cart_hash = compute_cart_hash(
        items=items,
        total_paise=total_paise,
        tax_paise=tax_paise,
        shipping_paise=shipping_paise,
        return_terms_days=return_terms_days,
    )
    return {
        "type": "CartMandate",
        "session_id": session_id,
        "items": items,
        "total_paise": total_paise,
        "tax_paise": tax_paise,
        "shipping_paise": shipping_paise,
        "return_terms_days": return_terms_days,
        "cart_hash": cart_hash,
        "nonce": nonce,
    }
