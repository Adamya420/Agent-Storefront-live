#!/usr/bin/env python3
"""Issue a signed AP2-shaped Intent Mandate — the STAND-IN USER WALLET (TRD §6.4).

Honesty note for the demo/README: in production this step happens inside the
user's AP2-compatible client, which signs with a hardware-backed key. Here we
simulate it with a local ES256 key. The buyer agent NEVER mints its own mandate;
it only relays what this issuer produced. That is the whole point — the ceiling
originates OUTSIDE the agent.

Usage:
    python data/issue_mandate.py \
        --category running_shoes --max-price 500000 \
        --min-return 21 --max-delivery 3 \
        --tolerate return,bundle \
        --merchant merchant://acg-sports --ttl 15
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.common.keys import load_private_key  # noqa: E402
from backend.common.mandates import (  # noqa: E402
    IntentConstraints,
    IntentMandatePayload,
    IntentTolerances,
    expiry_in,
    new_nonce,
    sign_mandate,
)

TOLERANCE_FLAGS = {
    "return": "return_ok",
    "bundle": "bundle_ok",
    "discount": "discount_ok",
    "shipping": "shipping_upgrade_ok",
}


def build_intent_mandate(
    *,
    category: str,
    max_price_paise: int,
    min_return_days: int,
    max_delivery_days: int,
    quantity: int,
    tolerate: list[str],
    allowed_merchants: list[str],
    buyer_id: str,
    agent_id: str,
    ttl_minutes: int,
) -> IntentMandatePayload:
    """Pure builder — unit-testable without touching disk or keys."""
    flags = {}
    for name in tolerate:
        name = name.strip().lower()
        if not name:
            continue
        if name not in TOLERANCE_FLAGS:
            raise ValueError(
                f"unknown tolerance {name!r}; valid: {', '.join(sorted(TOLERANCE_FLAGS))} "
                "(substitution is not permitted in v1)"
            )
        flags[TOLERANCE_FLAGS[name]] = True

    return IntentMandatePayload(
        buyer_id=buyer_id,
        agent_id=agent_id,
        constraints=IntentConstraints(
            category=category,
            max_price_paise=max_price_paise,
            min_return_days=min_return_days,
            max_delivery_days=max_delivery_days,
            quantity=quantity,
        ),
        tolerances=IntentTolerances(**flags),
        allowed_merchants=allowed_merchants,
        expiry=expiry_in(ttl_minutes),
        nonce=new_nonce(),
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Issue a signed Intent Mandate (stand-in user wallet)")
    p.add_argument("--category", required=True)
    p.add_argument("--max-price", type=int, required=True, help="in PAISE (e.g. 500000 = ₹5,000)")
    p.add_argument("--min-return", type=int, default=0, help="minimum return window in days")
    p.add_argument("--max-delivery", type=int, default=30, help="max delivery days")
    p.add_argument("--quantity", type=int, default=1)
    p.add_argument("--tolerate", default="", help="comma list: return,bundle,discount,shipping")
    p.add_argument("--merchant", action="append", default=None, help="allowed merchant id (repeatable)")
    p.add_argument("--buyer-id", default="did:acg:test-user-1")
    p.add_argument("--agent-id", default="agent://acg-buyer-standin")
    p.add_argument("--ttl", type=int, default=15, help="expiry in minutes")
    p.add_argument("--kid", default="user-test-1", help="signing key id (the USER's key)")
    p.add_argument("--out", default=None, help="write JWS to this file instead of stdout")
    args = p.parse_args(argv)

    payload = build_intent_mandate(
        category=args.category,
        max_price_paise=args.max_price,
        min_return_days=args.min_return,
        max_delivery_days=args.max_delivery,
        quantity=args.quantity,
        tolerate=args.tolerate.split(",") if args.tolerate else [],
        allowed_merchants=args.merchant or ["merchant://acg-sports"],
        buyer_id=args.buyer_id,
        agent_id=args.agent_id,
        ttl_minutes=args.ttl,
    )

    token = sign_mandate(payload, load_private_key(args.kid), kid=args.kid)

    if args.out:
        Path(args.out).write_text(token)
        print(f"Intent Mandate written to {args.out}")
    else:
        print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
