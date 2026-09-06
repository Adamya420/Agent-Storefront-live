"""Finalize a settlement once the payment link is paid (poll OR webhook).

The DECISION of what to do is PURE and unit-tested (`plan_finalize`). The DB
writes are a thin wrapper (`finalize_in_db`), integration-tested.

Idempotency is the whole point: polling and the webhook can BOTH fire for the
same payment, and the webhook can be delivered more than once. Whichever arrives
first captures; every later call is a safe no-op.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class FinalizeAction(str, enum.Enum):
    CAPTURE = "CAPTURE"            # link is paid, payment not yet captured -> do it
    ALREADY_CAPTURED = "ALREADY"  # already finalized -> no-op (idempotent)
    NOT_PAID_YET = "NOT_PAID"     # link still pending -> nothing to do
    LINK_FAILED = "LINK_FAILED"   # link cancelled/expired -> mark failed


@dataclass(frozen=True)
class FinalizePlan:
    action: FinalizeAction
    reason: str


def plan_finalize(*, link_status: str, current_payment_status: str | None) -> FinalizePlan:
    """Pure decision. link_status from Razorpay; current_payment_status from our DB.

    current_payment_status is one of None/CREATED/CAPTURED/FAILED.
    """
    if current_payment_status == "CAPTURED":
        return FinalizePlan(FinalizeAction.ALREADY_CAPTURED, "payment already captured")
    if link_status == "paid":
        return FinalizePlan(FinalizeAction.CAPTURE, "link paid, capturing")
    if link_status in ("cancelled", "expired"):
        return FinalizePlan(FinalizeAction.LINK_FAILED, f"link {link_status}")
    return FinalizePlan(FinalizeAction.NOT_PAID_YET, f"link status={link_status}")


def finalize_in_db(*, db, session, provider, audit, link_id: str) -> FinalizePlan:
    """Fetch link status, plan, and apply the plan idempotently. Integration path.

    Returns the plan that was applied so the caller (poll route or webhook) can
    report status. Safe to call repeatedly.
    """
    from backend.audit.chain import build_receipt_chain, compute_hash
    from backend.models import (
        CartMandate,
        IntentMandate,
        Payment,
        PaymentStatus,
        Receipt,
        SessionStatus,
    )
    from sqlalchemy import select

    # Find the pending payment for this session (created at /complete time).
    cart = db.execute(
        select(CartMandate).where(CartMandate.session_id == session.id)
    ).scalars().first()
    payment = db.execute(
        select(Payment).where(Payment.cart_mandate_id == cart.id)
    ).scalars().first() if cart else None

    current_status = payment.status.value if payment else None

    link = provider.fetch_payment_link(link_id)
    plan = plan_finalize(link_status=link.status, current_payment_status=current_status)

    if plan.action == FinalizeAction.ALREADY_CAPTURED:
        return plan

    if plan.action == FinalizeAction.CAPTURE and not link.payment_id:
        # T2 integration finding (ERRORS.md 2026-09-02): Razorpay's payment_link
        # fetch can report status="paid" a few seconds BEFORE its nested
        # `payments[]` sub-resource (the one carrying the actual payment_id) is
        # populated -- confirmed by re-fetching the same link moments later and
        # seeing payment_id appear. Capturing now would write a CONVERTED
        # session + Payment row with razorpay_payment_id=None, permanently
        # (ALREADY_CAPTURED short-circuits all future polls), which breaks "read
        # status back from Razorpay" and the receipt chain. Defer instead: leave
        # the payment CREATED so the next poll (webhook or manual) retries and
        # captures once the payment_id is actually visible.
        return FinalizePlan(FinalizeAction.NOT_PAID_YET,
                            "link paid but payment_id not yet visible; will retry")

    if plan.action == FinalizeAction.CAPTURE and payment is None:
        # The plan says capture, but there is no local Payment row to capture
        # against (a CartMandate with no Payment yet, or a webhook racing the
        # /complete transaction). Don't silently report CAPTURE having written
        # nothing — surface it explicitly so the caller retries rather than
        # believing settlement happened (deep-review).
        audit.append(session.id, "FINALIZE_NO_PAYMENT",
                     {"payment_link_id": link_id, "link_status": link.status})
        db.commit()
        return FinalizePlan(FinalizeAction.NOT_PAID_YET,
                            "link paid but no local payment row yet; will retry")

    if plan.action == FinalizeAction.CAPTURE and payment is not None:
        payment.status = PaymentStatus.CAPTURED
        payment.razorpay_payment_id = link.payment_id
        mandate = db.get(IntentMandate, session.intent_mandate_id)
        chain = build_receipt_chain(
            intent_hash=compute_hash("0" * 64, {"mandate": (mandate.raw_jws[:16] if mandate else "")}),
            cart_hash=cart.cart_hash,
            payment_hash=compute_hash("0" * 64, {"payment_id": link.payment_id, "status": "paid"}),
        )
        db.add(Receipt(session_id=session.id, chain=chain["links"],
                       chain_head_hash=chain["chain_head_hash"]))
        session.status = SessionStatus.CONVERTED
        session.outcome_reason = "CONVERTED"
        audit.append(session.id, "SETTLEMENT",
                     {"payment_link_id": link_id, "payment_id": link.payment_id,
                      "status": "CAPTURED", "amount_paise": link.amount_paise})
        db.commit()

    elif plan.action == FinalizeAction.LINK_FAILED and payment is not None:
        payment.status = PaymentStatus.FAILED
        session.status = SessionStatus.DENIED
        session.outcome_reason = f"SETTLEMENT_{link.status.upper()}"
        audit.append(session.id, "SETTLEMENT",
                     {"payment_link_id": link_id, "status": link.status.upper()})
        db.commit()

    return plan
