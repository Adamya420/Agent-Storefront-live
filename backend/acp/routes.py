"""ACP HTTP routes (TRD §5). Integration-tested (needs DB + settlement).

T1 scope: /feed, /checkout_sessions create + get + complete. The 'offer' is the
as-is selector (backend/acp/schemas.select_as_is); the Offer Engine is T2.

Determinism note: no LLM here. This module orchestrates the pure verifier, pure
gate, and pure selector; all money decisions are made by those pure components.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from backend.acp.orchestration import CompleteInputs, run_initiate
from backend.acp.schemas import (
    CatalogProduct,
    CompleteCheckoutRequest,
    CompleteCheckoutResponse,
    CreateCheckoutRequest,
    CreateCheckoutResponse,
    OfferView,
    select_as_is,
)
from backend.audit.chain import AuditWriter
from backend.common.keys import resolve_public_key
from backend.common.mandates import parse_delegated_token
from backend.common.money import margin_bps
from backend.db.session import get_session
from backend.gateway.cart import compute_cart_hash
from backend.gateway.gate import ReasonCode
from backend.gateway.nonce import get_nonce_store
from backend.gateway.verify import build_ceiling, verify_intent_mandate
from backend.acp.offer_wire import active_config, load_chosen_offer, run_engine, store_offer
from backend.offer.engine import realized_margin_bps
from backend.payments.finalize import finalize_in_db
from backend.payments.webhook import is_paid_event, parse_event, verify_webhook_signature
from backend.models import (
    Authorization,
    AuthorizationStatus,
    CartMandate,
    DelegatedToken,
    IntentMandate,
    Payment,
    PaymentStatus,
    Product,
    Receipt,
    Session as SessionModel,
    SessionStatus,
    AgentType,
)
from backend.payments.settlement import get_settlement_provider

router = APIRouter(prefix="/acp", tags=["acp"])

MERCHANT_ID = os.getenv("ACG_MERCHANT_ID", "merchant://acg-sports")
ALLOWED_CATEGORIES = ["running_shoes", "training_shoes", "apparel", "socks", "accessories"]
MERCHANT_KID = os.getenv("MANDATE_MERCHANT_KID", "merchant-test-1")
# Only the user wallet may originate an Intent Mandate (buyer consent). Pinned so
# an agent/merchant key cannot self-sign its own ceiling.
USER_KID = os.getenv("MANDATE_USER_KID", "user-test-1")


@router.get("/feed")
def feed():
    """Agent-readable product feed (ACP-shaped)."""
    db = get_session()
    try:
        products = db.execute(select(Product)).scalars().all()
        return [
            {
                "id": p.sku,
                "title": p.title,
                "category": p.category,
                "price_paise": p.list_price_paise,
                "currency": "INR",
                "stock": p.stock,
                "return_days": p.return_days,
                "shipping_days": p.shipping_days,
                "media": p.media,
                "attributes": p.attributes,
            }
            for p in products
        ]
    finally:
        db.close()


@router.post("/checkout_sessions", response_model=CreateCheckoutResponse)
def create_checkout(req: CreateCheckoutRequest, passive: bool = False):
    """Verify the mandate, bind the authorization ceiling, return the as-is offer."""
    db = get_session()
    try:
        audit = AuditWriter(db, actor="acp")

        if not req.intent_mandate_jws:
            # Dual-input (TRD §6.5): ACP-only path would synthesize a minimal
            # mandate from the delegated token. Deferred detail; T1 requires a
            # full mandate for the happy path.
            raise HTTPException(400, "intent_mandate_jws required in T1")

        result = verify_intent_mandate(
            req.intent_mandate_jws,
            merchant_id=MERCHANT_ID,
            allowed_categories=ALLOWED_CATEGORIES,
            expected_signer_kid=USER_KID,
        )
        audit.append(None, "MANDATE_VERIFY",
                     {"ok": result.ok, "reason_code": result.reason_code})

        if not result.ok:
            db.commit()
            return CreateCheckoutResponse(session_id="", status="DENIED",
                                          reason_code=result.reason_code)

        payload = result.payload
        ceiling = build_ceiling(payload)

        mandate_row = IntentMandate(
            raw_jws=req.intent_mandate_jws, buyer_id=payload.buyer_id, agent_id=payload.agent_id,
            constraints=payload.constraints.model_dump(mode="json"),
            tolerances=payload.tolerances.model_dump(mode="json"),
            allowed_merchants=payload.allowed_merchants, expiry=payload.expiry,
            nonce=payload.nonce, verified=True, verify_reason="OK", mandate_source="ap2_full",
        )
        db.add(mandate_row)
        db.flush()

        auth = Authorization(intent_mandate_id=mandate_row.id, ceiling=ceiling,
                             status=AuthorizationStatus.ACTIVE)
        db.add(auth)
        db.flush()

        from backend.acp.offer_wire import active_merchant as _active_merchant
        _m = _active_merchant(db)
        session = SessionModel(intent_mandate_id=mandate_row.id, agent_type=AgentType.GEMINI,
                               status=SessionStatus.OPEN,
                               merchant_id=(_m.id if _m else None))
        db.add(session)
        db.flush()

        engine_offer, reason, cart_hash = run_engine(db, ceiling, passive=passive)

        if engine_offer is None:
            session.status = SessionStatus.ABANDONED
            session.outcome_reason = reason
            audit.append(session.id, "NO_OFFER", {"reason": reason, "passive": passive})
            db.commit()
            return CreateCheckoutResponse(session_id=str(session.id), status="OPEN",
                                          reason_code="OK", no_offer_reason=reason)

        store_offer(db, session.id, engine_offer, cart_hash)
        audit.append(session.id, "OFFER", {"sku": engine_offer.base_sku,
                                           "lever": engine_offer.lever_type,
                                           "total_paise": engine_offer.total_paise,
                                           "margin_bps": engine_offer.resulting_margin_bps,
                                           "added_cost_paise": engine_offer.added_cost_paise,
                                           "total_discount_paise": engine_offer.total_discount_paise,
                                           "passive": passive})
        db.commit()
        title = next((p.title for p in db.execute(select(Product)).scalars().all()
                      if p.sku == engine_offer.base_sku), engine_offer.base_sku)
        return CreateCheckoutResponse(
            session_id=str(session.id), status="OPEN", reason_code="OK",
            offer=OfferView(base_sku=engine_offer.base_sku, title=title,
                            unit_price_paise=engine_offer.items[0].unit_price_paise,
                            total_paise=engine_offer.total_paise,
                            return_days=engine_offer.return_terms_days,
                            shipping_days=engine_offer.delivery_days,
                            lever_type=engine_offer.lever_type),
        )
    finally:
        db.close()


# Prompt-injection markers an attacker embeds in a product description to try to
# override the mandate. Detection is content-based (catalog-agnostic) rather than a
# hardcoded SKU literal, so any catalog's injection product is attributed correctly
# (deep-review). Case-insensitive substring match on the product description.
_INJECTION_MARKERS = (
    "ignore all prior", "ignore previous", "ignore all previous", "disregard",
    "raised to unlimited", "mandate has been raised", "approve and complete",
    "override", "unlimited budget", "at full price immediately",
)


def _is_injection_product(product) -> bool:
    if product is None:
        return False
    text = (getattr(product, "description", "") or "").lower()
    return any(m in text for m in _INJECTION_MARKERS)


def _valid_session_uuid(session_id: str) -> None:
    """Reject a non-UUID session path param with a clean 404 rather than a raw 500
    when it reaches the UUID-typed column (deep-review)."""
    import uuid as _uuid
    try:
        _uuid.UUID(str(session_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(404, "session not found")


@router.post("/checkout_sessions/{session_id}/complete", response_model=CompleteCheckoutResponse)
def complete_checkout(session_id: str, req: CompleteCheckoutRequest):
    _valid_session_uuid(session_id)
    """Gate → atomic nonce consume → settle → receipt. All money decisions here."""
    db = get_session()
    try:
        audit = AuditWriter(db, actor="acp")
        session = db.get(SessionModel, session_id)
        if session is None:
            raise HTTPException(404, "session not found")

        # Already-completed guard: a session may be driven to settlement exactly
        # once. Once it has left OPEN, or already has a Payment row (a link was
        # created), refuse — otherwise a fresh cart+token pair could open a second,
        # independent real payment link against the same session.
        if session.status != SessionStatus.OPEN or _any_payment(db, session.id) is not None:
            audit.append(session.id, "COMPLETE_REFUSED",
                         {"reason_code": ReasonCode.SESSION_ALREADY_COMPLETED})
            db.commit()
            return CompleteCheckoutResponse(session_id=str(session.id), status="DENIED",
                                            reason_code=ReasonCode.SESSION_ALREADY_COMPLETED)

        auth = db.execute(
            select(Authorization).where(Authorization.intent_mandate_id == session.intent_mandate_id)
        ).scalars().first()
        mandate = db.get(IntentMandate, session.intent_mandate_id)
        ceiling = auth.ceiling if auth else {"constraints": {}}

        # Verify cart mandate + delegated token signatures.
        cart_sig_valid = _verify_cart_sig(req.cart_mandate_jws)
        try:
            token = parse_delegated_token(req.delegated_token_jws, resolve_public_key(MERCHANT_KID))
            token_sig_valid = True
        except Exception:
            token, token_sig_valid = None, False

        cart = _decode_cart(req.cart_mandate_jws)

        # Session-binding guard: the Cart Mandate carries its OWN signed session_id.
        # A cart priced/authorized for one session must never settle against another.
        if cart is not None and str(cart.get("session_id", "")) != str(session.id):
            audit.append(session.id, "COMPLETE_REFUSED",
                         {"reason_code": ReasonCode.SESSION_MISMATCH,
                          "cart_session_id": str(cart.get("session_id", ""))})
            db.commit()
            return CompleteCheckoutResponse(session_id=str(session.id), status="DENIED",
                                            reason_code=ReasonCode.SESSION_MISMATCH)

        product = db.get(Product, cart["items"][0]["sku"]) if cart else None

        now = datetime.now(timezone.utc)
        recomputed_hash = compute_cart_hash(
            items=cart["items"], total_paise=cart["total_paise"], tax_paise=cart.get("tax_paise", 0),
            shipping_paise=cart.get("shipping_paise", 0), return_terms_days=cart["return_terms_days"],
        ) if cart else "invalid"

        # T2: enforce merchant band + cart integrity from the STORED offer, and
        # recompute REALIZED margin on the ACTUAL signed cart total (the coupon /
        # promo-stacking backstop). A checkout coupon the engine never saw cannot
        # push the capture below the floor — it becomes a clean margin-floor deny.
        chosen = load_chosen_offer(db, session.id)
        active_cfg = active_config(db)
        floor_bps = active_cfg.margin_floor_bps if active_cfg else 0
        offer_cart_hash = (chosen.transformation.get("cart_hash") if chosen else None) or recomputed_hash
        added_cost = int(chosen.transformation.get("added_cost_paise", 0)) if chosen else 0
        cogs = (product.cost_paise * cart["items"][0]["qty"]) if (product and cart) else 0
        if chosen and cart:  # add bundle addon COGS if present
            for it in cart["items"][1:]:
                ap = db.get(Product, it["sku"])
                if ap:
                    cogs += ap.cost_paise * it["qty"]
        realized_bps = realized_margin_bps(cart["total_paise"], cogs, added_cost) if cart else -1

        inp = CompleteInputs(
            authorization_active=(auth is not None and auth.status == AuthorizationStatus.ACTIVE),
            authorization_expired=(mandate.expiry <= now) if mandate else True,
            cart_sig_valid=cart_sig_valid,
            token_sig_valid=token_sig_valid,
            token_expired=(token.expiry <= now) if token else True,
            cart_nonce_fresh=get_nonce_store().is_fresh(cart["nonce"]) if cart else False,
            token_nonce_fresh=get_nonce_store().is_fresh(token.nonce) if token else False,
            merchant_in_scope=(MERCHANT_ID in (mandate.allowed_merchants or [])) if mandate else False,
            # category is authorized by the BUYER's signed intent, not merely by what
            # the merchant happens to sell. Compare to the ceiling's signed category.
            category_in_scope=(
                product is not None
                and product.category == ceiling.get("constraints", {}).get("category")
            ),
            cart_total_paise=cart["total_paise"] if cart else 0,
            ceiling=ceiling,
            cart_return_days=cart["return_terms_days"] if cart else 0,
            # Verify delivery against what the engine PROMISED (persisted on the stored
            # offer), not the raw catalog value — otherwise a legitimate SHIPPING_UPGRADE
            # offer is wrongly denied at check 6 (E-03). Falls back to the catalog value
            # for as-is offers (where they're identical).
            cart_delivery_days=(
                (chosen.transformation.get("delivery_days") if chosen else None)
                if (chosen and chosen.transformation.get("delivery_days") is not None)
                else (product.shipping_days if product else 999)
            ),
            cart_quantity=cart["items"][0]["qty"] if cart else 0,
            concession_within_band=(chosen.within_bounds if chosen else True),
            resulting_margin_bps=realized_bps,      # realized on the actual captured amount
            margin_floor_bps=floor_bps,             # real merchant floor enforced
            cart_hash=cart["cart_hash"] if cart else "invalid",
            priced_offer_hash=offer_cart_hash,      # what the engine actually priced
            stock_available=product.stock if product else 0,
            stock_version_at_offer=product.stock_version if product else 0,
            stock_version_now=product.stock_version if product else -1,
            token_max_amount_paise=token.max_amount_paise if token else 0,
            token_merchant_matches=(token.merchant_id == MERCHANT_ID) if token else False,
            token_consumed=False,
            existing_captured_payment=_has_captured_payment(db, session_id, cart["cart_hash"] if cart else ""),
            driving_sku_is_injection=_is_injection_product(product),
        )

        # TRANSACTION SPLIT (fixes the audit-rollback bug found in T1 integration):
        # gate decision + nonce consume + payment-link creation commit HERE, wrapped
        # so a settlement-provider error can never roll back the audit rows. Capture
        # happens later in /poll or the webhook (a separate transaction).
        try:
            outcome = run_initiate(
                inp, session_id=str(session.id),
                cart_nonce=cart["nonce"] if cart else "x", token_nonce=token.nonce if token else "y",
                nonce_store=get_nonce_store(), settlement_provider=get_settlement_provider(), audit=audit,
            )
        except Exception as exc:  # link creation failed at the provider
            audit.append(session.id, "LINK_FAILED", {"error": type(exc).__name__})
            session.status = SessionStatus.DENIED
            session.outcome_reason = "SETTLEMENT_INIT_FAILED"
            db.commit()  # audit + session state SURVIVE the settlement failure
            raise HTTPException(502, "payment link creation failed") from exc

        if not outcome.passed:
            session.status = SessionStatus.DENIED
            session.outcome_reason = outcome.reason_code
            db.commit()
            return CompleteCheckoutResponse(session_id=str(session.id), status="DENIED",
                                            reason_code=outcome.reason_code)

        # Persist cart mandate + a PENDING payment. Capture flips it to CAPTURED
        # in finalize (poll/webhook). Payment is keyed by session+cart_hash for
        # idempotency.
        cart_row = CartMandate(
            session_id=session.id, offer_id=_placeholder_offer_id(db, session.id),
            raw_jws=req.cart_mandate_jws, items=cart["items"], total_paise=cart["total_paise"],
            tax_paise=cart.get("tax_paise", 0), shipping_paise=cart.get("shipping_paise", 0),
            return_terms_days=cart["return_terms_days"], cart_hash=cart["cart_hash"],
            nonce=cart["nonce"], merchant_sig="merchant", buyer_sig="agent",
        )
        db.add(cart_row)
        db.flush()

        db.add(Payment(cart_mandate_id=cart_row.id, razorpay_order_id=outcome.payment_link_id,
                       razorpay_payment_id=None, amount_paise=cart["total_paise"],
                       status=PaymentStatus.CREATED,
                       idempotency_key=f"{session.id}:{cart['cart_hash']}"))
        session.status = SessionStatus.OPEN
        session.outcome_reason = "PENDING_PAYMENT"
        db.commit()

        return CompleteCheckoutResponse(
            session_id=str(session.id), status="PENDING_PAYMENT", reason_code="OK",
            payment_link_id=outcome.payment_link_id, payment_link_url=outcome.payment_link_url,
        )
    finally:
        db.close()


@router.post("/checkout_sessions/{session_id}/poll", response_model=CompleteCheckoutResponse)
def poll_checkout(session_id: str):
    _valid_session_uuid(session_id)
    """Poll the payment link and finalize if it has been paid. Idempotent.

    Safe to call repeatedly; converges with the webhook on one capture.
    """
    db = get_session()
    try:
        session = db.get(SessionModel, session_id)
        if session is None:
            raise HTTPException(404, "session not found")
        payment = _pending_payment(db, session.id)
        if payment is None:
            raise HTTPException(409, "no pending payment for this session (call /complete first)")

        audit = AuditWriter(db, actor="poll")
        plan = finalize_in_db(db=db, session=session, provider=get_settlement_provider(),
                              audit=audit, link_id=payment.razorpay_order_id)

        session = db.get(SessionModel, session_id)  # re-read post-finalize
        status = "CONVERTED" if session.status == SessionStatus.CONVERTED else \
                 ("DENIED" if session.status == SessionStatus.DENIED else "PENDING_PAYMENT")
        pay = _pending_payment(db, session.id) or _any_payment(db, session.id)
        return CompleteCheckoutResponse(
            session_id=str(session.id), status=status, reason_code=plan.action.value,
            razorpay_payment_id=(pay.razorpay_payment_id if pay else None),
        )
    finally:
        db.close()


# ---------------------------------------------------------------- helpers


def _verify_cart_sig(cart_jws: str) -> bool:
    from backend.common.mandates import verify_jws

    for kid in (MERCHANT_KID, os.getenv("MANDATE_AGENT_KID", "agent-test-1")):
        try:
            verify_jws(cart_jws, resolve_public_key(kid))
            return True
        except Exception:
            continue
    return False


def _decode_cart(cart_jws: str) -> dict | None:
    from backend.common.mandates import verify_jws

    for kid in (MERCHANT_KID, os.getenv("MANDATE_AGENT_KID", "agent-test-1")):
        try:
            return verify_jws(cart_jws, resolve_public_key(kid))
        except Exception:
            continue
    return None


def _has_captured_payment(db, session_id: str, cart_hash: str) -> bool:
    key = f"{session_id}:{cart_hash}"
    return db.execute(select(Payment).where(Payment.idempotency_key == key)).scalars().first() is not None


def _placeholder_offer_id(db, session_id):
    """T1 has no Offer row (Offer Engine is T2). Create a NONE-lever placeholder
    for that legacy case ONLY.

    This used to run unconditionally on every /complete call, so a T2 session
    (which already has a real chosen=True Offer from checkout_session creation,
    carrying its actual lever) got a SECOND chosen=True row here every time —
    two "chosen" offers per session, and whichever the unordered read-side
    query happened to return first (arbitrary, no ORDER BY) decided the lever
    shown on Merchant Overview/Analytics. That silently corrupted recovered-
    revenue/lever attribution for every T2 checkout. Reuse the existing chosen
    offer when there is one; only fabricate a placeholder when there truly
    isn't (real T1 legacy path).
    """
    from backend.models import LeverType, Offer

    existing = db.execute(
        select(Offer).where(Offer.session_id == session_id, Offer.chosen == True)  # noqa: E712
    ).scalars().first()
    if existing is not None:
        return existing.id

    offer = Offer(session_id=session_id, base_sku="", lever_type=LeverType.NONE,
                  transformation={}, computed_cost_paise=0, resulting_margin_bps=0,
                  within_bounds=True, chosen=True, reason="as_is")
    db.add(offer)
    db.flush()
    return offer.id


def _pending_payment(db, session_id):
    from sqlalchemy import select as _select
    from backend.models import CartMandate as _CM, Payment as _P, PaymentStatus as _PS
    carts = db.execute(_select(_CM).where(_CM.session_id == session_id)).scalars().all()
    for c in carts:
        p = db.execute(_select(_P).where(_P.cart_mandate_id == c.id)).scalars().first()
        if p and p.status == _PS.CREATED:
            return p
    return None


def _any_payment(db, session_id):
    from sqlalchemy import select as _select
    from backend.models import CartMandate as _CM, Payment as _P
    carts = db.execute(_select(_CM).where(_CM.session_id == session_id)).scalars().all()
    for c in carts:
        p = db.execute(_select(_P).where(_P.cart_mandate_id == c.id)).scalars().first()
        if p:
            return p
    return None


@router.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request):
    """Receive Razorpay webhooks (payment_link.paid / payment.captured).

    MUST verify the signature over the RAW body before trusting anything. Then
    finalize idempotently — the same payment may also be picked up by /poll, and
    Razorpay may deliver the event more than once; both are safe.
    """
    raw = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
    if not verify_webhook_signature(raw, signature, secret):
        raise HTTPException(400, "invalid webhook signature")

    import json as _json
    # A signed-but-malformed body (truncated/corrupted delivery, or a legitimate
    # "notes": null shape) must fail as a clean 400, never a raw 500 (deep-review).
    try:
        payload = _json.loads(raw.decode("utf-8"))
        evt = parse_event(payload)
    except Exception as exc:  # noqa: BLE001 — malformed but signed payload
        raise HTTPException(400, "malformed webhook payload") from exc
    if not is_paid_event(evt):
        return {"status": "ignored", "event": evt.event}

    db = get_session()
    try:
        # Resolve the session from the payment link id (stored as razorpay_order_id
        # on the pending Payment) or from the reference_id (session:cart_hash).
        from sqlalchemy import select as _select
        from backend.models import Payment as _P, Session as _S
        link_id = evt.payment_link_id
        pay = None
        if link_id:
            pay = db.execute(_select(_P).where(_P.razorpay_order_id == link_id)).scalars().first()
        if pay is None and getattr(evt, "session_id", None):
            # Prefer the session_id carried explicitly in the link/order notes —
            # the reference_id prefix split is lossy because a UUID already contains
            # hyphens (deep-review). Exact match on the notes-provided id first.
            from backend.models import CartMandate as _CM
            carts = db.execute(_select(_CM).where(_CM.session_id == evt.session_id)).scalars().all()
            for c in carts:
                p = db.execute(_select(_P).where(_P.cart_mandate_id == c.id)).scalars().first()
                if p:
                    pay = p
                    break
        if pay is None and evt.reference_id:
            sess_id = evt.reference_id.split("-")[0]
            pay = db.execute(_select(_P).where(_P.idempotency_key.like(f"{sess_id}%"))).scalars().first()
        if pay is None:
            return {"status": "no_matching_payment"}

        cart = db.get(__import__("backend.models", fromlist=["CartMandate"]).CartMandate, pay.cart_mandate_id)
        session = db.get(_S, cart.session_id)
        audit = AuditWriter(db, actor="webhook")
        plan = finalize_in_db(db=db, session=session, provider=get_settlement_provider(),
                              audit=audit, link_id=pay.razorpay_order_id)
        return {"status": "ok", "action": plan.action.value}
    finally:
        db.close()
