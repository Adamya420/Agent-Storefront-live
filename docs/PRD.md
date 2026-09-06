# PRD — Agentic Commerce Gateway (ACG)

> **Read this document first, in full, before TRD.md, SKILL.md, AGENTS.md, or SETUP_GUIDE.md.**
> This defines **what** we are building and **why**. It contains no implementation detail — that is TRD.md.
> If TRD.md or SKILL.md ever seems to contradict this document, **this document wins**; flag the conflict in `DECISIONS.md` rather than silently choosing one.
>
> **Product codename:** ACG (Agentic Commerce Gateway). **Demo brand:** "Razorpay Agent Storefront." The name is cosmetic; do not let it change any behavior.

---

## 1. One-paragraph pitch

AI shopping agents (ChatGPT, Claude, Gemini, and others) are starting to buy on behalf of humans, but an ordinary Razorpay merchant has no safe way to *transact* with them and no way to *win* their business. ACG is a merchant-side gateway that makes any Razorpay merchant transactable by autonomous AI buyers end to end: it ingests a cryptographically signed **buyer mandate** (AP2-shaped), exposes an **agent-readable catalog and checkout** (ACP-shaped), and — this is the part nobody has built — runs a deterministic **Offer Engine** that actively constructs the best *bounded* offer the merchant is authorized to make, so a sale that a passive merchant would silently lose is instead won, autonomously, with **every money action explainable, bounded, gated, and recorded in a tamper-evident audit chain**, and settled through Razorpay's test-mode APIs.

## 2. Track and context

**Track 01 — AI Growth & Agentic Commerce.**
> Grow the merchant's revenue, and make them sellable to AI buyers. Build an agent that grows revenue for a merchant on Razorpay test-mode APIs, or that makes a merchant transactable by an AI buyer end to end.
> **The bar:** Every money action explainable, bounded and gated. Show the audit trail and one failure handled gracefully.

**Why not the obvious submission.** The obvious build is "an AI agent that shops and checks out." Razorpay already runs this in production (its NPCI/Claude agentic-payments pilot with Zomato, Swiggy, Zepto, plus UPI Reserve Pay). Rebuilding the buyer side is a losing comparison against the judges' own team. ACG is the **merchant side** of the same protocol stack — the layer that makes a merchant *transactable and competitive* — which Razorpay has **not** shipped generally, and which is the natural role of a payments company in the ACP/AP2 world (the role Stripe plays for OpenAI's Instant Checkout). We hit **both** halves of the track: *make transactable* (the gateway) **and** *grow revenue* (the Offer Engine).

**Why now.** ACP (OpenAI/Stripe/Meta — the checkout + catalog rail), AP2 (Google + 60 partners — the trust/mandate rail), x402 (Coinbase — machine settlement), and NPCI's UAP (India agent identity over UPI) are all live or emerging in 2025–2026. The protocols standardize the *passive* transaction; none give the merchant the ability to *actively win* an agent's business. That gap is our product.

## 3. Goals

1. **Transactable end to end.** An autonomous (no-human-in-loop) AI buyer completes a real Razorpay test-mode purchase against our merchant, driven entirely by a signed mandate and tool calls — never by inventing a fact.
2. **Bounded, gated money.** Every money-moving action passes an authorization gate whose decision is explainable, bounded by the signed mandate and merchant policy, idempotent, and appended to a tamper-evident audit chain.
3. **Revenue won, not just observed.** The Offer Engine converts at least one sale a passive merchant would have lost, by constructing a bounded offer (bundle / return extension / shipping upgrade / bounded discount) that lands inside the intersection of what the buyer authorized and what the merchant permits — and **never** at a loss or outside authorized bounds.
4. **One failure handled gracefully, on camera.** A malicious/manipulated buyer input is refused cleanly with a specific reason, because the gate and Offer Engine read only signed, structured constraints.
5. **A verifiable receipt.** Every completed purchase yields a non-repudiable consent chain (Intent → Cart → Payment) that serves as dispute-grade evidence.

## 4. Non-goals (state these explicitly in README, TRD, the dashboard UI, and the demo video — do not let anyone imply otherwise)

