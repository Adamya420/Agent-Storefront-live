# DECISIONS.md

Append-only. Never delete or rewrite a past entry — if a decision is later reversed, add a **new** entry that says so and links back to the original. Every agent (the team, the team, Antigravity) and the human logs here.

**Format for every entry:**
```
## [YYYY-MM-DD] Short title
**Context:** what problem/ambiguity prompted this
**Decision:** what was decided
**Alternatives considered:** what else was on the table, and why they lost
**Reversible?:** yes/no — how costly to change later
```

---

## [Project start] Track 01 approach: merchant-side gateway, not a buyer bot
**Context:** the obvious Track-01 build (an AI shopping/checkout bot) duplicates Razorpay's own shipped pilots and loses by comparison.
**Decision:** build the merchant side — ACG: mandate verification + deterministic Offer Engine + gate + Razorpay settlement + receipts — the layer Razorpay has not shipped and the natural PSP role in ACP/AP2.
**Alternatives considered:** buyer checkout bot (rejected: clones their product); simulated "observatory" with replay of synthetic shoppers (rejected: not deployable, circular insight).
**Reversible?:** no — this is the project thesis.

## [Project start] Offer Engine and Gate are fully deterministic; only the buyer is an LLM
**Context:** an LLM in the money path is a liability that undermines "explainable, bounded, gated."
**Decision:** no LLM under `backend/gateway/` or `backend/offer/`. The only LLM is the Gemini buyer stand-in (and off-money-path insight text). Enforced by a test that greps those dirs for the Gemini client.
**Alternatives considered:** LLM-composed offers on an exception path (rejected: weakens the safety story, adds latency/cost, unprovable bounds).
**Reversible?:** yes, but would require re-proving bounds — avoid.

## [Project start] Mandates are AP2-shaped via JWS/ES256, not full W3C VC/DID
**Context:** full VC/DID crypto is out of scope for a ~4–5 day build.
**Decision:** sign mandates with JWS/ES256 against a local key registry; keep the Intent/Cart/Payment structure and verification semantics. State the simplification openly.
**Alternatives considered:** full VC/DID (rejected: scope); no signing (rejected: kills the "bounded/gated" and receipt claims).
**Reversible?:** yes — the verifier is behind an interface.

## [Project start] Database is Supabase (hosted Postgres), remote, not containerized
**Context:** chose Supabase for hosted Postgres.
**Decision:** app connects via the Supabase **pooler** URI (6543) with prepared statements disabled (`prepared_statement_cache_size=0`); Alembic uses the **direct** URI (5432). RLS disabled (backend is the only, trusted client). No local Postgres container.
**Alternatives considered:** local Dockerized Postgres (rejected: user chose Supabase); Supabase client libs for data access (rejected: direct SQL/SQLAlchemy is simpler for a trusted backend).
**Reversible?:** yes — `DATABASE_URL` is config.

## [Project start] Conservative tiering: Tiers 1–2 are the guaranteed submission
**Context:** ~4–5 day solo build; must guarantee a submittable result even if a day is lost.
**Decision:** Tier 1 (execution spine) + Tier 2 (Offer Engine + autonomous buyer) are the whole guaranteed scope. Tier 3 (surfaces/insight) is build-only-if-ahead. Multi-merchant competition cut.
**Alternatives considered:** aggressive tiering with Tier 3 baked in (rejected: risks a half-finished dashboard over a working core).
**Reversible?:** yes — reprioritize if ahead of schedule.

## [2026-09-01] T0: verifier split into pure structural checks (T0) + stateful checks (T1)
**Context:** TRD §6.2 defines a 5-step mandate verification pipeline. Step 4 (nonce/replay) requires Upstash, which the team cannot reach — but steps 1/2/3/5 are pure logic that must be unit-tested by the agent authoring them.
**Decision:** `backend/gateway/verify.py` implements steps 1 (schema), 2 (signature), 3 (expiry), 5 (scope) as a pure function with an injectable `key_resolver`. Step 4 (nonce) plus DB persistence of `intent_mandate`/`authorization` rows and audit emission land in T1, owned by the team/Antigravity.
**Alternatives considered:** implementing all five now with a mocked Redis — rejected, because a mocked replay check gives false confidence about the one check that must be proven against the real store. Deferring all verification to T1 — rejected, because it would leave T0's checkpoint ("issued JWS parses in the verifier") untestable.
**Reversible?:** yes, trivially — T1 wraps this function rather than replacing it.

