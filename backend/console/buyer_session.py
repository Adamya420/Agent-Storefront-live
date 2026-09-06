"""Buyer lens — turn composition + intent extraction (case 3 trust model).

Flow: user prompt → EXTRACT (LLM under a strict template, deterministic fallback)
→ the client shows the proposed constraints and the USER's tap signs the mandate
(nothing is signed until confirmed) → RUN drives the real Gemini agent loop on the
existing, tested ACP rails, returning ordered turns the client reveals one by one.

What is PURE and unit-tested here:
  * validate_proposed_intent() — sanitises whatever the extractor produced BEFORE it
    can be shown for signing (integer paise, sane bounds). This is the safety net on
    the one step that defines spend authority.
  * extract_intent_llm(text, llm) — LLM extraction with a strict template; ANY
    failure (no key, bad JSON, out-of-range) falls back to the deterministic parser.
    The llm is injected so this is testable with a mock.
  * compose_shop_turns() — the ordered chat turns for a shopping outcome.

What is NOT here (stays on tested rails): signing, /complete, /poll, the 11-check
gate. The chat never re-implements the money path, so decide_on_offer + the gate
still bound every settlement even if the extractor were manipulated.
"""

from __future__ import annotations

import json

from backend.console.buyer_chat import extract_intent, narrate

# turn kinds the frontend renders as chat bubbles
SEARCH, OFFER, REASONING, DECISION, NO_OFFER, BLOCKED, SETTLEMENT, RECEIPT = (
    "search", "offer", "reasoning", "decision", "no_offer", "blocked", "settlement", "receipt")

_TOL_VALID = {"return", "bundle", "discount", "shipping_upgrade"}