- We do **not** claim access to real ChatGPT/Claude.ai/any external platform's live shopping traffic. The buyer agent is **our own Gemini-driven stand-in** for external AI buyers; the merchant side is the real, deployable product and speaks the same ACP/AP2 any external agent would.
- We do **not** implement the full cryptographic stack of AP2. Mandates are **AP2-shaped, signed with JWS/ES256**, not full W3C Verifiable Credentials with DID resolution. We say so.
- We do **not** claim real-world revenue lift. The "recovered sale" is a **capability demonstration** in a controlled comparison, honestly labeled — not a statistical market claim.
- We do **not** move real money. All settlement is **Razorpay test mode**.
- We do **not** build a general-purpose e-commerce platform. The catalog, merchant, and buyer intents are synthetic and scoped to this demo.
- **Autonomy is at authorization, not per-payment.** In a fully agentic flow the human signs the Intent Mandate **once** (in production, via their wallet with a PIN/biometric — this is what UPI Reserve Pay and AP2 mandate-signing are); the agent then transacts autonomously within those signed limits and the human does **not** approve each payment. Authorization (mandate-signed, gate-verified) and settlement execution (the fund pull) are both hands-free after that one signature. Razorpay test mode gives us no autonomous settlement rail without S2S, so we stand that final step in with a **hosted Payment Link** (completed by a click, or hands-free via a Playwright autopay helper). The link is a **test-mode prosthetic for the autonomous settlement rail** (UPI Reserve Pay / UAP), not the human re-entering the loop — the authorization was already autonomous. On camera we say exactly this.
- We do **not** put an LLM in the money path. The Offer Engine and the gate are **fully deterministic**. The only LLM is the buyer stand-in and (off the money path) insight/narration copy.

## 5. Primary stakeholders

- **The merchant** (synthetic, a Nike-style sports store) — configures concession bounds, is protected by the gate, and is the party whose revenue the Offer Engine grows.
- **The AI buyer** — an autonomous Gemini agent holding a signed Intent Mandate, acting on behalf of a human with **no human in the loop** during the session.
- **Razorpay / the judges** — evaluate via public repo, 5-minute video, architecture, and an honest "what broke" account. Razorpay is also the conceptual owner of the gate/settlement/receipt rail.

## 6. The claims this project makes, and how each is proven

| # | Claim | Proof mechanism | Where it lives |
|---|---|---|---|
| 1 | A merchant can be made transactable by an autonomous AI buyer, safely | Signed-mandate ingestion + ordered authorization gate + real Razorpay test settlement + tamper-evident audit chain | Execution + Commerce planes |
| 2 | Every money action is explainable, bounded, gated | Deterministic gate: each action carries a structured reason code and is bounded by the signed mandate ∩ merchant policy; idempotent; audited | Execution plane |
| 3 | The merchant's revenue grows without loss or overreach | Deterministic Offer Engine constructs the minimum-cost bounded offer inside buyer-tolerance ∩ merchant-band, margin-floor enforced; declines when no legal offer exists | Commerce plane |
| 4 | The system refuses manipulation gracefully | Gate/Offer Engine read only signed structured constraints; injected natural-language instructions are inert data | Execution plane |

**Plane separation is itself part of the demonstration.** The **Execution plane** (gate + settlement) and the **Commerce plane** (catalog + Offer Engine) are architecturally separate from the **Observability plane** (audit, receipts, insight). If observability breaks, no money is affected. This is sound system design under a real safety constraint, and it is a resilience argument for the live demo (if the dashboard breaks on stage, the payment path still works).

## 7. Functional requirements (each independently checkable — yes/no, not "sort of")

**FR-1.** A buyer agent completes a full journey — receive Intent Mandate → discover products via ACP feed → inspect products / shipping / returns → receive an offer → accept or abandon → (if accept) settle — **entirely through defined tools**, never by inventing price, stock, shipping, or policy.

**FR-2.** Every money-moving action passes the authorization gate, which checks, in order: mandate schema, signature validity, expiry/TTL, nonce/replay, merchant + category scope, offer-within-mandate-ceiling, offer-within-merchant-band, cart integrity (hash unchanged since offer), inventory-version consistency, delegated-token scope, idempotency. Any failure returns a **structured, human-readable reason code** — never a silent failure or generic error.

**FR-3.** A successful autonomous checkout produces **one real Razorpay test-mode payment**, and the full chain (Intent Mandate → offer → Cart Mandate → Payment) is reconstructable from the append-only, hash-chained audit log alone.

**FR-4.** The Offer Engine, given an intent that no in-stock product satisfies as-is, either (a) constructs a **bounded** offer inside buyer-tolerance ∩ merchant-band that closes the sale with margin ≥ floor, or (b) returns a clean **NO_OFFER** with a reason. It never emits an offer that violates the mandate ceiling, the merchant band, or the margin floor.

**FR-5.** Every tool call and every gate decision produces **exactly one** append-only event row. No silent actions.

**FR-6.** Every abandoned/declined session is classified into **exactly one reason code** from a fixed taxonomy (TRD §7.3) — never free text. The reason is derived only from observable, disclosed signals (the mandate + tool calls), never from the buyer's private reasoning.

