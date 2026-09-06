"""Unit tests for the pure console modules: envelope, audit_view, metrics. No DB."""

import pytest

from backend.audit.chain import GENESIS_HASH, compute_hash, link
from backend.console.audit_view import reconstruct_trail
from backend.console.envelope import compute_offer_envelope, default_intents
from backend.console.metrics import DenyRow, SessionRow, aggregate_overview
from backend.offer.engine import EngineProduct, MerchantBand


def _band(**o):
    base = dict(margin_floor_bps=1500, discount_budget_bps=1200, return_band_min_days=14,
                return_band_max_days=18, shipping_upgrade_allowed=True,
                shipping_upgrade_max_cost_paise=15000, bundle_enabled=True,
                bundle_addon_categories=("socks",), allow_promo_stacking=False)
    base.update(o)
    return MerchantBand(**base)


def _prod(sku="SHOE", price=479900, cost=370000, ret=14, ship=2, cat="running_shoes", eff=0):
    return EngineProduct(sku, cat, price, cost, 20, 1, ret, ship, title=sku, effective_price_paise=eff)


# ---------------------------------------------------------------- envelope


class TestEnvelope:
    def test_envelope_covers_levers_and_boundary(self):
        base = _prod(ret=14)
        sock = EngineProduct("SOCK", "socks", 40000, 12000, 200, 1, 30, 2)
        env = compute_offer_envelope(base, _band(), [base, sock])
        by_label = {e.label: e for e in env}
        # as-is buyer -> NONE (or accretive BUNDLE, both reachable & this SKU)
        assert by_label["As-is buyer"].reachable
        # wants +returns within band -> return extension reachable
        assert by_label["Wants +returns (in band)"].lever in ("RETURN_EXTENSION", "MULTI")
        assert by_label["Wants +returns (in band)"].return_days == 18
        # beyond band -> not reachable, honest boundary
        assert by_label["Returns beyond band"].reachable is False
        assert by_label["Returns beyond band"].lever == "NO_OFFER"

    def test_default_intents_derive_from_sku(self):
        base = _prod(ret=10, ship=3)
        intents = default_intents(base, _band())
        assert len(intents) == 6
        labels = [l for l, _ in intents]
        assert "Faster delivery" in labels and "Tighter budget" in labels

    def test_envelope_never_levers_below_floor(self):
        # the floor guards CONCESSIONS: the engine must never use a LEVER to push
        # margin below floor. An as-is sale at the merchant's own (thin) price is
        # the merchant's pricing, not a concession, so it is exempt.
        base = _prod(price=520000, cost=490000, ret=18)
        env = compute_offer_envelope(base, _band(margin_floor_bps=1500), [base])
        for e in env:
            if e.reachable and e.margin_bps is not None and e.lever not in ("NONE",):
                assert e.margin_bps >= 1500, f"{e.label} levered below floor"


# ---------------------------------------------------------------- audit trail


def _chain(details):
    """Build a valid global chain from a list of (session_id, action, detail)."""
    rows, sids, prev = [], [], GENESIS_HASH
    for i, (sid, action, detail) in enumerate(details, start=1):
        r = link(prev, i, "gateway", action, detail)
        rows.append(r); sids.append(sid); prev = r.hash
    return rows, sids