## [2026-09-01] Signature is verified BEFORE any field is read for a decision
**Context:** verification step order could be arranged for convenience (e.g. cheap expiry check first to short-circuit).
**Decision:** always verify the signature before evaluating expiry or scope. A forged-and-expired mandate reports SIGNATURE_INVALID, not MANDATE_EXPIRED. Enforced by `test_signature_checked_before_expiry`.
**Alternatives considered:** expiry-first for speed — rejected: it means making a trust decision on unauthenticated data, and it leaks which forged fields were "valid" to an attacker probing the endpoint.
**Reversible?:** no — reordering would need a new entry and a security argument.

## [2026-09-01] Catalog: category determines PRICE RANGE only; every other attribute is independent
**Context:** TRD §14 requires independently randomized attributes to avoid manufactured correlations, but a sock priced like a running shoe is unrealistic and would make the demo look fake.
**Decision:** `_price_for(category)` sets a per-category price range (necessary realism). Return window, shipping days, stock, rating, weight, colour, and target margin are drawn independently of category AND of each other. `test_catalog_decorrelation.py` asserts category means stay close and |Pearson r| < 0.15 for price×returns and price×shipping.
**Alternatives considered:** fully category-independent prices — rejected as absurd (₹8,000 socks). Category templates bundling attributes — rejected: it is exactly the manufactured-correlation trap.
**Reversible?:** yes, but any change must keep the decorrelation tests green.

## [2026-09-01] Margin and budget arithmetic rounds toward the SAFE side, not nearest
**Context:** `margin_bps` and `apply_bps` sit directly under the gate's margin-floor and discount-budget checks.
**Decision:** `margin_bps` floors (so a borderline margin never rounds UP across a floor) and `apply_bps` rounds DOWN (so a discount budget can never be exceeded by a paisa). Both reject floats and bools outright.
**Alternatives considered:** round-half-up for both — rejected: on a boundary it would permit an offer the merchant did not authorize, which is precisely what the bounds exist to prevent.
**Reversible?:** no, without re-proving the bounds.

## [2026-09-01] Injection attack text lives ONLY in `product.description`
**Context:** the T1/T2 adversarial scenario needs a hostile product, but the attack must be realistic rather than staged.
**Decision:** exactly one seeded SKU (`ACG-SEED-INJECT-001`) carries instruction-shaped text in its `description`, and is priced at ₹8,999 — deliberately above a typical ₹5,000 ceiling, so a refusal actually means something. No structured field is ever attack-controlled; the money path reads only signed structured constraints.
**Alternatives considered:** injecting into `title` or `attributes` too — rejected for T0: one clean, unambiguous attack surface makes the refusal attributable to a single cause.
**Reversible?:** yes — more attack surfaces are a T4 bonus item.

## [2026-09-01] T1: the Gate is a PURE function; side effects live in orchestration
**Context:** the 11-check gate needs live state (nonce freshness, inventory version, existing payment). It could fetch these itself, but then it could not be unit-tested without a DB, and the "provably bounded money path" claim weakens.
**Decision:** `backend/gateway/gate.py::authorize(ctx)` is a pure function over a pre-fetched `GateContext`. The orchestration layer (`backend/acp/orchestration.py`) fetches state, calls the gate, and performs side effects. All 11 checks are exercised on both paths in `test_gate.py` with zero DB.
**Alternatives considered:** a stateful gate that queries the DB — rejected: untestable by the planner agent, and mixes decision with I/O.
**Reversible?:** yes, but it would sacrifice the pure test suite.

## [2026-09-01] Nonce: read-only freshness in the gate, ATOMIC consume at settlement
**Context:** replay protection must be atomic, but the gate is pure and side-effect-free. Also, a stale-stock retry must not permanently burn the authorization (reference-repo audited fix + TRD §7 note on check 9).
**Decision:** gate check-4 reads `is_fresh` (non-authoritative). The authoritative `consume` (atomic SET NX on Upstash) runs in orchestration AFTER a full gate PASS and immediately before settlement. If the atomic consume loses a race, we DENY NONCE_REPLAY and never settle. Encoded in `test_orchestration.py::test_atomic_consume_catches_race_even_if_gate_read_said_fresh`.
**Alternatives considered:** consume inside the gate — rejected: makes the gate impure and could burn a nonce on a check that a legitimate retry would pass.
**Reversible?:** no, without re-opening the double-charge class.

