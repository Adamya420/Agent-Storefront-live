"""Unit tests for checkout orchestration (Payment Link initiate model).

Proves the money-safety ordering: gate → atomic nonce consume → create link,
and that a replayed initiate cannot create a second link / second money action.
Capture is a separate step (finalize), tested in test_payment_link_flow.py and
integration.
"""

from backend.acp.orchestration import CompleteInputs, run_initiate
from backend.gateway.gate import ReasonCode
from backend.gateway.nonce import InMemoryNonceStore
from backend.payments.settlement import MockSettlementProvider


class FakeAudit:
    def __init__(self):
        self.rows = []

    def append(self, session_id, action, detail):
        self.rows.append((action, detail))


def _passing_inputs(**overrides) -> CompleteInputs:
    base = dict(
        authorization_active=True,
        authorization_expired=False,
        cart_sig_valid=True,
        token_sig_valid=True,
        token_expired=False,
        cart_nonce_fresh=True,
        token_nonce_fresh=True,
        merchant_in_scope=True,
        category_in_scope=True,
        cart_total_paise=479900,
        ceiling={"constraints": {"category": "running_shoes", "max_price_paise": 500000,
                                 "min_return_days": 21, "max_delivery_days": 3, "quantity": 1}},
        cart_return_days=21,
        cart_delivery_days=2,
        cart_quantity=1,
        concession_within_band=True,
        resulting_margin_bps=2200,
        margin_floor_bps=1500,
        cart_hash="cart-hash-1",
        priced_offer_hash="cart-hash-1",
        stock_available=10,
        stock_version_at_offer=1,
        stock_version_now=1,
        token_max_amount_paise=500000,
        token_merchant_matches=True,
        token_consumed=False,
        existing_captured_payment=False,
        driving_sku_is_injection=False,
    )
    base.update(overrides)
    return CompleteInputs(**base)


def _run(inp, nonce_store=None, provider=None, audit=None, cart_nonce="cn", token_nonce="tn"):
    nonce_store = nonce_store or InMemoryNonceStore()
    provider = provider or MockSettlementProvider()
    audit = audit or FakeAudit()
    outcome = run_initiate(
        inp, session_id="sess-1", cart_nonce=cart_nonce, token_nonce=token_nonce,
        nonce_store=nonce_store, settlement_provider=provider, audit=audit,
    )
    return outcome, nonce_store, provider, audit


class TestHappyPath:
    def test_pass_creates_link_and_audits(self):
        outcome, _, provider, audit = _run(_passing_inputs())
        assert outcome.passed and outcome.reason_code == ReasonCode.OK
        assert outcome.payment_link_id and outcome.payment_link_url
        actions = [a for a, _ in audit.rows]
        assert actions == ["GATE_PASS", "LINK_CREATED"]
        assert provider.create_link_calls == 1

    def test_no_capture_happens_at_initiate(self):
        # Initiate must NOT capture — the link starts 'created', not 'paid'.
        outcome, _, provider, _ = _run(_passing_inputs())
        link = provider.fetch_payment_link(outcome.payment_link_id)
        assert link.status == "created"


class TestGateDenyStops:
    def test_deny_creates_no_link_and_leaves_nonce_fresh(self):
        nonce = InMemoryNonceStore()
        outcome, _, provider, audit = _run(
            _passing_inputs(cart_total_paise=999999999), nonce_store=nonce
        )
        assert not outcome.passed and outcome.reason_code == ReasonCode.PRICE_ABOVE_CEILING
        assert provider.create_link_calls == 0
        assert nonce.is_fresh("cn")
        assert [a for a, _ in audit.rows] == ["GATE_DENY"]

    def test_injection_over_ceiling_denied_no_link(self):
        outcome, _, provider, _ = _run(
            _passing_inputs(cart_total_paise=899900, driving_sku_is_injection=True)
        )
        assert not outcome.passed and outcome.reason_code == ReasonCode.INJECTION_REFUSED
        assert provider.create_link_calls == 0


class TestReplayDefense:
    def test_replayed_initiate_does_not_create_second_link(self):
        """The core money-safety property under the link model.

        Shared nonce store across two initiates. The first creates a link; the
        second (nonce now consumed) is refused and creates NO second link.
        """
        nonce = InMemoryNonceStore()
        provider = MockSettlementProvider()

        first, *_ = _run(_passing_inputs(), nonce_store=nonce, provider=provider)
        assert first.passed and provider.create_link_calls == 1

        second, *_ = _run(_passing_inputs(cart_nonce_fresh=False),
                          nonce_store=nonce, provider=provider)
        assert not second.passed
        assert second.reason_code == ReasonCode.NONCE_REPLAY
        assert provider.create_link_calls == 1  # NO second link

    def test_atomic_consume_catches_race_even_if_gate_read_said_fresh(self):
        nonce = InMemoryNonceStore()
        provider = MockSettlementProvider()
        assert nonce.consume("cn") is True  # winning racer consumed it out of band

        outcome, *_ = _run(_passing_inputs(cart_nonce_fresh=True),
                          nonce_store=nonce, provider=provider)
        assert not outcome.passed
        assert outcome.reason_code == ReasonCode.NONCE_REPLAY
        assert provider.create_link_calls == 0
