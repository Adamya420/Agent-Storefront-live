#!/usr/bin/env python3
"""Build + sign a Cart Mandate and a Delegated Token for an end-to-end T1 flow.

This stands in for what the buyer agent (T2) will do autonomously: given an offer,
produce a signed Cart Mandate (agent counter-signature) and a scoped delegated
payment token. In T1 we drive it by hand to prove the execution spine.

Usage:
    python scripts/sign_cart.py --sku ACG-SEED-BUNDLE-SHOE-001 --price 479900 \
        --return-days 21 --session <session_id> --out-cart cart.jws --out-token token.jws
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.common.keys import load_private_key  # noqa: E402
from backend.common.mandates import (  # noqa: E402
    DelegatedTokenPayload,
    expiry_in,
    new_nonce,
    sign_mandate,
)
from backend.gateway.cart import build_cart_payload  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sku", required=True)
    p.add_argument("--price", type=int, required=True, help="unit price in paise")
    p.add_argument("--qty", type=int, default=1)
    p.add_argument("--return-days", type=int, required=True)
    p.add_argument("--session", required=True)
    p.add_argument("--merchant", default="merchant://acg-sports")
    p.add_argument("--agent-kid", default="agent-test-1")
    p.add_argument("--merchant-kid", default="merchant-test-1")
    p.add_argument("--ttl", type=int, default=15)
    p.add_argument("--out-cart", default="cart.jws")
    p.add_argument("--out-token", default="token.jws")
    args = p.parse_args(argv)

    total = args.price * args.qty
    cart_payload = build_cart_payload(
        session_id=args.session,
        items=[{"sku": args.sku, "qty": args.qty, "unit_price_paise": args.price}],
        total_paise=total, tax_paise=0, shipping_paise=0,
        return_terms_days=args.return_days, nonce=new_nonce(),
    )
    # Merchant signs the cart; in production the agent counter-signs. For T1 the
    # merchant signature is what the gate verifies.
    cart_jws = sign_mandate(cart_payload, load_private_key(args.merchant_kid), kid=args.merchant_kid)

    token_payload = DelegatedTokenPayload(
        max_amount_paise=total, merchant_id=args.merchant, expiry=expiry_in(args.ttl), nonce=new_nonce(),
    )
    token_jws = sign_mandate(token_payload, load_private_key(args.merchant_kid), kid=args.merchant_kid)

    Path(args.out_cart).write_text(cart_jws)
    Path(args.out_token).write_text(token_jws)
    print(f"cart  -> {args.out_cart}\ntoken -> {args.out_token}\ntotal_paise={total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
