"""Settlement providers (Execution plane, TRD §10) — Payment Link model.

WHY THIS SHAPE (DECISIONS.md 2026-09-01, S2S finding):
    This Razorpay test account does not have S2S ("create payment" server-side)
    enabled — it is an on-demand feature. Even when enabled, S2S still needs
    browser rendering for bank/OTP auth, so it is not truly headless. We therefore
    settle via a **Payment Link**: created server-side with ordinary test keys, it
    yields a hosted page the buyer completes, producing a real, dashboard-visible
    test-mode payment.

    Completion is detected by EITHER:
      * polling  — fetch_payment_link(id) until status == "paid" (no public URL), or
      * webhook  — Razorpay POSTs payment_link.paid to our endpoint (production).
    Both converge on one idempotent finalize step (backend/payments/finalize.py).

IDEMPOTENCY: reference_id / idem_key = session_id + ":" + cart_hash. Creating a
link with an existing reference_id returns/uses the same link rather than a second.
Status is always READ BACK from Razorpay — never hardcoded (reference-repo bug).

The S2S path (settle_autonomous) is kept for accounts that DO have it, selectable
via ACG_CAPTURE_MODE, but 'link' is the default.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class PaymentLinkResult:
    id: str
    short_url: str
    status: str            # created | paid | cancelled | expired  (READ BACK)
    amount_paise: int
    payment_id: str | None = None
    raw: dict | None = None


@dataclass(frozen=True)
class SettlementResult:
    order_id: str
    payment_id: str | None
    status: str            # CREATED | CAPTURED | FAILED (READ BACK)
    amount_paise: int
    raw: dict


def idempotency_key(session_id: str, cart_hash: str) -> str:
    return f"{session_id}:{cart_hash}"


def reference_id_for(idem_key: str) -> str:
    # Razorpay reference_id max length is 40 chars.
    return idem_key.replace(":", "-")[:40]


class SettlementProvider(ABC):
    # ---- Payment Link model (default path) ----
    @abstractmethod
    def create_payment_link(self, amount_paise: int, idem_key: str, notes: dict | None = None) -> PaymentLinkResult:
        """Create (or reuse) a hosted payment link for this idempotency key."""

    @abstractmethod
    def fetch_payment_link(self, link_id: str) -> PaymentLinkResult:
        """Read the current status of a payment link. Status is authoritative."""

    # ---- S2S model (only if the account has it; not default) ----
    def create_order(self, amount_paise: int, idem_key: str, notes: dict | None = None) -> str:  # pragma: no cover
        raise NotImplementedError("S2S order path not used in link mode")

    def settle_autonomous(self, order_id: str, amount_paise: int) -> SettlementResult:  # pragma: no cover
        raise NotImplementedError("S2S capture path not used in link mode")


class MockSettlementProvider(SettlementProvider):
    """Deterministic provider for unit tests. Simulates the link lifecycle.

    Use mark_paid(link_id) to simulate the buyer completing the hosted page.
    Enforces idempotency: a repeated idem_key returns the SAME link.
    """

    def __init__(self, *, autopay: bool = False) -> None:
        self._links_by_ref: dict[str, str] = {}
        self._links: dict[str, PaymentLinkResult] = {}
        self._autopay = autopay
        self._counter = 0
        self.create_link_calls = 0
        self.fetch_calls = 0

    def create_payment_link(self, amount_paise, idem_key, notes=None) -> PaymentLinkResult:
        self.create_link_calls += 1
        if idem_key in self._links_by_ref:
            return self._links[self._links_by_ref[idem_key]]
        self._counter += 1
        link_id = f"plink_mock_{self._counter}"
        status = "paid" if self._autopay else "created"
        payment_id = f"pay_mock_{self._counter}" if self._autopay else None
        res = PaymentLinkResult(id=link_id, short_url=f"https://rzp.test/{link_id}",
                                status=status, amount_paise=amount_paise, payment_id=payment_id)
        self._links_by_ref[idem_key] = link_id
        self._links[link_id] = res
        return res

    def fetch_payment_link(self, link_id) -> PaymentLinkResult:
        self.fetch_calls += 1
        return self._links[link_id]

    def mark_paid(self, link_id: str) -> None:
        cur = self._links[link_id]
        self._links[link_id] = PaymentLinkResult(
            id=cur.id, short_url=cur.short_url, status="paid", amount_paise=cur.amount_paise,
            payment_id=f"pay_for_{cur.id}",
        )


class RazorpayTestProvider(SettlementProvider):
    """Razorpay test-mode settlement via Payment Links. Integration-tested only."""

    def __init__(self, key_id: str | None = None, key_secret: str | None = None) -> None:
        import razorpay

        key_id = key_id or os.getenv("RAZORPAY_KEY_ID")
        key_secret = key_secret or os.getenv("RAZORPAY_KEY_SECRET")
        if not key_id or not key_secret:
            raise RuntimeError("RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not set (test mode).")
        if not key_id.startswith("rzp_test_"):
            raise RuntimeError("Refusing non-test Razorpay key. Use rzp_test_ keys only.")
        self._client = razorpay.Client(auth=(key_id, key_secret))

    def create_payment_link(self, amount_paise, idem_key, notes=None) -> PaymentLinkResult:
        ref = reference_id_for(idem_key)
        try:
            link = self._client.payment_link.create({
                "amount": amount_paise,
                "currency": "INR",
                "accept_partial": False,
                "reference_id": ref,
                "description": "ACG agentic checkout (test mode)",
                "customer": {"name": "ACG Buyer", "email": "buyer@acg.test",
                            "contact": os.getenv("ACG_TEST_CONTACT", "+919876543210")},
                "notify": {"sms": False, "email": False},
                "reminder_enable": False,
                "notes": notes or {},
            })
        except Exception as exc:
            if "reference_id" in str(exc).lower():
                existing = self._find_link_by_reference(ref)
                if existing:
                    return existing
            raise
        return PaymentLinkResult(id=link["id"], short_url=link["short_url"],
                                 status=link["status"], amount_paise=amount_paise, raw=link)

    def fetch_payment_link(self, link_id) -> PaymentLinkResult:
        link = self._client.payment_link.fetch(link_id)
        payment_id = None
        payments = link.get("payments") or []
        if payments:
            payment_id = payments[0].get("payment_id") or payments[0].get("id")
        return PaymentLinkResult(id=link["id"], short_url=link.get("short_url", ""),
                                 status=link["status"], amount_paise=link.get("amount", 0),
                                 payment_id=payment_id, raw=link)

    def _find_link_by_reference(self, ref: str) -> PaymentLinkResult | None:
        try:
            all_links = self._client.payment_link.all({"reference_id": ref})
            items = all_links.get("payment_links") or all_links.get("items") or []
            if items:
                link = items[0]
                return PaymentLinkResult(id=link["id"], short_url=link.get("short_url", ""),
                                         status=link["status"], amount_paise=link.get("amount", 0), raw=link)
        except Exception:
            return None
        return None


_SIM_PROVIDER: "MockSettlementProvider | None" = None


def get_settlement_provider() -> SettlementProvider:
    """Pick the settlement provider from ACG_SETTLEMENT_MODE.

        simulate (DEFAULT): settle in-process against the gate-authorized delegated
                            token — no Razorpay call, no payment link, no quota. This
                            is the safe mode for a public deploy: nothing a visitor
                            does can burn the 30-link test cap, and it's the honest
                            stand-in for the S2S pull the sandbox won't enable.
        razorpay          : real test-mode Payment Link + capture (consumes quota).
                            Only used when you deliberately want a real capture.

    A public deployment ships with NO Razorpay keys and stays on 'simulate'.
    """
    mode = os.getenv("ACG_SETTLEMENT_MODE", "simulate").strip().lower()
    if mode in ("razorpay", "real", "live"):
        if os.getenv("RAZORPAY_KEY_ID") and os.getenv("RAZORPAY_KEY_SECRET"):
            return RazorpayTestProvider()
        raise RuntimeError("ACG_SETTLEMENT_MODE=razorpay but RAZORPAY_KEY_ID/SECRET are not set")
    # simulate: reuse ONE instance so the link created at /complete is still visible
    # at /poll (the mock stores links in memory; a fresh instance per request would
    # lose them and the poll's fetch_payment_link would fail).
    global _SIM_PROVIDER
    if _SIM_PROVIDER is None:
        _SIM_PROVIDER = MockSettlementProvider(autopay=True)
    return _SIM_PROVIDER