class TestAuditTrail:
    def test_valid_chain_verifies_and_extracts_session(self):
        rows, sids = _chain([
            ("S1", "MANDATE_VERIFY", {"ok": True}),
            ("S2", "OFFER", {"lever": "NONE"}),
            ("S1", "GATE_PASS", {"checks": 11}),
            ("S1", "SETTLEMENT", {"payment_id": "pay_X"}),
        ])
        view = reconstruct_trail(rows, "S1", sids)
        assert view.chain_verified is True and view.first_bad_seq is None
        # only S1's rows are extracted, in order
        assert [s.action for s in view.steps] == ["MANDATE_VERIFY", "GATE_PASS", "SETTLEMENT"]
        assert view.ledger_head_hash == rows[-1].hash

    def test_tampered_detail_is_detected(self):
        rows, sids = _chain([
            ("S1", "MANDATE_VERIFY", {"ok": True}),
            ("S1", "OFFER", {"lever": "RETURN_EXTENSION", "total_paise": 479900}),
            ("S1", "SETTLEMENT", {"payment_id": "pay_X"}),
        ])
        # tamper: someone edits the offer amount after the fact, hash no longer matches
        bad = rows[1]
        rows[1] = type(bad)(seq=bad.seq, actor=bad.actor, action=bad.action,
                            detail={"lever": "RETURN_EXTENSION", "total_paise": 999999},
                            prev_hash=bad.prev_hash, hash=bad.hash)
        view = reconstruct_trail(rows, "S1", sids)
        assert view.chain_verified is False
        assert view.first_bad_seq == 2

    def test_receipt_verification(self):
        from backend.audit.chain import build_receipt_chain
        rows, sids = _chain([("S1", "SETTLEMENT", {"payment_id": "pay_X"})])
        good = build_receipt_chain("ih", "ch", "ph")
        v = reconstruct_trail(rows, "S1", sids, receipt=good)
        assert v.receipt_present and v.receipt_verified
        tampered = {"links": {"intent_hash": "ih", "cart_hash": "ch", "payment_hash": "ph"},
                    "chain_head_hash": "deadbeef"}
        v2 = reconstruct_trail(rows, "S1", sids, receipt=tampered)
        assert v2.receipt_present and v2.receipt_verified is False


# ---------------------------------------------------------------- metrics


class TestMetrics:
    def test_recovered_revenue_and_delta(self):
        sessions = [
            SessionRow("a", "CONVERTED", "RETURN_EXTENSION", 479900),   # recovered
            SessionRow("b", "CONVERTED", "NONE", 400000),               # baseline as-is
            SessionRow("c", "ABANDONED", "", 0),
            SessionRow("d", "DENIED", "", 0, "MARGIN_FLOOR_BLOCK"),
            SessionRow("e", "CONVERTED", "BUNDLE", 500000),             # recovered
        ]
        denies = [DenyRow("MARGIN_FLOOR_BLOCK", 7), DenyRow("WITHIN_MANDATE_CEILING", 6),
                  DenyRow("MARGIN_FLOOR_BLOCK", 7)]
        m = aggregate_overview(sessions, denies)
        assert m.sessions == 5 and m.converted == 3 and m.abandoned == 1 and m.denied == 1
        assert m.recovered_sales == 2
        assert m.recovered_revenue_paise == 479900 + 500000
        assert m.baseline_converted == 1 and m.engine_delta == 2
        assert m.lever_mix == {"RETURN_EXTENSION": 1, "NONE": 1, "BUNDLE": 1}
        assert m.deny_reasons["MARGIN_FLOOR_BLOCK"] == 2
        assert m.conversion_rate_bps == 6000  # 3/5

    def test_empty_is_safe(self):
        m = aggregate_overview([], [])
        assert m.sessions == 0 and m.conversion_rate_bps == 0 and m.recovered_revenue_paise == 0


# ---------------------------------------------------------------- buyer intent parse


class TestBuyerIntent:
    def test_parses_price_and_returns(self):
        from backend.console.buyer_chat import extract_intent
        i = extract_intent("running shoes under ₹5,000 with at least 18-day returns")
        assert i["category"] == "running_shoes"
        assert i["max_price_paise"] == 500000
        assert i["min_return_days"] == 18
        assert "return" in i["tolerate"]

    def test_does_not_grab_days_as_price(self):
        from backend.console.buyer_chat import extract_intent
        i = extract_intent("18-day returns, budget 4999 rupees")
        assert i["max_price_paise"] == 499900   # 4999, not 18
        assert i["min_return_days"] == 18

    def test_category_and_tolerances(self):
        from backend.console.buyer_chat import extract_intent
        i = extract_intent("gym trainers, open to a bundle, want it delivered in 2 days")
        assert i["category"] == "training_shoes"
        assert "bundle" in i["tolerate"]
        assert i["max_delivery_days"] == 2

    def test_defaults_when_unspecified(self):
        from backend.console.buyer_chat import extract_intent
        i = extract_intent("I want some shoes")
        assert i["max_price_paise"] == 500000 and i["max_delivery_days"] == 7
        assert i["quantity"] == 1