## [2026-09-01] Razorpay capture: autonomous S2S path, Payment Link documented as fallback
**Context:** the no-human-in-loop thesis wants server-side capture, but some test accounts do not have S2S payment creation enabled.
**Decision:** `RazorpayTestProvider.settle_autonomous()` creates an order and does S2S create+capture via UPI test VPA `success@razorpay`, reading the final status back from the API (never hardcoded). `create_payment_link()` is provided as a one-click fallback if S2S is not enabled. The gate/audit/receipt logic is identical either way. HUMAN/the team must confirm which path their test account supports during T1 integration.
**Alternatives considered:** Payment Link only (needs a human click — weaker autonomy story); browser checkout (needs a UI + card entry — heavier).
**Reversible?:** yes — both paths are in the adapter; switching is a one-line call-site change.

## [2026-09-01] T1 gate runs with margin_floor=0 and concession_within_band=True (as-is)
**Context:** T1 has no Offer Engine and applies no concessions, so the merchant-band and margin-floor checks (7) have nothing to constrain yet.
**Decision:** in T1 the orchestration passes `margin_floor_bps=0` and `concession_within_band=True`, so check 7 is a no-op pass for as-is offers. T2 wires the real merchant_config band + floor and the Offer Engine's concessions into these fields. The gate code for check 7 is fully implemented and unit-tested now; only its T1 *inputs* are neutral.
**Alternatives considered:** stubbing check 7 out in T1 — rejected: leaves the check unexercised in the running system; better to run it with neutral inputs and test it fully in isolation.
**Reversible?:** yes — T2 replaces the neutral inputs with real config.

## [2026-09-01] T1 integration finding: this Razorpay test account does NOT have S2S payment creation enabled
**Context:** the 2026-09-01 "Razorpay capture: autonomous S2S path, Payment Link documented as fallback" decision left it open which path this project's actual test account supports, pending T1 integration. Ran `test_full_authorized_purchase_and_real_payment` against real Supabase/Razorpay/Upstash.
**Decision:** no code change made (none authorized). Recording the finding only: `RazorpayTestProvider.settle_autonomous()`'s S2S call (`client.payment.createPaymentJson` → `POST /v1/payments/create/json`) fails on this account with `razorpay.errors.BadRequestError: The requested URL was not found on the server.` (`code: BAD_REQUEST_ERROR`). Order creation on the same account/keys works correctly (verified by directly fetching the created order back from the Razorpay API — status `created`, correct amount). This matches the "S2S not enabled" branch anticipated by the original decision, not a code defect.
**Alternatives considered:** none evaluated by the team — explicitly out of scope for this session (Role 2 may diagnose and report but not switch the default capture path without human sign-off).
**Reversible?:** yes — switching the call site to `create_payment_link()` is, per the original decision, a one-line change once the human/the team approves it. Until then, Tier 1's "one real test-mode payment" checkpoint box is **not met**.

## [2026-09-01] Settlement switched to Payment Link + polling + webhook (S2S not available)
**Context:** T1 integration proved this Razorpay test account has no S2S; S2S is on-demand and even then needs browser auth. Needed a keys-only way to produce a real, dashboard-visible test payment.
**Decision:** default `ACG_CAPTURE_MODE=link`. `/complete` now GATES + consumes nonce + creates a **Payment Link** and returns `PENDING_PAYMENT` + link url. Capture happens in a **separate transaction** via `/poll` (fetch link status until paid) OR the `/acp/webhooks/razorpay` endpoint (`payment_link.paid`/`payment.captured`, signature-verified). Both converge on one idempotent `finalize_in_db` (plan_finalize is pure + unit-tested). S2S code retained behind the interface for accounts that have it.
**Alternatives considered:** wait for S2S enablement (uncertain timing, blocks the deadline); browser Checkout (needs a UI); polling-only (kept as default, but webhook added for the production story + judge appeal).
**Reversible?:** yes — capture mode is config; the finalize step is shared.

