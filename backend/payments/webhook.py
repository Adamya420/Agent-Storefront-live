"""Razorpay webhook verification + event parsing (pure, unit-testable).

Razorpay signs each webhook with your webhook secret (HMAC-SHA256 over the RAW
request body) and sends it in the `X-Razorpay-Signature` header. We MUST verify
this before trusting anything — an unverified webhook is spoofable.

Test mode and live mode have SEPARATE webhook secrets. Use the test-mode secret
here (RAZORPAY_WEBHOOK_SECRET in .env).

Idempotency: Razorpay may deliver the same event more than once and may retry.
The finalize step (backend/payments/finalize.py) is idempotent, so duplicate
deliveries are safe.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass


class WebhookVerificationError(Exception):
    """Raised when a webhook signature does not verify. Never process on failure."""


def verify_webhook_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """Constant-time HMAC-SHA256 verification over the RAW body.

    raw_body MUST be the exact bytes Razorpay sent — re-serializing the parsed
    JSON can change whitespace/key order and break the signature.
    """
    if not signature or not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@dataclass(frozen=True)
class WebhookEvent:
    event: str                 # e.g. "payment_link.paid", "payment.captured"
    payment_link_id: str | None
    payment_id: str | None
    reference_id: str | None
    status: str | None
    amount_paise: int | None
    session_id: str | None = None


def parse_event(payload: dict) -> WebhookEvent:
    """Extract the fields finalize needs from a webhook payload.

    Handles the two events we subscribe to:
      * payment_link.paid   -> payload.payload.payment_link.entity (+ payment.entity)
      * payment.captured    -> payload.payload.payment.entity
    """
    event = payload.get("event", "")
    body = payload.get("payload", {}) or {}

    link = (body.get("payment_link") or {}).get("entity") or {}
    payment = (body.get("payment") or {}).get("entity") or {}
    order = (body.get("order") or {}).get("entity") or {}

    # notes may be present-but-null in a legitimate payload; `.get("notes", {})`
    # only substitutes the default when the key is ABSENT, so coerce None → {} to
    # avoid an AttributeError on the chained .get (deep-review).
    link_notes = link.get("notes") or {}
    pay_notes = payment.get("notes") or {}

    payment_link_id = link.get("id")
    reference_id = link.get("reference_id") or pay_notes.get("reference_id")
    # session_id is written into the link/order notes at creation — read it directly
    # rather than re-deriving it lossily from the hyphenated reference_id.
    session_id = link_notes.get("session_id") or pay_notes.get("session_id")
    payment_id = payment.get("id")
    status = link.get("status") or payment.get("status")
    amount = link.get("amount") if link.get("amount") is not None else payment.get("amount")
    if amount is None:
        amount = order.get("amount")

    return WebhookEvent(
        event=event,
        payment_link_id=payment_link_id,
        payment_id=payment_id,
        reference_id=reference_id,
        status=status,
        amount_paise=amount,
        session_id=session_id,
    )


def is_paid_event(evt: WebhookEvent) -> bool:
    """True only for events that mean 'the money arrived'."""
    if evt.event == "payment_link.paid":
        return True
    if evt.event == "payment.captured":
        return True
    # A payment_link.* event that carries status 'paid' also counts.
    return evt.status == "paid" or evt.status == "captured"