def validate_proposed_intent(raw: dict) -> dict:
    """Coerce + bound an extracted intent into a safe, signable proposal.

    Never trusts the extractor: prices clamped to a sane range, integers enforced,
    tolerances whitelisted. Returns a clean intent dict; raises ValueError if the
    input is unusable (caller then falls back to the deterministic parser).
    """
    if not isinstance(raw, dict):
        raise ValueError("intent not a dict")
    cat = str(raw.get("category", "running_shoes")).strip() or "running_shoes"
    try:
        price = int(raw["max_price_paise"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("missing/invalid max_price_paise")
    # sane bounds: ₹1 .. ₹10,00,000 (100 .. 100_000_000 paise)
    if not (100 <= price <= 100_000_000):
        raise ValueError("price out of range")
    ret = int(raw.get("min_return_days", 0) or 0)
    deliv = int(raw.get("max_delivery_days", 7) or 7)
    qty = int(raw.get("quantity", 1) or 1)
    if not (0 <= ret <= 365) or not (1 <= deliv <= 60) or not (1 <= qty <= 20):
        raise ValueError("constraint out of range")
    tol = [t for t in (raw.get("tolerate") or []) if t in _TOL_VALID]
    if ret and "return" not in tol:
        tol.append("return")
    return {"category": cat, "max_price_paise": price, "min_return_days": ret,
            "max_delivery_days": deliv, "quantity": qty, "tolerate": tol or ["return"]}


EXTRACTION_TEMPLATE = """You convert a shopper's request into STRICT JSON for a signed \
spending mandate. Output ONLY a JSON object, no prose, with exactly these keys:
  category (one of: running_shoes, training_shoes, apparel, socks, accessories)
  max_price_paise (integer paise; ₹1 = 100 paise)
  min_return_days (integer, 0 if unspecified)
  max_delivery_days (integer, 7 if unspecified)
  quantity (integer, 1 if unspecified)
  tolerate (array subset of: return, bundle, discount, shipping_upgrade)
Rules: the price is the buyer's spending CEILING; never guess high — if the amount is \
vague, choose the lower plausible bound. A number followed by "day"/"days" is a return \
or delivery window, NEVER the price. Request:
---
{REQUEST}
---
JSON:"""


def extract_intent_llm(text: str, llm=None, prior: dict | None = None) -> tuple[dict, str]:
    """Extract a PROPOSED intent. Returns (intent, source).

    If `prior` is given, this is a conversational EDIT: only the fields the message
    mentions change (the standing intent is preserved). llm is optional; on any
    failure (no llm, non-JSON, validation error) falls back to the deterministic
    parser/merger — the chat never dead-ends, and validate_proposed_intent gates
    anything signable.
    """
    if llm is not None:
        try:
            ctx = ""
            if prior:
                ctx = ("\nThe shopper already has this standing intent (JSON); apply ONLY the "
                       f"changes in the request, keep everything else:\n{json.dumps(prior)}\n")
            raw = llm(EXTRACTION_TEMPLATE.replace("{REQUEST}", (ctx + (text or ""))))
            blob = raw[raw.find("{"): raw.rfind("}") + 1]
            parsed = json.loads(blob)
            merged = {**(prior or {}), **parsed} if prior else parsed
            return validate_proposed_intent(merged), "gemini"
        except Exception:  # noqa: BLE001 — fall back to deterministic
            pass
    from backend.console.buyer_chat import merge_intent
    if prior:
        return merge_intent(prior, text), "deterministic"
    return extract_intent(text), "deterministic"


def confirm_prompt(intent: dict) -> str:
    """The read-back the user must approve before anything is signed."""
    return narrate(intent)


# ---------------------------------------------------------------- turn composition


def _rupees(paise) -> str:
    return f"₹{(paise or 0) / 100:,.2f}"


def compose_shop_turns(intent: dict, offer: dict | None, decision: dict,
                       *, injection_blocked: bool = False) -> list[dict]:
    """Ordered pre-settlement turns for a shopping outcome. Pure.

    offer: the OfferView dict from /acp/checkout_sessions (or None for NO_OFFER).
    decision: {"action": "ACCEPT"|"ABANDON", "reason": "..."} from decide_on_offer.
    """
    cat = intent["category"].replace("_", " ")
    turns: list[dict] = [{
        "kind": SEARCH, "role": "agent",
        "text": f"Searching the merchant for {cat} under {_rupees(intent['max_price_paise'])}"
                + (f", ≥{intent['min_return_days']}-day returns" if intent["min_return_days"] else "") + "…"}]

    if injection_blocked:
        turns.append({"kind": BLOCKED, "role": "agent",
                      "text": "A product's description tried to raise my limit and rush the purchase. "
                              "That instruction isn't from you — ignoring it. The gateway would refuse "
                              "it anyway.", "reason": decision.get("reason", "INJECTION")})
        return turns

    if offer is None:
        turns.append({"kind": NO_OFFER, "role": "agent",
                      "text": "Nothing in the catalogue fits inside your signed limits, and the "
                              "merchant can't reach them within its own band. Walking away — no "
                              "sale beats a bad one.", "reason": decision.get("reason", "NO_OFFER")})
        return turns

    lever = offer.get("lever_type", "NONE")
    if lever in ("NONE", ""):
        offer_text = (f"Found {offer['base_sku']} at {_rupees(offer['total_paise'])} — it already "
                      f"meets everything you asked for.")
    else:
        offer_text = (f"Closest match is {offer['base_sku']}, but it misses on one thing. The "
                      f"merchant's engine offered a bounded fix ({lever.replace('_', ' ').lower()}) "
                      f"→ {_rupees(offer['total_paise'])}, {offer['return_days']}-day returns.")
    turns.append({"kind": OFFER, "role": "agent", "text": offer_text, "offer": offer})

    if decision["action"] == "ACCEPT":
        turns.append({"kind": REASONING, "role": "agent",
                      "text": "That's inside the budget you signed. Accepting."})
        turns.append({"kind": DECISION, "role": "agent", "decision": "ACCEPT",
                      "reason": decision.get("reason", "IN_SPACE"),
                      "text": "Placing the order through the gateway."})
    else:
        turns.append({"kind": REASONING, "role": "agent",
                      "text": f"This falls outside what you authorised ({decision.get('reason')}). "
                              f"I won't sign for it."})
        turns.append({"kind": DECISION, "role": "agent", "decision": "ABANDON",
                      "reason": decision.get("reason", "OUT_OF_SPACE"),
                      "text": "Abandoning — your limit holds."})
    return turns


def compose_settlement_turn(init: dict, *, simulated: bool = False) -> dict:
    """Turn for the /complete result (settlement or a gate refusal)."""
    if init.get("status") == "PENDING_PAYMENT":
        if simulated:
            # Simulated S2S: the agent settles against the delegated token the gate
            # already authorized — no hosted link, no human at a card step.
            return {"kind": SETTLEMENT, "role": "agent", "status": "SETTLING",
                    "text": "Order authorised by the gate. Settling autonomously against the "
                            "delegated payment token — no human in the loop."}
        return {"kind": SETTLEMENT, "role": "agent", "status": "PENDING_PAYMENT",
                "payment_link": init.get("payment_link_url"),
                "text": f"Order authorised by the gateway. Complete payment here — "
                        f"I'll confirm once it settles."}
    return {"kind": BLOCKED, "role": "agent", "status": init.get("status"),
            "reason": init.get("reason_code"),
            "text": f"The gateway refused this at settlement ({init.get('reason_code')}). "
                    f"No money moved."}


def compose_receipt_turn(poll: dict) -> dict | None:
    """Turn for a settled payment (or None if still pending)."""
    if poll.get("status") in ("CONVERTED", "CAPTURED"):
        pid = poll.get("razorpay_payment_id") or poll.get("payment_id")
        return {"kind": RECEIPT, "role": "agent", "status": "CONVERTED", "payment_id": pid,
                "text": f"Paid and settled. Receipt on file — payment {pid}. "
                        f"Every step is in your audit trail."}
    return None