## [2026-09-01] Transaction split: gate+link commit first, capture commits separately
**Context:** T1 integration found `/complete` had no try/except around settlement, so an S2S exception rolled back the whole transaction INCLUDING the audit rows (GATE_PASS/ORDER_CREATED vanished). Root-caused by the team.
**Decision:** `/complete` commits the gate decision + LINK_CREATED audit + a PENDING payment in one transaction, wrapped so a provider error still commits the audit and returns a clean 502. Capture (SETTLEMENT audit + receipt + CONVERTED) is a second transaction in finalize. Audit can never again be lost to a settlement failure.
**Alternatives considered:** one big transaction with try/except — rejected: still risks partial state; the split is cleaner and matches the async link lifecycle.
**Reversible?:** no real reason to; this is strictly safer.

## [2026-09-01] Autonomy framing: authorization is autonomous; link is a settlement-rail prosthetic
**Context:** clarifying that in an agentic flow the human doesn't pay per transaction — the mandate is a one-time signed pre-authorization.
**Decision:** documented in PRD §4 and TRD §10.1 that authorization (mandate-signed, gate-verified) and settlement execution are both autonomous in production (UPI Reserve Pay/UAP), and the hosted link (click or Playwright autopay) is a test-mode stand-in for the settlement rail — NOT the human re-entering the loop. This is stated on camera.
**Reversible?:** n/a — framing/documentation.

## [2026-09-01] T1 integration fix: replaced hardcoded repeating-digit test phone number in Razorpay customer payload
**Context:** `test_full_purchase_via_link_and_poll` failed every attempt with a 502 from `/complete`. Isolating the real (non-wrapped) exception showed Razorpay's API deterministically rejects the hardcoded `"contact": "+919999999999"` in `RazorpayTestProvider.create_payment_link` with `BadRequestError: Recurring digits in customer contact are disallowed` — confirmed by payload isolation, logged in `ERRORS.md`. This blocked 100% of payment-link creation, i.e. the entire Tier 1 checkpoint.
**Decision:** changed the literal to `+919876543210` (non-repeating) in `backend/payments/settlement.py`. Scope kept to the single line; not in `backend/gateway/` or `backend/offer/` so no DECISIONS-gated re-run was mandatory, but the full unit suite (136) was re-run anyway and stayed green.
**Alternatives considered:** catching/retrying inside `create_payment_link` on this specific Razorpay error and stripping/mutating the contact — rejected as needless complexity for a one-line literal fix; sourcing the customer contact from `.env`/config instead of a literal — noted as a follow-up in ERRORS.md but not done now, to keep the fix minimal and scoped to the actual defect.
**Reversible?:** yes — trivial one-line revert; no behavioral coupling elsewhere.

## [2026-09-01] Follow-up: test phone/contact literal moved from settlement.py to config
**Context:** closes the "Would this recur elsewhere?" follow-up noted in the ERRORS.md entry for the recurring-digits contact fix — that entry flagged sourcing the contact from `.env`/config per `AGENTS.md §4` ("config... never hardcoded in logic") instead of leaving it as a literal in `settlement.py`.
**Decision:** `RazorpayTestProvider.create_payment_link` now reads `os.getenv("ACG_TEST_CONTACT", "+919876543210")` instead of the hardcoded string; the working non-repeating value is kept as the default so behavior is unchanged. Added `ACG_TEST_CONTACT=+919876543210` to `.env.example` with a comment explaining Razorpay's recurring-digit rejection, so a future editor doesn't reintroduce a repeating-digit literal without knowing why it matters.
**Alternatives considered:** none — this was the specific follow-up already identified; no new option space to weigh.
**Reversible?:** yes — trivial to inline the default back into the literal.

<!-- New entries below this line -->

---
## T2 — Offer Engine, autonomous buyer, and anti-stacking

**Cost model.** Return extension = RETURN_COST_BPS_PER_DAY (20 bps/day) of price;
shipping upgrade = flat ₹80; discount = revenue given up. Return-extension of a
₹4,799 shoe 14→18d costs ₹38.39 and lands margin at 22.10%, matching the TRD §8
worked example exactly. Discount is a PRICE REDUCTION (margin computed on the
reduced price), NOT an added cost — computing it as an added cost double-counts and
understated margin (caught by a unit test on first pass; fixed).

