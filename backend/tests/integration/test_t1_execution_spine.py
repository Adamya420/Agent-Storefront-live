"""T1 INTEGRATION checks (Payment Link model) — Supabase + Razorpay + Upstash.

Claude chat CANNOT run these. Run via Claude Code / human:
    pytest backend/tests/integration/test_t1_execution_spine.py -v -m integration

The full-payment test is SEMI-MANUAL by nature: a Payment Link must be completed
on Razorpay's hosted page (the test-mode stand-in for the autonomous settlement
rail). The test creates the link, prints its URL, and polls for up to
ACG_POLL_TIMEOUT seconds while you (or the Playwright autopay helper) complete it.
Set ACG_AUTOPAY=1 (with playwright installed) to make it hands-free.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.integration

from backend.audit.chain import AuditRow, verify_chain  # noqa: E402

POLL_TIMEOUT = int(os.getenv("ACG_POLL_TIMEOUT", "120"))
POLL_INTERVAL = 3


@pytest.fixture(scope="module")
def db():
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set — needs a real Supabase .env")
    from backend.db.session import get_session
    s = get_session()
    yield s
    s.close()


@pytest.fixture(scope="module")
def seeded(db):
    from backend.models import Merchant, MerchantConfig, Product
    from data.generate_catalog import generate_catalog
    merchant = Merchant(name="ACG Sports (T1 IT)")
    db.add(merchant); db.flush()
    db.add(MerchantConfig(merchant_id=merchant.id, version=1,
                          allowed_categories=["running_shoes", "socks"]))
    for p in generate_catalog(count=40, seed=42):
        if db.get(Product, p.sku):
            continue
        db.add(Product(sku=p.sku, merchant_id=merchant.id, title=p.title, category=p.category,
                       list_price_paise=p.list_price_paise, cost_paise=p.cost_paise, stock=p.stock,
                       stock_version=p.stock_version, return_days=p.return_days,
                       shipping_days=p.shipping_days, attributes=p.attributes, media=p.media,
                       description=p.description))
    db.commit()
    yield merchant


def _client():
    from fastapi.testclient import TestClient
    from backend.api.main import app
    return TestClient(app)


def _issue_intent(min_return=21, max_price=500000):
    from backend.common.keys import load_private_key
    from backend.common.mandates import sign_mandate
    from data.issue_mandate import build_intent_mandate
    payload = build_intent_mandate(
        category="running_shoes", max_price_paise=max_price, min_return_days=min_return,
        max_delivery_days=3, quantity=1, tolerate=["return", "bundle"],
        allowed_merchants=["merchant://acg-sports"], buyer_id="did:acg:it-user",
        agent_id="agent://it", ttl_minutes=15)
    return sign_mandate(payload, load_private_key("user-test-1"), kid="user-test-1")


def test_full_purchase_via_link_and_poll(db, seeded):
    """PROVES: gate → link created → (hosted pay) → poll finalizes → real payment."""
    from scripts.sign_cart import main as sign_cart_main
    client = _client()
    intent = _issue_intent(min_return=21)
    r = client.post("/acp/checkout_sessions", json={"intent_mandate_jws": intent})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["offer"] is not None, body
    session_id, offer = body["session_id"], body["offer"]

    sign_cart_main(["--sku", offer["base_sku"], "--price", str(offer["unit_price_paise"]),
                    "--return-days", str(offer["return_days"]), "--session", session_id,
                    "--out-cart", "/tmp/cart.jws", "--out-token", "/tmp/token.jws"])

    r2 = client.post(f"/acp/checkout_sessions/{session_id}/complete", json={
        "cart_mandate_jws": open("/tmp/cart.jws").read(),
        "delegated_token_jws": open("/tmp/token.jws").read()})
    assert r2.status_code == 200, r2.text
    init = r2.json()
    assert init["status"] == "PENDING_PAYMENT", init
    assert init["payment_link_url"], "no payment link url returned"

    print(f"\n>>> COMPLETE THIS PAYMENT LINK (test method) within {POLL_TIMEOUT}s: "
          f"{init['payment_link_url']}")
    if os.getenv("ACG_AUTOPAY") == "1":
        _autopay(init["payment_link_url"])

    deadline = time.time() + POLL_TIMEOUT
    final = None
    while time.time() < deadline:
        pr = client.post(f"/acp/checkout_sessions/{session_id}/poll").json()
        if pr["status"] == "CONVERTED":
            final = pr; break
        time.sleep(POLL_INTERVAL)
    assert final is not None, f"link not paid within {POLL_TIMEOUT}s (complete it and re-run)"
    assert final["razorpay_payment_id"], "captured but no payment id"
    print(f">>> CONFIRM IN RAZORPAY TEST DASHBOARD: payment {final['razorpay_payment_id']}")


def test_purchase_reconstructable_from_audit_chain(db, seeded):
    """PROVES: audit chain verifies intact AND contains the full purchase path."""
    from backend.models import AuditLog
    rows = db.execute(select(AuditLog).order_by(AuditLog.seq)).scalars().all()
    assert rows, "no audit rows"
    chain = [AuditRow(seq=r.seq, actor=r.actor, action=r.action, detail=r.detail,
                      prev_hash=r.prev_hash, hash=r.hash) for r in rows]
    ok, bad = verify_chain(chain)
    assert ok, f"audit chain broken at seq {bad}"
    actions = {r.action for r in rows}
    for expected in ("MANDATE_VERIFY", "OFFER", "GATE_PASS", "LINK_CREATED", "SETTLEMENT"):
        assert expected in actions, f"missing audit action {expected}: {sorted(actions)}"


def test_poll_is_idempotent_no_double_capture(db, seeded):
    """PROVES: /poll after CONVERTED does not re-capture."""
    from backend.models import Payment, PaymentStatus, Session as S, SessionStatus
    converted = db.execute(select(S).where(S.status == SessionStatus.CONVERTED)).scalars().first()
    if converted is None:
        pytest.skip("no converted session yet (run the full-purchase test first)")
    client = _client()
    before = db.execute(select(Payment).where(Payment.status == PaymentStatus.CAPTURED)).scalars().all()
    client.post(f"/acp/checkout_sessions/{converted.id}/poll")
    db.expire_all()
    after = db.execute(select(Payment).where(Payment.status == PaymentStatus.CAPTURED)).scalars().all()
    assert len(after) == len(before), "poll re-captured — idempotency broken"


def test_injection_sku_denied_no_money(db, seeded):
    """PROVES: injection SKU's over-ceiling attempt refused, no link, no money."""
    from scripts.sign_cart import main as sign_cart_main
    client = _client()
    intent = _issue_intent(min_return=0, max_price=500000)
    session_id = client.post("/acp/checkout_sessions", json={"intent_mandate_jws": intent}).json()["session_id"]
    sign_cart_main(["--sku", "ACG-SEED-INJECT-001", "--price", "899900", "--return-days", "30",
                    "--session", session_id, "--out-cart", "/tmp/ci.jws", "--out-token", "/tmp/ti.jws"])
    r = client.post(f"/acp/checkout_sessions/{session_id}/complete", json={
        "cart_mandate_jws": open("/tmp/ci.jws").read(),
        "delegated_token_jws": open("/tmp/ti.jws").read()}).json()
    assert r["status"] == "DENIED" and r["reason_code"] == "INJECTION_REFUSED", r
    assert r["payment_link_url"] is None


