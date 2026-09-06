"""Unit tests for T1 support components: nonce, audit chain, cart hash, idempotency."""

import pytest

from backend.audit.chain import GENESIS_HASH, compute_hash, link, verify_chain
from backend.gateway.cart import compute_cart_hash
from backend.gateway.nonce import InMemoryNonceStore


class TestNonceStore:
    def test_first_consume_succeeds_replay_fails(self):
        store = InMemoryNonceStore()
        assert store.consume("n1") is True     # first use claims it
        assert store.consume("n1") is False    # replay is rejected

    def test_is_fresh_is_read_only(self):
        store = InMemoryNonceStore()
        assert store.is_fresh("n2") is True
        assert store.is_fresh("n2") is True     # reading does not consume
        assert store.consume("n2") is True      # so consume still succeeds

    def test_distinct_nonces_independent(self):
        store = InMemoryNonceStore()
        assert store.consume("a") is True
        assert store.consume("b") is True

    def test_shared_instance_detects_replay_across_calls(self):
        """The 2 AM bug, encoded as a test.

        A SHARED store detects replay across separate 'requests'.
        """
        store = InMemoryNonceStore()

        def request(nonce):  # simulates a request handler using the shared store
            return store.consume(nonce)

        assert request("dup") is True
        assert request("dup") is False   # shared store catches it

    def test_fresh_store_per_request_would_MISS_replay(self):
        """Demonstrates WHY the singleton matters: a per-request store never
        detects replay. This asserts the broken pattern is broken, so nobody
        'simplifies' get_nonce_store() into per-request construction later."""

        def broken_request(nonce):
            return InMemoryNonceStore().consume(nonce)  # fresh store each time = bug

        assert broken_request("dup") is True
        assert broken_request("dup") is True   # replay NOT detected — the bug


class TestAuditChain:
    def test_hash_is_deterministic_and_order_independent(self):
        h1 = compute_hash(GENESIS_HASH, {"b": 1, "a": 2})
        h2 = compute_hash(GENESIS_HASH, {"a": 2, "b": 1})
        assert h1 == h2

    def test_valid_chain_verifies(self):
        r0 = link(GENESIS_HASH, 1, "gate", "VERIFY", {"x": 1})
        r1 = link(r0.hash, 2, "gate", "AUTHORIZE", {"x": 2})
        r2 = link(r1.hash, 3, "payments", "CAPTURE", {"x": 3})
        ok, bad = verify_chain([r0, r1, r2])
        assert ok is True and bad is None

    def test_tampered_detail_breaks_chain(self):
        r0 = link(GENESIS_HASH, 1, "gate", "VERIFY", {"amount": 100})
        r1 = link(r0.hash, 2, "payments", "CAPTURE", {"amount": 100})
        # Tamper: rewrite r1's detail but keep its old hash.
        import dataclasses

        tampered = dataclasses.replace(r1, detail={"amount": 999999})
        ok, bad = verify_chain([r0, tampered])
        assert ok is False and bad == 2

    def test_deleted_row_breaks_chain(self):
        r0 = link(GENESIS_HASH, 1, "a", "A", {"n": 0})
        r1 = link(r0.hash, 2, "a", "B", {"n": 1})
        r2 = link(r1.hash, 3, "a", "C", {"n": 2})
        ok, bad = verify_chain([r0, r2])   # r1 removed
        assert ok is False and bad == 3

    def test_reordered_rows_break_chain(self):
        r0 = link(GENESIS_HASH, 1, "a", "A", {"n": 0})
        r1 = link(r0.hash, 2, "a", "B", {"n": 1})
        ok, bad = verify_chain([r1, r0])
        assert ok is False


class TestCartHash:
    def test_equivalent_carts_hash_equal(self):
        items_a = [{"sku": "X", "qty": 1, "unit_price_paise": 100},
                   {"sku": "Y", "qty": 2, "unit_price_paise": 50}]
        items_b = list(reversed(items_a))  # order shouldn't matter
        h_a = compute_cart_hash(items=items_a, total_paise=200, tax_paise=0,
                                shipping_paise=0, return_terms_days=14)
        h_b = compute_cart_hash(items=items_b, total_paise=200, tax_paise=0,
                                shipping_paise=0, return_terms_days=14)
        assert h_a == h_b

    def test_changed_price_changes_hash(self):
        base = dict(items=[{"sku": "X", "qty": 1, "unit_price_paise": 100}],
                    tax_paise=0, shipping_paise=0, return_terms_days=14)
        assert compute_cart_hash(total_paise=100, **base) != compute_cart_hash(total_paise=90, **base)

    def test_changed_return_terms_changes_hash(self):
        base = dict(items=[{"sku": "X", "qty": 1, "unit_price_paise": 100}],
                    total_paise=100, tax_paise=0, shipping_paise=0)
        assert (compute_cart_hash(return_terms_days=14, **base)
                != compute_cart_hash(return_terms_days=21, **base))