**Discount vs added-cost split.** `resulting_margin_bps` (floor check) uses only
real added costs (return/shipping). `computed_cost_paise` (ranking) also includes
the discount give-up, so the engine still prefers the cheapest way to close.

**ANTI-STACKING (revenue-loss defense; answers the offer/promo/coupon stacking risk).**
Three independent layers so no single stacked concession sells below cost:
  1. ONE budget. `discount_budget_bps` caps the SUM of price concessions (standing
     promo + engine discount), measured on LIST price — never per-source.
  2. Promo-aware pricing. Products carry `effective_price_paise`/`promo_code` in the
     `attributes` JSONB (no migration). The engine prices off effective and treats
     (list-effective) as spent budget. Default `allow_promo_stacking=False`: the
     engine will NOT stack a fresh discount on an already-promo'd item
     (→ NoOffer PROMO_STACK_BLOCKED); non-price levers (return/shipping) still allowed
     since they don't touch price. Opt-in stacking is still bounded by the single
     budget (STACKED_DISCOUNT_OVER_BUDGET) and the floor.
  3. Gate backstop. /complete recomputes REALIZED margin on the ACTUAL signed cart
     total (`realized_margin_bps(final_total, cogs, added_cost)`) instead of trusting
     the engine's pre-computed margin. A checkout coupon applied AFTER the offer —
     which the engine never saw — therefore cannot push a capture below the floor; it
     becomes a clean deny. A total-changing coupon also trips cart-integrity (check 8)
     since it no longer matches the priced offer hash. Position: coupons must flow
     THROUGH the engine to be honored; a post-offer coupon is refused by design.

**Merchant-scoped catalog.** `offer_wire._catalog` filters by the active (most-recent)
merchant, so the curated demo catalog is isolated from the seeded 150 products. This
is also the correct model: a merchant's engine sees its own catalog.

**Gate wiring.** create_checkout runs the engine (or as-is-only when `?passive=true`
for the A/B baseline) and stores the chosen Offer with its priced cart_hash +
added_cost. complete now enforces the real margin floor (check 7) on the realized
margin and real cart integrity (check 8) against the stored offer.

**Buyer LLM boundary.** The only LLM is the Gemini buyer narration (backend/agent/);
the ACCEPT decision is the pure `decide_on_offer` and the gate independently refuses
over-ceiling, so an injected buyer cannot cause an over-ceiling settlement. Nothing
under backend/offer/ or backend/gateway/ imports an LLM (determinism guard, 3 files).

**A/B methodology.** Reported at the OFFER level (deterministic): how many intents
the passive baseline serves as-is vs how many the engine recovers — raw counts, no
inflation. One --pay run proves the recovered sale settles for real test-mode money.

## [2026-09-02] T2 checkpoint: defer capture rather than trust `payment_link.fetch()` as atomic
**Context:** live T2 integration run found `finalize_in_db` could commit a `CAPTURED` payment with `razorpay_payment_id = NULL` when Razorpay's `payment_link.fetch()` reports `status: "paid"` a moment before its nested `payments[]` array (the actual payment record) is populated (see ERRORS.md 2026-09-02). Because `plan_finalize` correctly treats an already-`CAPTURED` payment as terminal (idempotency, TRD-required), a capture made on this incomplete read could never self-correct — permanently breaking "read status back from Razorpay" (TRD §10) and the receipt chain (FR-3, FR-10).
**Decision:** in `finalize_in_db` only (the integration-tested DB wrapper), if the plan says CAPTURE but `link.payment_id` is empty, defer: leave the payment row untouched and let the next poll/webhook retry, rather than trusting `status == "paid"` alone as sufficient to finalize. The pure, unit-tested `plan_finalize` function itself was **not** changed — its signature/semantics/tests (`backend/tests/unit/test_payment_link_flow.py`) stand as-is; the guard sits in the thin wrapper the file's own docstring already designates for exactly this kind of integration nuance.
**Alternatives considered:** (a) add a short in-process sleep-and-retry inside `fetch_payment_link` itself — rejected, it would hide the race inside the settlement adapter and add latency to every fetch, even ones that don't need it; (b) fall back to `client.order.payments(order_id)` as a secondary source when `payments[]` is empty — rejected for now as unnecessary complexity since the existing 5s poll loop already converges within one or two cycles once Razorpay's own state catches up, and the webhook path is unaffected (it carries `payment.entity.id` directly in its payload, not via a link re-fetch); (c) mark the capture as CAPTURED with a placeholder/null payment_id and backfill later — rejected outright, this is the exact "hardcoded/assumed status" failure mode TRD §10 names as a reference-repo bug to never repeat.
**Reversible?:** yes — isolated to one function; revisit if the order-payments fallback (b) is ever needed for a provider/config where the poll interval is too coarse to converge in time.