# ---------------------------------------------------------------- buyer chat (case 3)


class TestBuyerChatExtraction:
    def test_llm_extraction_validated(self):
        from backend.console.buyer_session import extract_intent_llm
        def fake_llm(prompt):
            return '```json\n{"category":"running_shoes","max_price_paise":500000,' \
                   '"min_return_days":18,"max_delivery_days":3,"quantity":1,"tolerate":["return"]}\n```'
        intent, source = extract_intent_llm("shoes under 5k, 18-day returns", llm=fake_llm)
        assert source == "gemini" and intent["max_price_paise"] == 500000
        assert intent["min_return_days"] == 18

    def test_llm_garbage_falls_back_to_deterministic(self):
        from backend.console.buyer_session import extract_intent_llm
        intent, source = extract_intent_llm("running shoes under ₹5,000, 18-day returns",
                                            llm=lambda p: "sorry I cannot help")
        assert source == "deterministic" and intent["max_price_paise"] == 500000

    def test_llm_out_of_range_price_rejected_falls_back(self):
        from backend.console.buyer_session import extract_intent_llm
        # model hallucinates a 9-billion-paise ceiling -> validation rejects -> fallback
        intent, source = extract_intent_llm("cheap socks",
                                            llm=lambda p: '{"category":"socks","max_price_paise":9000000000}')
        assert source == "deterministic"

    def test_validate_clamps_and_whitelists(self):
        from backend.console.buyer_session import validate_proposed_intent
        v = validate_proposed_intent({"category": "socks", "max_price_paise": 40000,
                                      "min_return_days": 7, "tolerate": ["return", "hack", "bundle"]})
        assert v["tolerate"] == ["return", "bundle"]  # 'hack' dropped
        import pytest as _p
        with _p.raises(ValueError):
            validate_proposed_intent({"category": "x"})  # missing price


class TestBuyerChatTurns:
    def _offer(self, lever="RETURN_EXTENSION", total=479900, ret=18):
        return {"base_sku": "DEMO-RETURNS-001", "lever_type": lever, "total_paise": total,
                "return_days": ret, "shipping_days": 2, "unit_price_paise": total}

    def test_accept_flow_turns(self):
        from backend.console.buyer_session import compose_shop_turns
        intent = {"category": "running_shoes", "max_price_paise": 500000, "min_return_days": 18}
        turns = compose_shop_turns(intent, self._offer(), {"action": "ACCEPT", "reason": "IN_SPACE"})
        kinds = [t["kind"] for t in turns]
        assert kinds == ["search", "offer", "reasoning", "decision"]
        assert turns[-1]["decision"] == "ACCEPT"

    def test_no_offer_turns(self):
        from backend.console.buyer_session import compose_shop_turns
        intent = {"category": "running_shoes", "max_price_paise": 500000, "min_return_days": 21}
        turns = compose_shop_turns(intent, None, {"action": "ABANDON", "reason": "RETURN_UNSERVABLE_IN_BAND"})
        assert [t["kind"] for t in turns] == ["search", "no_offer"]

    def test_injection_blocked_turn(self):
        from backend.console.buyer_session import compose_shop_turns
        intent = {"category": "running_shoes", "max_price_paise": 500000, "min_return_days": 18}
        turns = compose_shop_turns(intent, self._offer(total=899900), {"action": "ABANDON", "reason": "OVER_CEILING"},
                                   injection_blocked=True)
        assert turns[-1]["kind"] == "blocked"

    def test_settlement_and_receipt_turns(self):
        from backend.console.buyer_session import compose_settlement_turn, compose_receipt_turn
        s = compose_settlement_turn({"status": "PENDING_PAYMENT", "payment_link_url": "https://rzp.io/x"})
        assert s["kind"] == "settlement" and s["payment_link"].startswith("https://")
        assert compose_receipt_turn({"status": "PENDING_PAYMENT"}) is None
        r = compose_receipt_turn({"status": "CONVERTED", "razorpay_payment_id": "pay_9"})
        assert r["kind"] == "receipt" and r["payment_id"] == "pay_9"

    def test_settlement_refusal_turn(self):
        from backend.console.buyer_session import compose_settlement_turn
        s = compose_settlement_turn({"status": "DENIED", "reason_code": "MARGIN_FLOOR_BLOCK"})
        assert s["kind"] == "blocked" and s["reason"] == "MARGIN_FLOOR_BLOCK"


