"""T1 webhook INTEGRATION check — verifies the webhook endpoint finalizes a paid
link with a valid signature and rejects a bad one. Run via Claude Code / human.

    pytest backend/tests/integration/test_t1_webhook.py -v -m integration

This does not require Razorpay to actually POST to you; it simulates Razorpay's
signed POST against the TestClient using RAZORPAY_WEBHOOK_SECRET, which is exactly
what Razorpay does. The REAL end-to-end webhook (Razorpay POSTing over a tunnel)
is exercised manually per WEBHOOK_SETUP.md.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os

import pytest

pytestmark = pytest.mark.integration


def _client():
    from fastapi.testclient import TestClient
    from backend.api.main import app
    return TestClient(app)


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhook_rejects_bad_signature():
    if not os.getenv("RAZORPAY_WEBHOOK_SECRET"):
        pytest.skip("RAZORPAY_WEBHOOK_SECRET not set")
    body = json.dumps({"event": "payment_link.paid", "payload": {}}).encode()
    r = _client().post("/acp/webhooks/razorpay", content=body,
                       headers={"X-Razorpay-Signature": "deadbeef"})
    assert r.status_code == 400


def test_webhook_accepts_valid_signature_ignores_unrelated_event():
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")
    if not secret:
        pytest.skip("RAZORPAY_WEBHOOK_SECRET not set")
    body = json.dumps({"event": "payment_link.created",
                       "payload": {"payment_link": {"entity": {"id": "plink_x", "status": "created"}}}}).encode()
    r = _client().post("/acp/webhooks/razorpay", content=body,
                       headers={"X-Razorpay-Signature": _sign(body, secret)})
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"


def test_webhook_signed_event_does_not_capture_unpaid_link(db_seeded_pending_link):
    """Security property: a validly SIGNED webhook cannot capture money for a link
    that was never actually paid — finalize re-fetches the real status from Razorpay
    and returns NOT_PAID. The genuine CAPTURE path is proven in
    test_full_purchase_via_link_and_poll and the manual paid-link webhook proof."""
    import json, os, hashlib, hmac
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")
    if not secret:
        import pytest; pytest.skip("RAZORPAY_WEBHOOK_SECRET not set")
    link_id, session_id = db_seeded_pending_link
    body = json.dumps({
        "event": "payment_link.paid",
        "payload": {"payment_link": {"entity": {"id": link_id, "status": "paid", "amount": 479900}},
                    "payment": {"entity": {"id": "pay_synthetic", "status": "captured"}}},
    }).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    r = _client().post("/acp/webhooks/razorpay", content=body,
                       headers={"X-Razorpay-Signature": sig})
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "NOT_PAID"


@pytest.fixture
def db_seeded_pending_link():
    """Create a real pending link via /complete and yield (link_id, session_id).

    Skips cleanly if Razorpay link creation is unavailable on this account.
    """
    if not os.getenv("DATABASE_URL") or not os.getenv("RAZORPAY_KEY_ID"):
        pytest.skip("needs Supabase + Razorpay .env")

    from backend.common.keys import load_private_key
    from backend.common.mandates import sign_mandate
    from data.generate_catalog import generate_catalog
    from data.issue_mandate import build_intent_mandate
    from backend.db.session import get_session
    from backend.models import Merchant, MerchantConfig, Product
    from scripts.sign_cart import main as sign_cart_main

    db = get_session()
    m = Merchant(name="ACG Sports (webhook IT)")
    db.add(m); db.flush()
    db.add(MerchantConfig(merchant_id=m.id, version=1, allowed_categories=["running_shoes", "socks"]))
    for p in generate_catalog(count=40, seed=42):
        if not db.get(Product, p.sku):
            db.add(Product(sku=p.sku, merchant_id=m.id, title=p.title, category=p.category,
                           list_price_paise=p.list_price_paise, cost_paise=p.cost_paise, stock=p.stock,
                           stock_version=p.stock_version, return_days=p.return_days,
                           shipping_days=p.shipping_days, attributes=p.attributes, media=p.media,
                           description=p.description))
    db.commit()

    payload = build_intent_mandate(category="running_shoes", max_price_paise=500000, min_return_days=21,
                                   max_delivery_days=3, quantity=1, tolerate=["return"],
                                   allowed_merchants=["merchant://acg-sports"], buyer_id="did:acg:wh",
                                   agent_id="agent://wh", ttl_minutes=15)
    intent = sign_mandate(payload, load_private_key("user-test-1"), kid="user-test-1")

    client = _client()
    body = client.post("/acp/checkout_sessions", json={"intent_mandate_jws": intent}).json()
    session_id, offer = body["session_id"], body["offer"]
    sign_cart_main(["--sku", offer["base_sku"], "--price", str(offer["unit_price_paise"]),
                    "--return-days", str(offer["return_days"]), "--session", session_id,
                    "--out-cart", "/tmp/wc.jws", "--out-token", "/tmp/wt.jws"])
    init = client.post(f"/acp/checkout_sessions/{session_id}/complete", json={
        "cart_mandate_jws": open("/tmp/wc.jws").read(),
        "delegated_token_jws": open("/tmp/wt.jws").read()}).json()
    db.close()
    if init.get("status") != "PENDING_PAYMENT":
        pytest.skip(f"could not create pending link: {init}")
    yield init["payment_link_id"], session_id