def test_expired_mandate_denied(db, seeded):
    from backend.common.keys import load_private_key
    from backend.common.mandates import sign_mandate
    from data.issue_mandate import build_intent_mandate
    payload = build_intent_mandate(
        category="running_shoes", max_price_paise=500000, min_return_days=21, max_delivery_days=3,
        quantity=1, tolerate=["return"], allowed_merchants=["merchant://acg-sports"],
        buyer_id="did:acg:it", agent_id="agent://it", ttl_minutes=15)
    payload = payload.model_copy(update={"expiry": datetime.now(timezone.utc)})
    intent = sign_mandate(payload, load_private_key("user-test-1"), kid="user-test-1")
    r = _client().post("/acp/checkout_sessions", json={"intent_mandate_jws": intent}).json()
    assert r["reason_code"] == "MANDATE_EXPIRED", r


def test_replayed_nonce_denied_against_real_upstash(db, seeded):
    from backend.gateway.nonce import get_nonce_store
    store = get_nonce_store()
    nonce = f"it-{uuid.uuid4().hex}"
    assert store.consume(nonce) is True
    assert store.consume(nonce) is False
    assert store.is_fresh(nonce) is False


def _autopay(url: str) -> None:
    """Optional Playwright helper: complete the hosted test link hands-free,
    matching the production model where the settlement rail fires automatically.
    Requires: pip install playwright && playwright install chromium."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed; complete the link manually")
        return
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url)
        try:
            page.click("text=UPI", timeout=10000)
            page.fill("input[name='vpa']", "success@razorpay")
            page.click("text=Verify and Pay", timeout=10000)
            page.wait_for_timeout(5000)
        except Exception as exc:
            print(f"autopay could not drive the page ({exc}); complete manually")
        browser.close()