class TestConversationalMerge:
    def test_edit_keeps_prior_updates_only_mentioned(self):
        # the exact reported failure: follow-up edits return, keeps price+category+tol
        from backend.console.buyer_chat import extract_intent, merge_intent
        a = extract_intent("trainers under \u20b94,500, open to a bundle")
        b = merge_intent(a, "no min return should be 5 days")
        assert b["category"] == "training_shoes"       # kept
        assert b["max_price_paise"] == 450000          # kept
        assert b["min_return_days"] == 5               # updated
        assert "bundle" in b["tolerate"]               # kept

    def test_return_number_after_word(self):
        from backend.console.buyer_chat import detect_fields
        assert detect_fields("min return should be 5 days")["min_return_days"] == 5
        assert detect_fields("returns of 21 days")["min_return_days"] == 21

    def test_price_edit_keeps_returns(self):
        from backend.console.buyer_chat import extract_intent, merge_intent
        a = extract_intent("running shoes, 18-day returns")
        b = merge_intent(a, "actually make it under 4000")
        assert b["max_price_paise"] == 400000 and b["min_return_days"] == 18

    def test_message_with_no_constraints_is_noop(self):
        from backend.console.buyer_chat import extract_intent, merge_intent
        a = extract_intent("running shoes under 5000, 18-day returns")
        b = merge_intent(a, "ok sounds good")
        assert b["max_price_paise"] == 500000 and b["min_return_days"] == 18

    def test_llm_extract_merge_path(self):
        from backend.console.buyer_session import extract_intent_llm
        prior = {"category": "training_shoes", "max_price_paise": 450000, "min_return_days": 0,
                 "max_delivery_days": 7, "quantity": 1, "tolerate": ["return", "bundle"]}
        intent, source = extract_intent_llm("min return should be 5 days", llm=None, prior=prior)
        assert source == "deterministic" and intent["min_return_days"] == 5
        assert intent["max_price_paise"] == 450000  # preserved through merge


class TestAnalytics:
    def _rows(self):
        return [
            SessionRow("a", "CONVERTED", "RETURN_EXTENSION", 479900, "", 2210),
            SessionRow("b", "CONVERTED", "NONE", 400000, "", 1500),
            SessionRow("c", "CONVERTED", "BUNDLE", 500000, "", 2360),
            SessionRow("d", "ABANDONED", "", 0, "NO_OFFER", 0),
            SessionRow("e", "DENIED", "", 0, "MARGIN_FLOOR_BLOCK", 0),
        ]

    def test_funnel(self):
        from backend.console.metrics import funnel
        f = funnel(self._rows(), offered=3)
        assert f == {"sessions": 5, "offered": 3, "converted": 3, "recovered": 2}

    def test_lever_effectiveness_sorted_by_revenue(self):
        from backend.console.metrics import lever_effectiveness
        le = lever_effectiveness(self._rows())
        assert le[0]["lever"] == "BUNDLE" and le[0]["revenue_paise"] == 500000
        re = next(x for x in le if x["lever"] == "RETURN_EXTENSION")
        assert re["avg_margin_bps"] == 2210 and re["count"] == 1

    def test_margin_histogram_buckets(self):
        from backend.console.metrics import margin_histogram
        h = margin_histogram(self._rows(), bucket_bps=500)
        floors = {b["floor_bps"]: b["count"] for b in h}
        assert floors.get(1500) == 1 and floors.get(2000) == 2  # 2210 & 2360 both in 2000 bucket