---
## T3 rev2 — feedback pass

**Role separation as a mode switch, not three apps.** One app; a role dropdown swaps
the entire nav + surface (Merchant / Rail / Buyer). Three deployed codebases were
rejected: triple the work for a solo dev, no shared code, same deadline.

**Merchant nav = only tabs backed by real capability** (per the user: no fake Razorpay
chrome, no god-mode). Home, Analytics, Products, Offers, Transactions, Disputes,
Reports, Settings. Dropped: Payment Pages/Button, Route, Invoice Manager, Settlements
(those are Razorpay's own products, not ACG's).

**Audit trail is request-and-reveal, in Disputes — not always-on.** Per the user and
rule #8: a merchant doesn't sit on the buyer's provenance; when a dispute/complaint is
raised, the merchant requests the trail and the verified chain is reconstructed for
that case. The merchant surface never shows the buyer agent's private reasoning.

**Merchant insights added.** New `/console/analytics`: conversion funnel (sessions →
offered → converted → recovered), lever effectiveness (revenue + avg margin per lever),
margin distribution, and recovered-revenue over time — all from real session data
(pure aggregations unit-tested).

**Band editor = sliders with a live envelope preview** (was 4 plain number inputs).
Makes the "provably bounded" story tactile and shows the effect on reachable offers.

**Authorization Theater: functional or cut.** Being rebuilt to plot REAL sessions in
the authorization space; if it can't earn its place on real data it will be cut rather
than shipped as a scripted toy.

## [2026-09-02] Live env: `.env` untracked by `python-dotenv` in `venv/`, keys/DB reset, port squatters killed
**Context:** T3-FE2 live run. Fresh venv had none of `requirements.txt` installed;
`data/keys/*.pem` didn't exist yet; `uvicorn backend.api.main:app` 500'd on every DB route
because nothing loaded `.env` into that process (see ERRORS.md same date); and both
`localhost:8000` and `localhost:3000` had unrelated stale Python/Node processes already
LISTENING from an earlier, unrelated session, so the documented run commands silently
bound to the wrong process (8000: startup errored and the port kept an old process; 3000:
Next.js quietly fell back to `3001` and the browser kept resolving the old tier-2 UI on
`3000` from that stale process, not ours).
**Decision:** (1) created `venv/`, `pip install -r requirements.txt`; (2) ran
`python data/generate_keys.py` to create the ES256 keypairs fresh (gitignored, never
committed); (3) added `load_dotenv()` to `backend/api/main.py` (logged separately in
ERRORS.md); (4) identified and force-killed the stale processes squatting on 8000/3000
before starting our own `uvicorn --reload` / `npm run dev`, and verified via `netstat`
that only our new PIDs were LISTENING before treating either server as live.
**Alternatives considered:** point the frontend at whatever port Next.js picked (3001) —
rejected because HANDOFF_T3_FRONTEND.md and this session's instructions hardcode
`http://localhost:3000` and the redirect target, and silently using a different port
would have left the *actually intended* :3000 origin serving stale content to anyone
(including a human reviewer) who opened it during this session.
**Reversible?:** yes, no schema/data impact — purely local process/port hygiene plus one
non-behavioral entrypoint fix.

