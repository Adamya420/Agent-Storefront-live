"""Tier 2 integration tests (run by Claude Code / human — hit Supabase + Razorpay).

Proves the Tier 2 checkpoint:
  * a full autonomous session recovers a sale the passive baseline abandons;
  * the injection SKU never yields an over-ceiling settlement across repeated runs;
  * a baseline-vs-engine A/B produces a real, labelled delta with raw counts;
  * the coupon-stacking backstop refuses a below-floor capture.

These require DATABASE_URL / DIRECT_URL and the Razorpay test keys in .env, and the
demo catalog seeded via scripts.run_buyer_session.setup_demo(). Claude chat CANNOT
run these (no Supabase/Razorpay from the sandbox); it produces them for the executor.

Run:
  pytest backend/tests/integration/test_t2_offer_engine.py -v -s
The --pay recovery + coupon tests create real hosted Payment Links; complete the
recovery link in the browser (or the Playwright autopay helper) within
ACG_POLL_TIMEOUT to let the recovery test assert a real capture.
"""

import os

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def demo_catalog():
    from scripts.run_buyer_session import setup_demo
    setup_demo()


def test_ab_delta_engine_beats_baseline():
    """A/B: the engine serves strictly more intents than the passive baseline, and
    the recovery intent is exactly a case the baseline abandons."""
    from scripts.run_buyer_session import run_arm
    baseline = run_arm(passive=True, min_return=18)
    engine = run_arm(passive=False, min_return=18)
    assert baseline["outcome"] == "abandoned", baseline      # no as-is 18-day product
    assert engine["outcome"] == "offer_served", engine       # engine recovers via return extension
    assert engine["offer"]["lever_type"] == "RETURN_EXTENSION"
    assert engine["offer"]["return_days"] == 18


def test_unreachable_return_is_no_offer_for_both():
    """21-day wanted vs band max 18 -> unreachable -> both arms abandon (honest bound)."""
    from scripts.run_buyer_session import run_arm
    assert run_arm(passive=True, min_return=21)["outcome"] == "abandoned"
    e = run_arm(passive=False, min_return=21)
    assert e["outcome"] == "abandoned" and e["reason"] == "RETURN_UNSERVABLE_IN_BAND"


def test_injection_never_settles_over_ceiling():
    """Across repeated forced runs, the injection SKU is refused every time."""
    from scripts.run_buyer_session import run_injection
    assert run_injection(repeat=5) is True


def test_coupon_stacking_is_refused():
    """A checkout coupon stacked on the engine's offer must not capture below floor."""
    from scripts.run_buyer_session import run_arm
    res = run_arm(passive=False, min_return=18, pay=True, coupon_paise=150000)
    assert res["outcome"] == "denied", res                   # gate refuses; no capture


@pytest.mark.skipif(os.getenv("ACG_RUN_PAYMENT") != "1",
                    reason="set ACG_RUN_PAYMENT=1 and complete the hosted link to assert a real capture")
def test_full_autonomous_recovery_settles():
    """The recovered sale settles for real test-mode money (semi-manual: pay the link)."""
    from scripts.run_buyer_session import run_arm
    res = run_arm(passive=False, min_return=18, pay=True)
    assert res["outcome"] == "converted", res
    assert res["payment_id"], "no payment id captured"
