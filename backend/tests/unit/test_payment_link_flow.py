"""Unit tests for the Payment Link settlement path: link idempotency, webhook
signature verification + event parsing, and the pure finalize planner. No network.
"""

import hashlib
import hmac
import json

import pytest

from backend.payments.finalize import FinalizeAction, plan_finalize
from backend.payments.settlement import (
    MockSettlementProvider,
    idempotency_key,
    reference_id_for,
)
from backend.payments.webhook import (
    is_paid_event,
    parse_event,
    verify_webhook_signature,
)


class TestPaymentLinkIdempotency:
    def test_same_idem_key_reuses_link(self):
        prov = MockSettlementProvider()
        key = idempotency_key("sess-1", "cart-1")
        a = prov.create_payment_link(479900, key)
        b = prov.create_payment_link(479900, key)   # retry
        assert a.id == b.id
        assert prov.create_link_calls == 2          # called twice...
        assert len(prov._links) == 1                # ...one link exists

    def test_different_keys_distinct_links(self):
        prov = MockSettlementProvider()
        a = prov.create_payment_link(100, idempotency_key("s", "h1"))
        b = prov.create_payment_link(100, idempotency_key("s", "h2"))
        assert a.id != b.id

    def test_created_then_paid_status_read_back(self):
        prov = MockSettlementProvider()
        link = prov.create_payment_link(100, idempotency_key("s", "h"))
        assert link.status == "created" and link.payment_id is None
        prov.mark_paid(link.id)
        fetched = prov.fetch_payment_link(link.id)
        assert fetched.status == "paid" and fetched.payment_id is not None

    def test_reference_id_truncated_to_40(self):
        long = idempotency_key("s" * 30, "c" * 30)
        assert len(reference_id_for(long)) <= 40
        assert ":" not in reference_id_for(long)


class TestWebhookSignature:
    SECRET = "whsec_test_123"

    def _sign(self, body: bytes) -> str:
        return hmac.new(self.SECRET.encode(), body, hashlib.sha256).hexdigest()

    def test_valid_signature_passes(self):
        body = json.dumps({"event": "payment_link.paid"}).encode()
        assert verify_webhook_signature(body, self._sign(body), self.SECRET) is True

    def test_tampered_body_fails(self):
        body = json.dumps({"event": "payment_link.paid"}).encode()
        sig = self._sign(body)
        tampered = json.dumps({"event": "payment_link.paid", "x": 1}).encode()
        assert verify_webhook_signature(tampered, sig, self.SECRET) is False

    def test_wrong_secret_fails(self):
        body = b"{}"
        assert verify_webhook_signature(body, self._sign(body), "other_secret") is False

    def test_empty_signature_or_secret_fails(self):
        assert verify_webhook_signature(b"{}", "", self.SECRET) is False
        assert verify_webhook_signature(b"{}", "abc", "") is False


class TestWebhookParsing:
    def test_payment_link_paid_event(self):
        payload = {
            "event": "payment_link.paid",
            "payload": {
                "payment_link": {"entity": {"id": "plink_1", "reference_id": "sess-cart",
                                            "status": "paid", "amount": 479900}},
                "payment": {"entity": {"id": "pay_1", "status": "captured"}},
            },
        }
        evt = parse_event(payload)
        assert evt.event == "payment_link.paid"
        assert evt.payment_link_id == "plink_1"
        assert evt.payment_id == "pay_1"
        assert evt.amount_paise == 479900
        assert is_paid_event(evt) is True

    def test_payment_captured_event(self):
        payload = {"event": "payment.captured",
                   "payload": {"payment": {"entity": {"id": "pay_9", "status": "captured", "amount": 100}}}}
        evt = parse_event(payload)
        assert evt.payment_id == "pay_9"
        assert is_paid_event(evt) is True

    def test_unrelated_event_not_paid(self):
        evt = parse_event({"event": "payment_link.created",
                           "payload": {"payment_link": {"entity": {"id": "p", "status": "created"}}}})
        assert is_paid_event(evt) is False


class TestFinalizePlanner:
    def test_paid_and_not_captured_triggers_capture(self):
        plan = plan_finalize(link_status="paid", current_payment_status="CREATED")
        assert plan.action == FinalizeAction.CAPTURE

    def test_already_captured_is_noop(self):
        # Idempotency: poll and webhook both firing must not double-capture.
        plan = plan_finalize(link_status="paid", current_payment_status="CAPTURED")
        assert plan.action == FinalizeAction.ALREADY_CAPTURED

    def test_not_paid_yet(self):
        plan = plan_finalize(link_status="created", current_payment_status="CREATED")
        assert plan.action == FinalizeAction.NOT_PAID_YET

    def test_cancelled_link_marks_failed(self):
        plan = plan_finalize(link_status="cancelled", current_payment_status="CREATED")
        assert plan.action == FinalizeAction.LINK_FAILED

    def test_expired_link_marks_failed(self):
        plan = plan_finalize(link_status="expired", current_payment_status="CREATED")
        assert plan.action == FinalizeAction.LINK_FAILED

    def test_already_captured_wins_even_if_link_expired(self):
        # If we already captured, a later 'expired' fetch must not undo it.
        plan = plan_finalize(link_status="expired", current_payment_status="CAPTURED")
        assert plan.action == FinalizeAction.ALREADY_CAPTURED