## [2026-09-02] Rail gate ledger reads repointed to AuditLog instead of the unwritten `event` table
**Context:** live-testing Rail -> Sessions/[id] and Gate log showed empty/near-empty gate data
against real, verified sessions (see ERRORS.md same date for the full diagnosis) — the `event`
table three read endpoints queried is never written by application code; every real gate decision
is already durably, hash-chain-audited in `AuditLog` (confirmed via the Disputes tamper test, which
passed cleanly both before and after this change).
**Decision:** changed only the read side (`backend/console/routes.py`: `overview()`,
`session_detail()`, `gate_log()`) to select `GATE_PASS`/`GATE_DENY` rows from `AuditLog` and pull
`reason_code`/`check_index` out of its `detail` JSONB, instead of the `event` table.
**Alternatives considered:** (a) add real `Event` inserts at the gate call site in
`backend/acp/orchestration.py` so the originally-intended per-check ledger table gets populated —
rejected for *this* session because it touches the money-decision gate path guarded by rule #4
("never reorder/modify a check without a DECISIONS.md entry") and rule #3's audit contract, and a
write-side change to that file deserves its own deliberate review, not a same-session fix bundled
with unrelated live-integration verification; (b) leave it broken and just note it — rejected
because two of the three "what to verify against live data" checklist items for this tier
(Sessions/[id] gate ledger, Gate log) would otherwise silently fail. (a) remains open as follow-up
work if a denormalized per-check table is still wanted; for now AuditLog is the single source of
truth for gate decisions on both the Disputes/Provenance surface and the Rail surface.
**Reversible?:** yes — read-only endpoint change, no schema or write-path touched. Swapping back to
`Event` (once it's actually populated) is a pure revert of these three query blocks.

---
## T3-FE3 — polish + latency pass

**Kept the team's two backend fixes** (verified correct, 205 tests still pass): `load_dotenv()`
in `backend/api/main.py` (the app entrypoint genuinely never loaded `.env`), and repointing the
three console read endpoints (`overview`, `session_detail`, `gate_log`) from the never-written
`event` table to `AuditLog` (`GATE_PASS`/`GATE_DENY`), which is the real, hash-chained write target
in `backend/acp/orchestration.py`.

**Authorization space: CUT.** The user's verdict was that it "does not solve any problem and does
not provide any insight" and took too much space. Per the standing "functional or cut" rule, it's
removed from Analytics and the component deleted. The bounded-offer story is already carried
functionally by the Settings live envelope preview and the lever-effectiveness / margin-distribution
views.

**Depth over flatness — dark-mode specific.** Chose top-highlight insets + a readable deep-blue
shadow + surface-luminance steps rather than black drop-shadows (which are invisible on the graphite
base). Panels gained a subtle gradient + hover.

**Fonts via async `<link>`, not next/font.** next/font fetches from Google at build time, which
isn't reachable in the build sandbox; the async preconnect+preload `<link>` is non-blocking, builds
offline, and still avoids the old `@import` serialization. Reversible to next/font on a networked
build machine if preferred.

---
## Deep-review resolution pass

Fixed the ~38 deep-review findings. Notable decisions:

**Signer pinning is opt-in via a parameter, defaulting to the user kid at the ACP route.**
`verify_intent_mandate(expected_signer_kid=...)` defaults to None (backward-compatible for the
pure verifier + existing tests); the real entry point pins `USER_KID`. This keeps the unit-tested
verifier flexible while closing the consent hole on the live path.

**RETURN_ABOVE_BAND scoped to manufactured extensions, not native product returns.** The band
bounds what the ENGINE extends (`RETURN_EXTENSION`/`MULTI`), not a product's own more-generous
native return window — rejecting the latter was the root of the false NO_OFFER. A product the
merchant already sells with a 30-day return is in-policy by definition.

**Merchant scoping treats NULL merchant_id as "legacy, visible".** Rather than risk a live demo
DB going blank, pre-migration sessions (NULL) still show for the active merchant; new sessions are
scoped. On a fresh DB every session is scoped, so cross-merchant contamination is gone. The
migration also backfills existing rows to the sole product-owning merchant when exactly one exists.

**Audit seq serialized with FOR UPDATE (Postgres-only), not a schema change.** Chose a dialect-
guarded row lock over introducing a Postgres SEQUENCE, to avoid a second migration on the audit
table and keep the change reversible. Needs live concurrency verification.

**Perf findings (audit-trail full scan, N+1) deliberately NOT changed.** They're correct-by-design
(global chain verification) and classified by the reviewer as scalability notes; changing them
risks the trust-critical verification for no demo-scale benefit. Logged as deferred.

**Nonce pair: precheck-then-consume with best-effort release.** A true two-phase atomic consume
would need a store-level primitive; the precheck closes the common "stale token burns cart nonce"
case, and a `release()` (if the store implements it) compensates the rare mid-consume race.