**FR-7.** At least one adversarial scenario — the **prompt-injection-via-product-field** case (see TRD §12) — is demonstrated live and refused with a correct, specific reason, with no money moved.

**FR-8.** A merchant console lets the merchant set concession bounds (margin floor, discount budget, return-extension band min/max, allowed categories, velocity limits) and shows an agent-readiness view of the catalog.

**FR-9.** A "wire theater" view shows a live session using **only observable data** (the disclosed mandate + the tool-call stream + the gate stamps + the merchant-side offer moves). The buyer agent's private reasoning appears **only** in a separate, explicitly labeled "buyer stand-in internals" panel, **never** inside the merchant console.

**FR-10.** A completed purchase renders a **verifiable receipt** (Intent → Cart → Payment chain) with a one-click dispute-evidence export.

**FR-11.** All non-goals in §4 are stated visibly — README, TRD, dashboard UI labels, and spoken in the video.

## 8. Success criteria / acceptance bar (submission-ready when all true)

- [ ] One real, end-to-end Razorpay **test-mode** payment exists, fully reconstructable from the audit chain alone.
- [ ] The autonomous buyer completes a purchase **the Offer Engine recovered** (a passive-merchant baseline abandons the same intent; the Offer Engine converts it within bounds) — shown side by side.
- [ ] All gate checks (TRD §7) have passing unit tests for **both** pass and fail paths.
- [ ] The prompt-injection scenario is refused live with a specific reason and **no** money moved.
- [ ] Every non-goal in §4 is stated somewhere a judge sees before asking.
- [ ] `docker compose up` brings the backend + frontend up for a judge with **zero undocumented manual steps** (Postgres is Supabase-hosted, Redis is Upstash-hosted — both remote; the judge only needs the `.env` values documented in `.env.example`).
- [ ] `docs/what_broke.md` is written from **real** `ERRORS.md` entries, not invented after the fact.

## 9. Scope tiers and the timeline reality

Solo builder, coding via Claude Code + Antigravity, deadline **Sept 4–5**. Scope is disciplined to protect a winning spine.

- **Tier 0 — Scaffold** (must): clean repo, Docker, data model, Nike-style catalog generator with independently-randomized attributes.
- **Tier 1 — Execution spine** (must): ACP endpoints + mandate verifier + authorization gate (all checks, tested) + Razorpay test settlement + hash-chained audit + receipt. One manual authorized purchase, reconstructable from the audit chain.
- **Tier 2 — The differentiator** (must): deterministic Offer Engine + bounds validator + Gemini buyer agent (no-human-in-loop). One autonomous **recovered** sale end to end; the injection failure handled gracefully.
- **Tier 3 — Surfaces + insight** (should, build only if Tier 1–2 are genuinely done): merchant console + wire theater + receipt drawer; recoverable-revenue insight; deterministic Sandbox Preview.
- **Tier 4 — Polish** (bonus/cut): extra adversarial scenarios, dashboard polish, `graphify` codebase graph **only if navigation genuinely becomes hard**.

**If time runs short, the frontend for the video takes priority over Tier 3 analytics.** A working recovered-sale + injection-refusal demo with a basic UI beats a rich dashboard with a broken payment path.

## 10. Open risks (tracked, not hidden)

- **Consent gap under autonomy.** The Offer Engine changes the deal; with no human present, the buyer agent may only auto-accept offers **inside the pre-authorized space** (buyer-tolerance ∩ merchant-band). Anything outside → decline. Enforced in TRD §8. If this is implemented loosely, we reintroduce unauthorized-add-on risk.
- **Ecosystem mismatch.** Real ChatGPT today sends ACP with a delegated token and often **no** full AP2 Intent Mandate. We accept **both**: if a full mandate is absent, the delegated token's (max amount, expiry, merchant) is treated as a minimal mandate. Stated as a deliberate design choice, not hidden.
- **"Recovered sale" honesty.** Because we author the buyer stand-in, the recovery is only as real as a fixed, fair buyer decision rule. The buyer's accept/reject logic must be identical across baseline and Offer-Engine runs, and we say the comparison is controlled.
- **Margin claims.** "Cannot make a loss" is only true per validated line against its floor; promo-stacking, tax-on-discounted-price, and return-cost modeling are explicit bounds in TRD §8, not assumptions.
- **Free-tier limits (Gemini, Upstash).** Ceilings shift; build retry-with-backoff from day one, do not hardcode a number.
- **Timeline.** The single biggest risk. Protect the Tier 1–2 spine above all.

## 11. Glossary

See `AGENTS.md §1` for the shared glossary used across all documents.
