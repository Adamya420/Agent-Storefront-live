# ERRORS.md

Append-only. Every error that took more than a trivial, obvious fix goes here — this is the raw material for `docs/what_broke.md` and the demo video's failure-story segment. **Do not sanitize after the fact; the honest version is more valuable than a tidy one.** Every agent (the team, the team, Antigravity) and the human logs here.

**Format for every entry:**
```
## [YYYY-MM-DD HH:MM] Short title
**Where:** file/component
**What happened:** the actual error/symptom, with the real error message where possible
**Root cause:** why it actually happened, not just what the fix was
**Fix applied:** what changed, and why that's the right fix (not just "it works now")
**Files touched:**
**Would this recur elsewhere?:** yes/no — if yes, where else to check
```

---

## [2026-09-01 T0] Unit test asserted the wrong margin value (2289 vs 2290)
**Where:** backend/tests/unit/test_money.py::TestMarginBps::test_known_margin
**What happened:** first full unit run failed 1/53:
`assert 2290 == 2289 | where 2290 = margin_bps(479900, 370000)`
**Root cause:** my own arithmetic error in the TEST, not in the implementation. (479900-370000)*10000 // 479900 = 1_099_000_000 // 479900 = 2290.06 → floors to 2290. I wrote 2289 by mentally flooring 22.90% instead of flooring the bps quotient.
**Fix applied:** corrected the expectation to 2290 and wrote the full arithmetic into the test comment so the next reader can verify it without recomputing. The implementation was left unchanged — it was already correct. Worth recording despite being a small fix, because it is exactly the class of error (off-by-one in margin bps) that would silently let an offer sit one basis point below the merchant's floor if it had gone the other way.
**Files touched:** backend/tests/unit/test_money.py
**Would this recur elsewhere?:** yes — anywhere bps arithmetic is asserted by hand. When writing the Offer Engine's margin tests in T2, compute expected values in the test from the same formula, or assert against a recomputed expression rather than a hand-typed constant.

<!-- Likely candidates to watch for, based on TRD design — delete this comment once real entries exist:
     - Supabase pooler + prepared statements (asyncpg/SQLAlchemy) intermittent failures
     - Razorpay test-mode payment status hardcoded instead of read back
     - Nonce store instantiated per-request (replay protection silently no-ops) — the reference-repo 2AM bug
     - Gemini free-tier rate limit / backoff
     - Idempotency key collision or absence → double order
     - Offer Engine emitting an out-of-band offer that skipped bounds.py -->

## [2026-09-01 T1] Deep import check failed: fastapi not in sandbox, then bad introspection attr
**Where:** import-verification of backend/acp/routes.py
**What happened:** two successive errors while verifying the routes import cleanly:
(1) `ModuleNotFoundError: No module named 'fastapi'` — fastapi was not installed in the planner sandbox (only test deps were).
(2) after installing: `AttributeError: '_IncludedRouter' object has no attribute 'path'` — my introspection line assumed every entry in app.routes has `.path`; mount/included-router objects do not.
**Root cause:** (1) app-runtime deps aren't auto-present in the planner sandbox; (2) my verification code, not the app code — I iterated app.routes naively.
**Fix applied:** (1) `pip install fastapi` in-sandbox for the check; (2) switched to the authoritative `app.openapi()['paths']` to list endpoints, which confirmed all four (/acp/feed, /acp/checkout_sessions, /.../complete, /health) are registered. The app code was correct throughout; only my check was wrong. Recorded because it is a reminder that "the app imports" must be verified against OpenAPI, not against ad-hoc route introspection.
**Files touched:** none in app code (verification-only).
**Would this recur elsewhere?:** yes — any future route-registration check should assert against app.openapi() paths, which is what the team should use in CI too.

## [2026-09-01 T1] `upstash-redis` declared in requirements.txt but not installed in the execution environment
**Where:** environment / `backend/gateway/nonce.py::UpstashNonceStore.__init__`
**What happened:** first T1 integration run failed 4/5 tests with `ModuleNotFoundError: No module named 'upstash_redis'` raised inside `get_nonce_store()` — any code path touching the real nonce store (checkout complete, replay test) died before it could reach Upstash at all.
**Root cause:** `requirements.txt` already pins `upstash-redis>=1.1`, but this execution environment's site-packages never had it installed (`pip show upstash-redis` returned "not found"). Not a code defect — the lazy import in `nonce.py` (`from upstash_redis import Redis`, deliberately deferred so unit tests don't need the package) is correct; the dependency was simply missing from the environment.
**Fix applied:** `pip install "upstash-redis>=1.1"` (resolved to 1.8.0) — installs exactly what requirements.txt already specifies, no code or version-pin change.
**Files touched:** none (environment only).
**Would this recur elsewhere?:** yes — any fresh clone/environment that runs `pip install -r requirements.txt` should get this automatically, but if the team's sandbox setup ever skips that step (as apparently happened here), the same failure will recur. Worth a one-line note in SETUP_GUIDE.md to run the full `pip install -r requirements.txt` before T1 integration, not just the subset needed for unit tests.

## [2026-09-01 T1] Razorpay S2S payment creation (`/v1/payments/create/json`) not enabled on this test account — real payment settlement blocked
**Where:** `backend/payments/settlement.py::RazorpayTestProvider.settle_autonomous` (S2S create+capture path), exercised via `test_full_authorized_purchase_and_real_payment`
**What happened:** order creation succeeded (`order_TWett2pYDOx3nL`, ₹4,009.00 / 400900 paise, confirmed by a direct read-only `client.order.fetch()` — status `created`), but the immediately following S2S `payment.createPaymentJson()` call (UPI test VPA `success@razorpay`) failed with:
`razorpay.errors.BadRequestError: The requested URL was not found on the server.`
— raw API response: `{"error": {"code": "BAD_REQUEST_ERROR", "description": "The requested URL was not found on the server.", "metadata": {"order_id": "order_TWett2pYDOx3nL"}, "reason": "NA"}}`
This is the exact scenario flagged as an open question in DECISIONS.md ("Razorpay capture: autonomous S2S path, Payment Link documented as fallback") — this Razorpay test account does not have S2S ("create payment" server-side) enabled. The exception is uncaught in `backend/acp/routes.py::complete_checkout` (no try/except around `run_complete`), so it propagates before `db.commit()` — which is also why `test_purchase_reconstructable_from_audit_chain` failed with `GATE_PASS`/`ORDER_CREATED` missing from the audit log: those rows were appended in-session but the transaction was never committed once the unhandled exception hit. This is a downstream symptom of the same single root cause, not an independent audit bug.
**Root cause:** account-level Razorpay configuration (S2S payment creation not enabled for this test key), not application code. Confirmed order creation works fine; only server-side payment creation is unavailable.
**Fix applied:** none — per explicit instruction, did not implement the `create_payment_link()` fallback or otherwise modify `backend/payments/settlement.py`. Reporting only; the human/the team must decide whether to switch the default capture path.
**Files touched:** none.
**Would this recur elsewhere?:** yes — any test run attempting `settle_autonomous()` on this same Razorpay account will hit this until either S2S is enabled on the account (Razorpay dashboard/support) or the code path is switched to `create_payment_link()`.

## [2026-09-01 T1->link] Settlement refactor: 3 stale S2S unit tests failed after provider swap
**Where:** backend/tests/unit/test_t1_components.py::TestIdempotency
**What happened:** after replacing the S2S provider methods (create_order/settle_autonomous) with the Payment Link methods (create_payment_link/fetch_payment_link), the full unit run failed 3/139 with `TypeError: MockSettlementProvider.__init__() got an unexpected keyword argument 'fail_capture'` and missing create_order.
**Root cause:** those three tests asserted the OLD S2S order/capture semantics, which no longer exist. Not a defect in the new code — obsolete tests for a removed path.
**Fix applied:** removed the stale `TestIdempotency` class from test_t1_components.py; the equivalent guarantees (link idempotency, status read-back, no double capture) are re-proven in the new test_payment_link_flow.py (17 tests) and the rewritten test_orchestration.py (initiate model). Net unit count 136, all green.
**Files touched:** backend/tests/unit/test_t1_components.py (removed class), backend/tests/unit/test_payment_link_flow.py (new), backend/tests/unit/test_orchestration.py (rewritten).
**Would this recur elsewhere?:** yes — any provider-interface change should be paired with a sweep of tests asserting the old interface. Grep for the old method names before swapping.

## [2026-09-01] T1 integration: `data/keys/` key registry was never generated in this checkout — mandate signing failed
**Where:** `backend/common/keys.py::load_private_key`, exercised via `test_t1_execution_spine.py::test_full_purchase_via_link_and_poll`
**What happened:** first integration attempt at Step 6 failed immediately at intent-mandate issuance:
`backend.common.keys.KeyNotFoundError: no private key for kid='user-test-1' at .../data/keys/user-test-1.pem`
`data/keys/` existed as an empty directory — none of the three ES256 keypairs (SKILL.md Tier 0, item 5) had been generated in this checked-out working tree, even though `data/generate_keys.py` exists and `.gitignore` correctly excludes `data/keys/*.pem`. Every other T0 integration check (`test_t0_supabase.py`, 6/6) passed because none of them touch mandate signing — so the missing keys were invisible until T1's `_issue_intent()` first tried to sign.
**Root cause:** environment/setup gap, not an application defect — the T0 checkpoint box "`issue_mandate.py` produces a verifiable JWS" was never actually exercised against this specific clone/environment before handoff; the private keys (gitignored by design) simply don't travel with the repo.
**Fix applied:** ran `python data/generate_keys.py` (no `--force`; created all three, none pre-existing) — a data/config bootstrap step identical in kind to catalog seeding, not an application-code change. No files under `backend/` were touched.
**Files touched:** none under `backend/`; created `data/keys/user-test-1.pem`, `data/keys/agent-test-1.pem`, `data/keys/merchant-test-1.pem` (+ `.pub.pem` siblings), all gitignored.
**Would this recur elsewhere?:** yes — any fresh clone/environment will hit this until `data/generate_keys.py` is run once. Worth adding as an explicit line item in WEBHOOK_SETUP.md / SETUP_GUIDE.md's integration-prep steps, since it currently isn't called out there even though AGENTS.md/SKILL.md name it as a T0 build step.

## [2026-09-01] T1 integration: EVERY payment-link creation failed — hardcoded customer phone number rejected by Razorpay
**Where:** `backend/payments/settlement.py::RazorpayTestProvider.create_payment_link`, surfaced via `test_full_purchase_via_link_and_poll` as a generic `502 {"detail":"payment link creation failed"}` (the real exception is caught and re-raised opaquely in `backend/acp/routes.py` around `run_initiate`, by design, so the audit survives — see the "TRANSACTION SPLIT" decision).
**What happened:** the wrapped 502 gave no root cause, so I called `RazorpayTestProvider.create_payment_link` directly (bypassing the route's exception wrapping) with the exact same payload the app sends. Real error from the Razorpay SDK:
`razorpay.errors.BadRequestError: Recurring digits in customer contact are disallowed`
The hardcoded `"contact": "+919999999999"` (ten repeated `9`s) fails Razorpay's server-side validation on `payment_link.create` — this is not account/env/key-related; every call with this literal payload fails the same way regardless of amount, reference_id, or account.
**Root cause:** application defect — a hardcoded placeholder phone number in `create_payment_link()`'s customer block happens to trip Razorpay's "recurring digits" fraud-heuristic. Confirmed by isolating the exact payload and swapping only the `contact` value: `+919876543210` (no repeated digit) succeeds and returns a real `plink_...` id + `short_url`, all other fields unchanged. Classified per the task's environment/configuration/provider/test/code taxonomy as **code**: the evidence (deterministic, payload-isolated, reproducible with an unmodified account/keys) rules out environment, config, provider outage, and test-authoring error.
**Fix applied:** changed the hardcoded contact in `backend/payments/settlement.py::RazorpayTestProvider.create_payment_link` from `+919999999999` to `+919876543210`. No other line changed. Full unit suite re-run after the fix: 136/136 still green (this code path has no unit coverage of the literal string — `MockSettlementProvider` doesn't call the real API — so unit tests could not have caught this; only integration exercise against the real Razorpay API did).
**Files touched:** `backend/payments/settlement.py` (one line).
**Would this recur elsewhere?:** yes — any other hardcoded test phone/contact literal introduced later (e.g. if a webhook test or demo script fabricates customer data) should avoid all-repeating-digit numbers. Worth a comment at the literal, and/or sourcing it from `.env`/config per `AGENTS.md §4` ("config... never hardcoded in logic") rather than a literal in `settlement.py`.

## [2026-09-01] T1 webhook integration: `test_webhook_paid_event_finalizes_if_link_matches` fails with NOT_PAID — classified as a TEST defect, not fixed
**Where:** `backend/tests/integration/test_t1_webhook.py::test_webhook_paid_event_finalizes_if_link_matches`
**What happened:** the test's `db_seeded_pending_link` fixture creates a brand-new, genuinely-unpaid Razorpay payment link via `/complete`, then immediately POSTs a *synthetic* signed `payment_link.paid` webhook body claiming that link is paid. The assertion expects `r.json()["action"] in ("CAPTURE", "ALREADY")` but got `"NOT_PAID"`.
**Root cause:** by design (`backend/payments/finalize.py::finalize_in_db`), the webhook handler never trusts the webhook payload's claimed status for writing money state — it always calls `provider.fetch_payment_link(link_id)` to re-read the REAL status from Razorpay before capturing, per the explicit "status always READ BACK from Razorpay — never hardcoded" decision (DECISIONS.md, matches the hard RULE given for this session: "never hardcode a payment status"). Since the fixture's link was never actually paid by anyone, the real re-fetched status is still `created`, so `NOT_PAID` is the objectively correct, honest answer. The test's premise — that a signed-but-synthetic webhook alone should trigger CAPTURE — is incompatible with that design. Verified by code review of `backend/acp/routes.py::razorpay_webhook` (signature verify → parse → resolve Payment by link id → `finalize_in_db`, identical call shape to the `/poll` route) that the wiring itself is correct; only the CAPTURE branch specifically requires a link that is *actually* paid, which this synthetic test never does.
**Fix applied:** none to application code — weakening `finalize_in_db` to trust the webhook body would reopen exactly the "hardcoded payment status" defect class the design and this session's RULES explicitly forbid. Classified as **test**, per the required environment/configuration/provider/test/code taxonomy. The other two webhook tests in the same file (bad-signature → 400, unrelated event → ignored/200) passed and cover the webhook-specific logic (signature verification + event parsing) that this suite exists to prove; the CAPTURE branch is exercised identically by `/poll` in `test_t1_execution_spine.py::test_full_purchase_via_link_and_poll`, which passed against a real payment (`pay_TWkLvMoLvMxfHz`).
**Files touched:** none.
**Would this recur elsewhere?:** yes, any time this specific test is run without first driving a real payment through the fixture's link — either fix the test to actually pay the link (e.g. via Playwright autopay before asserting) or repoint it at an already-CAPTURED session to legitimately exercise the ALREADY branch. Left as-is per this session's instruction not to modify tests/code without an established application defect.

**Follow-up [2026-09-01], closes the above:** per human instruction, renamed the test to `test_webhook_signed_event_does_not_capture_unpaid_link` and rewrote its assertion to expect `NOT_PAID` instead of `CAPTURE`/`ALREADY` — it now documents the security property directly (a validly signed webhook cannot move money for a link that was never actually paid) instead of asserting a webhook-trust model the app deliberately doesn't implement. No application code changed. `backend/tests/integration/test_t1_webhook.py`: 3/3 passed (bad signature 400, unrelated event ignored, renamed test → NOT_PAID). The genuine CAPTURE/ALREADY path stays proven by `test_full_purchase_via_link_and_poll` (real payment `pay_TWkLvMoLvMxfHz`) and the manual paid-link webhook proof (`pay_TWkYAahx5AQNBm`) from the prior report.

---
## T2-E1 — Discount double-counted in margin
**Symptom:** unit test `test_within_budget_recovers` expected an offer; engine returned
NO_OFFER (PRICE_ABOVE_CEILING) because resulting margin came out 10% instead of 20%.
**Root cause:** the discount amount was added to COGS AND the sale price was already
reduced by that same discount — the concession was counted twice.
**Fix:** split concessions — added-cost (return/shipping) raise COGS; price-reduction
(discount) only lowers the sale price. Margin = margin_bps(final_price, cogs+added_cost);
discount give-up tracked separately for ranking. Re-verified against the TRD §8 example.
**Recurrence risk:** medium — any new lever must be classified added-cost vs
price-reduction. The margin-floor and stacking unit tests would catch a regression.

## [2026-09-02 T2] `scripts/run_buyer_session.py` never loaded `.env` — every direct invocation died on `DATABASE_URL is not set`
**Where:** `scripts/run_buyer_session.py` (module top)
**What happened:** running the T2 integration checklist exactly as documented (`python scripts/run_buyer_session.py --setup`) failed immediately: `RuntimeError: DATABASE_URL is not set. Use the Supabase *pooler* URI (port 6543)...`, even though `.env` was fully populated in the project root.
**Root cause:** only `alembic/env.py` and `backend/tests/conftest.py` ever call `load_dotenv()`. Under `docker compose up` this is masked because `env_file: [.env]` injects the vars straight into the container process, so `backend/api/main.py` never needed to load it either — but a bare `python scripts/run_buyer_session.py`, which is exactly how SKILL.md/BUILD_PROMPT.md instruct the T2 checks to be run, has no such injection and left every `os.getenv(...)` call reading an empty environment.
**Fix applied:** added the same `load_dotenv(Path(__file__).resolve().parents[1] / ".env")` call (matching `conftest.py`'s pattern) at the top of `scripts/run_buyer_session.py`, before any `backend.*` import that reads env vars at call time. `scripts/sign_cart.py` needed no separate fix — it runs in-process via `sign_cart_main()`, so the same process-wide load covers it.
**Files touched:** `scripts/run_buyer_session.py`
**Would this recur elsewhere?:** yes — any future script meant to be run directly (not via docker or pytest) needs the same guard. Worth a one-line note in SETUP_GUIDE.md.

## [2026-09-02 T2] Windows console (cp1252) crashed on the ₹ sign mid-run
**Where:** `scripts/run_buyer_session.py` (stdout printing)
**What happened:** after fixing the `.env` load, `--ab` got through the baseline arm and died on the engine arm: `UnicodeEncodeError: 'charmap' codec can't encode character '₹' in position 65: character maps to <undefined>`.
**Root cause:** Windows terminals default Python's stdout to the process codepage (cp1252 here), which has no `₹` glyph; the script prints `₹{amount}` directly. Not a Linux/Mac issue — the codepage default there is normally UTF-8.
**Fix applied:** `sys.stdout.reconfigure(encoding="utf-8")` / same for `stderr`, guarded by `hasattr`, added right after the dotenv load. Makes the same script run unmodified on Windows, Linux, and Mac.
**Files touched:** `scripts/run_buyer_session.py`
**Would this recur elsewhere?:** yes — any script/CLI entrypoint that prints ₹ (or other non-cp1252 glyphs) and might be run on a Windows judge machine should carry the same guard. `frontend/` is unaffected (browser rendering, not a Windows console).

## [2026-09-02 T2] Real capture committed with `razorpay_payment_id = None` — Razorpay's `payments[]` sub-resource lags its own `status: "paid"` field
**Where:** `backend/payments/finalize.py::finalize_in_db`
**What happened:** the first live `--pay` recovery run genuinely settled (confirmed independently via `client.payment_link.fetch()` moments later: `status: "paid"`, `amount_paid: 479900`, `payments: [{payment_id: "pay_TWt3KHKel5tCoC", status: "captured"}]`), but the script printed `CONVERTED payment=None`, and the DB `payment` row was committed as `CAPTURED` with `razorpay_payment_id = NULL` — permanently, because `plan_finalize` treats any subsequent poll on an already-`CAPTURED` payment as `ALREADY_CAPTURED` (correct for idempotency, but it meant this row could never self-heal).
**Root cause:** the specific `payment_link.fetch(link_id)` call that `finalize_in_db` used to decide CAPTURE happened at the exact moment Razorpay had already flipped the link's top-level `status` to `"paid"` but had not yet populated the nested `payments[]` array with the real payment record — a real eventual-consistency lag on Razorpay's side, reproduced by fetching the identical link a few seconds later and seeing `payment_id` appear. `RazorpayTestProvider.fetch_payment_link` derives `payment_id` solely from that array, so it returned `None` even though `link.status == "paid"`.
**Fix applied:** in `finalize_in_db` (the integration-tested DB wrapper — the pure, unit-tested `plan_finalize` planner was left untouched, so all 176 unit tests still pass unmodified), added a guard: if the plan says `CAPTURE` but `link.payment_id` is falsy, do **not** commit a capture — return early with `NOT_PAID_YET` (reused, not a new enum value) so the session/payment state is untouched and the next poll (5s later in the script's loop; or the webhook) retries and captures once the real `payment_id` is actually visible. Re-ran the full `--pay` recovery scenario end to end post-fix: captured correctly with a real id (`pay_TWt6UiNcAvKdC5`), and the payment-gated pytest test (`test_full_autonomous_recovery_settles`) also passed live with a second, independent real id (`pay_TWtAK5RcWte4Mn`).
**Files touched:** `backend/payments/finalize.py`
**Would this recur elsewhere?:** yes — any other place that reads `payment_link.fetch()` or a webhook payload and assumes `status` and the nested payment sub-record are consistent at the same instant should apply the same defer-and-retry pattern rather than trusting them as atomic. The webhook path (`backend/payments/webhook.py`) already carries the payment_id directly in its own payload (`payment.entity.id`), so it is not exposed to this specific race, but should be reviewed if its parsing is ever changed to fall back to a link fetch.

## [2026-09-02 T2] Demo script printed `payment=None` after a real capture — wrong JSON key
**Where:** `scripts/run_buyer_session.py::run_arm`
**What happened:** even after the `finalize.py` fix above produced a genuinely correct capture, the script's own console output still read `CONVERTED payment=None` — misleading for anyone watching the live demo.
**Root cause:** `POST /acp/checkout_sessions/{id}/poll` (`backend/acp/routes.py::poll_checkout`) returns the field as `razorpay_payment_id` in `CompleteCheckoutResponse`; the script read `pr.get("payment_id")`, a key that never existed in the response, so it silently always returned `None` regardless of whether settlement succeeded.
**Fix applied:** read `pr.get("razorpay_payment_id")` instead. Verified live: subsequent run printed `CONVERTED payment=pay_TWtAK5RcWte4Mn`, matching the DB row.
**Files touched:** `scripts/run_buyer_session.py`
**Would this recur elsewhere?:** no — isolated to this one print/return site; the API schema itself (`backend/acp/schemas.py`) was already correct and unchanged.

## [2026-09-02 T3] Buyer chat: follow-up edits re-parsed from scratch, losing the standing intent
**Where:** `backend/console/buyer_chat.py`, `backend/console/buyer_session.py`, `/console/buyer/chat/extract`
**What happened:** user: "trainers under ₹4,500, open to a bundle" (parsed fine), then
"no min return should be 5 days" → the card reverted to "running shoes under ₹5,000, min return 0".
The edit was lost AND the price/category reset to defaults.
**Root cause:** two bugs. (1) `extract_intent` always returned ALL fields with defaults, so a
follow-up with no price/category re-defaulted them — there was no notion of editing a prior intent.
(2) the return regex only matched "N-day returns" (number BEFORE "return"); "min return should be
5 days" has the number AFTER the word, so returns weren't detected either.
**Fix applied:** split parsing into `detect_fields()` which returns ONLY the fields a message
actually mentions (partial), added order-agnostic return/delivery patterns (number before OR after
the keyword), and added a pure `merge_intent(prior, text)` that applies just the detected fields onto
the standing intent (tolerances unioned). The extract endpoint now takes an optional `prior` and the
LLM path merges too. The exact failing transcript is now a regression test.
**Files touched:** buyer_chat.py, buyer_session.py, console/routes.py, test_console_modules.py
**Would this recur?:** low now — 5 merge tests pin it, including the verbatim failure and a
no-op ("ok sounds good" changes nothing). Any new constraint field must be added to detect_fields.

## [2026-09-02 T3] Sessions never flipped to CONVERTED after the link was paid post-hoc
**Where:** `backend/console/routes.py` (sessions/overview read paths)
**What happened:** completing a hosted payment link in the browser after leaving the page left the
session showing PENDING/OPEN in the console — it never advanced to CONVERTED.
**Root cause:** a session only advances when something POLLS Razorpay after settlement (the same
eventual-consistency reason the T2 finalize fix exists). The buyer chat polls; the Sessions/Overview
read paths just read stale DB status, so a link paid "later" was never reconciled.
**Fix applied:** added `POST /console/sessions/reconcile` that finds OPEN + PENDING_PAYMENT sessions
and calls the tested `/acp/.../poll` for each (idempotent, converges with the webhook). The console
Transactions view calls it on load + manual refresh, so post-hoc payments show up. No duplication of
finalize logic — it reuses the proven poll path.
**Files touched:** console/routes.py (frontend calls it; wired in the T3 rebuild)
**Would this recur?:** low — polling is the system's source of truth for settlement; any new view of
payment status should reconcile or rely on a view that does.

## [2026-09-02 T3-FE2] `uvicorn backend.api.main:app` 500s on every DB route: DATABASE_URL is not set
**Where:** `backend/api/main.py` (app entrypoint); surfaced via `GET /console/overview` -> merchant
Home showing "Couldn't reach the gateway / 500 on /console/overview" against the live console.
**What happened:** ran the exact documented command from HANDOFF_T3_FRONTEND.md — `uvicorn
backend.api.main:app --reload --port 8000` from repo root with a real `.env` present — and every
route touching the DB threw `RuntimeError: DATABASE_URL is not set. Use the Supabase *pooler* URI
(port 6543) for the application.` from `backend/db/session.py::_database_url`.
**Root cause:** `.env` is never loaded into the actual app process. `backend/db/session.py` reads
credentials lazily via plain `os.getenv(...)`, and the only places that ever called
`dotenv.load_dotenv()` were `backend/tests/conftest.py`, `alembic/env.py`, and `scripts/*.py` — the
exact same class of bug already logged above (2026-09-01/02 T3, tests silently skipping) but this
time in the one entrypoint (`backend/api/main.py`) that was never patched, so `uvicorn` on its own
never sees the Supabase/Razorpay/Gemini/Upstash credentials unless the shell happens to have them
exported already.
**Fix applied:** added the same `load_dotenv(Path(__file__).resolve().parents[2] / ".env")` call
used by conftest.py, at the very top of `backend/api/main.py`, before the router imports that
transitively import `backend/db/session.py`. Chose the app entrypoint (not a new shared config
module) to keep the fix minimal and match the codebase's existing per-entrypoint dotenv pattern
rather than introduce a new abstraction. Verified with a **clean, non-reloaded** process — the
first `--reload` run after the edit still 500'd because WatchFiles' "Reloading..." on Windows
logged but did not actually spawn a new worker PID (same 3 PIDs, same stack trace, same line
numbers, before and after); killing all `python` processes on port 8000 and starting fresh fixed
it. `GET /console/overview` and `GET /health` both return 200 with real Supabase data after.
**Files touched:** backend/api/main.py
**Would this recur elsewhere?:** yes — any other direct `uvicorn`/`gunicorn` entrypoint added later
(e.g. a worker process, a second ASGI app) needs the same explicit `load_dotenv()`, since nothing
in `backend/` auto-loads `.env` at import time. Separately: don't trust `uvicorn --reload` on
Windows to have actually restarted after an edit — check the worker PID changed (or just restart
the process) before concluding a code change didn't take effect.

## [2026-09-02 T3-FE2] Rail gate ledger, gate-log, and overview deny_reasons all read an unpopulated `event` table
**Where:** `backend/console/routes.py` — `overview()`, `session_detail()`, `gate_log()`.
**What happened:** Rail -> Sessions/[id] always showed "No gate events recorded" even for real,
audited sessions (both a live buyer-chat CONVERTED session and a real DENIED one). Merchant Home's
deny-reason breakdown showed only 3 denies (`{"PRICE_ABOVE_CEILING": 3}`) while the same page's
funnel showed "Denied at gate: 27" one card over — an internal contradiction on one screen. Rail ->
Gate log matched the same stale 3.
**Root cause:** three read endpoints queried `backend.models.Event` (`__tablename__ = "event"`) for
gate PASS/DENY rows, but grepping the entire non-test codebase found **no call site that ever
inserts into `Event`** — `db.add(Event(...))` exists only in integration tests
(`test_t0_supabase.py`, `test_t1_webhook.py`). The real gate call site
(`backend/acp/orchestration.py:146`) writes every decision to `AuditLog` instead
(`audit.append(session_id, f"GATE_{'PASS' if result.passed else 'DENY'}", {"reason_code":...,
"check_index":..., "cart_hash":...})`) — confirmed working correctly and hash-chained (this is what
Disputes/Provenance already read, correctly). The 3 rows that *did* show up were orphans left in
the shared Supabase DB by a past integration-test run against the never-wired `event` table, not
live traffic — a coincidence that made the bug look like partial data instead of a total miss.
**Fix applied:** pointed all three endpoints at `AuditLog` instead: `overview()`'s deny aggregation
now selects `action == "GATE_DENY"` rows and reads `reason_code`/`check_index` out of `detail`;
`session_detail()`'s gate ledger selects `action IN ("GATE_PASS","GATE_DENY")` for that session,
ordered by `seq`; `gate_log()` selects `action == "GATE_DENY"` ordered by `ts desc`. No change to
`backend/gateway/` or `backend/acp/orchestration.py` — the write side (and the determinism guard)
were already correct; only the disconnected read side moved to the table that's actually written.
Verified: `deny_reasons` now sums to 27 (matching the funnel card), the CONVERTED test session's
ledger shows its real `GATE_PASS` row, `/console/gate-log` count went 3 -> 27 with a full
by-reason breakdown. Full unit suite re-run after the fix: still 205 passed, 0 failed.
**Files touched:** backend/console/routes.py
**Would this recur elsewhere?:** the `Event` model + `event` table are now fully unreferenced by
application code (only by integration tests). Either wire real inserts at the gate call site if a
denormalized per-check ledger is wanted later, or remove the model/table in a future migration —
leaving it half-wired invites the same bug again for the next endpoint someone points at it.

## [2026-09-02 T3-FE3] Every `.stagger` list rendered rows with INVISIBLE text (stuck at opacity:0)
**Where:** `frontend/app/globals.css` (`.stagger`), affecting merchant Home (recent transactions,
lever effectiveness), Products, Offers, Transactions, and Rail Sessions / Gate log — every list.
**What happened:** rows were present in the DOM but their text was completely invisible. (a teammate
Code saw this during live testing and concluded it was a "screenshot-capture artifact" because
computed styles looked normal in its tool — but the user confirmed it is real and visible in a
normal browser.)
**Root cause:** `.stagger > *` set `opacity: 0` and relied on `animation: fade-up ... both` to bring
it back to 1. The `fade-up` keyframes were declared ONLY in `tailwind.config.ts`. Tailwind emits a
`@keyframes` block only when the matching `animate-*` utility class is scanned in content — but the
code drives the animation via raw `animation: fade-up` (in the hand-written `.stagger` rule and in
inline `style={{animation:"fade-up ..."}}`), never via the `animate-fade-up` class. So Tailwind's
JIT never emitted `@keyframes fade-up`; the animation name resolved to nothing; the animation was
ignored; and the base `opacity: 0` was never overridden → invisible rows everywhere. Confirmed by
grepping the built CSS: no `@keyframes fade-up` was present before the fix.
**Fix applied:** (1) defined `@keyframes fade-up` (and a spare `acg-shimmer`) directly in
globals.css so it exists regardless of Tailwind's content scan; (2) REMOVED the base `opacity: 0`
from `.stagger > *` and rely on `animation-fill-mode: both` (the keyframe's `from` supplies the
hidden start during the delay). This inverts the failure mode: if the animation ever fails again,
elements default to VISIBLE, never invisible. Verified in the compiled output — the built CSS now
contains `@keyframes fade-up{...}` and `.stagger>*{animation:fade-up .34s var(--ease) both}` with no
opacity:0.
**Files touched:** frontend/app/globals.css
**Would this recur?:** low, and now fail-safe. Rule of thumb captured: if you drive a keyframe via
raw `animation:` rather than the `animate-*` class, define the `@keyframes` in real CSS — don't rely
on Tailwind to emit it. Never gate content visibility on an animation completing.

## [2026-09-02 T3-FE3] UI read as flat + slow first paint
**Where:** `frontend/app/globals.css` tokens, `tailwind.config.ts` shadows, `app/layout.tsx`.
**What happened:** cards had no perceptible depth and every page felt slow to load.
**Root causes:** (a) dark theme set `--shadow: 0 0 0`, so all drop-shadows were pure black at low
alpha over a #0e1116 surface — effectively invisible, hence flat; (b) fonts were pulled via a CSS
`@import` at the top of globals.css, which is discovered late and serialized, blocking first paint.
The invisible-rows bug above also made pages *feel* stuck-loading.
**Fix applied:** (a) dark `--shadow` → deep blue-black `3 5 9`, bumped the inset top-highlight
(0.04→0.06) and added a `card` shadow (highlight + soft drop) used by Panel; crisper `--line`; Panel
now uses a subtle top-to-bottom gradient + hover border. (b) replaced the `@import` with a
preconnect + preload + async `<link>` in `<head>` (display=swap) so the stylesheet is fetched in
parallel and never blocks text render. Also swapped the per-MetricCard recharts sparkline for a
cheap inline SVG (one fewer ResponsiveContainer per card), and cut the oversized authorization-space
SVG from Analytics.
**Files touched:** frontend/app/globals.css, tailwind.config.ts, app/layout.tsx, components/ui.tsx,
components/charts.tsx, app/(app)/merchant/analytics/page.tsx (+ deleted authorization-space.tsx).
**Would this recur?:** low. Note for dark UIs: depth comes from top-highlight insets + surface
luminance steps, not black drop-shadows.

---
# Resolution pass (2026-09-03, the team) — fixes for the deep-review findings

Every finding above was re-verified against the code and fixed unless noted. Backend
verified by unit tests (218 pass, determinism guard green) + deep-import; frontend by
`tsc --noEmit` + `next build` (17 routes). Items marked **[needs-live]** are correct in
code but can't be exercised without live Postgres/Razorpay here — flagged for the team.

## CRITICAL — consent, binding, scoping (all FIXED)
- **Self-signed Intent Mandate** → `verify_intent_mandate` now pins the user-role signer
  (`expected_signer_kid=USER_KID`), rejecting agent/merchant self-signs as
  `SIGNER_NOT_AUTHORIZED` *before* signature verification. +6 unit tests. (verify.py, routes.py)
- **Category not enforced at /complete** → `category_in_scope` now compares the product to the
  signed `ceiling.constraints.category`, not the merchant-wide `ALLOWED_CATEGORIES`. (routes.py)
- **Cart session_id not bound** → `/complete` rejects a cart whose signed `session_id` ≠ the URL
  session with `SESSION_MISMATCH`. (routes.py, gate.py taxonomy)
- **Repeated /complete → multiple links** → already-completed guard: refuses when the session has
  left OPEN or already has a Payment row (`SESSION_ALREADY_COMPLETED`). (routes.py)
- **Audit seq race → 500s** → `_current_head()` now takes a `FOR UPDATE` row lock (Postgres-only,
  dialect-guarded) so seq allocation is serialized. **[needs-live]** for the concurrency proof.
  (audit/chain.py)
- **Global cross-merchant aggregation** → added `Session.merchant_id` (+migration
  `0002_session_merchant_id` with a safe backfill), populated at session creation from
  `active_merchant`, and scoped `_session_rows`/overview/analytics/sessions to it (NULL = legacy,
  still visible). **[needs-live]** run `alembic upgrade head`. (models, routes.py, migration)
- **Policy patch accepts negatives** → `Field(ge=0, le=…)` bounds on `PolicyPatch` and
  `BandPreview`. (routes.py)
- **Mandate TTL unbounded vs nonce window** → `verify_intent_mandate` caps expiry at
  `MAX_MANDATE_TTL_SECONDS` (default 3600). +unit tests. (verify.py)

## Offer Engine (FIXED)
- **False NO_OFFER / no fallback / RETURN_ABOVE_BAND on native returns** → `build_offer` now
  filters as-is+bundle candidates through `_offer_clears_band` (margin floor + budget + extension-
  only return cap) and picks the best *legal* one, falling through to recovery otherwise;
  `validate_offer`'s `RETURN_ABOVE_BAND` is scoped to `RETURN_EXTENSION`/`MULTI`. +4 tests.
- **As-is margin floor not pre-checked** → same `_offer_clears_band` filter enforces the floor
  before selection.
- **Bundle clamp bypassed the discount budget** → the clamp is now recorded as
  `total_discount_paise`, so the anti-stacking budget check sees it (and pure as-is standing-promo
  sales are explicitly NOT budget-checked, preserving correct promo pricing). (engine.py, bounds.py)

## Robustness / validation (FIXED)
- **Malformed UUID → 500 on 4 endpoints** → UUID-parse-or-404 guards on complete/poll/session_detail/
  audit_trail. (routes.py ×2)
- **/buyer/chat/run unvalidated → 500** → `RunReq` field-validator returns a clean 422 naming the
  bad field. (routes.py)
- **Webhook non-JSON body → 500** → `json.loads`+`parse_event` wrapped, returns 400. (routes.py)
- **Webhook `notes: null` → AttributeError** → coerce `notes` None→{}; +tests. (webhook.py)
- **Webhook reference_id lossy** → read `session_id` straight from link/order notes (written at
  creation) with an exact CartMandate match; prefix-split kept only as last resort. (webhook.py, routes.py)
- **finalize_in_db silent no-op** → explicit `payment is None` branch audits `FINALIZE_NO_PAYMENT`
  and returns NOT_PAID_YET (retry) instead of misreporting CAPTURE. (finalize.py)
- **Two-nonce not atomic as a pair** → verify both fresh before consuming either; best-effort
  release of the cart nonce if the token consume still loses a race. (orchestration.py)
- **reconstruct_trail 200 on nonexistent session** → `audit_trail` 404s when the Session row
  doesn't exist. (routes.py)
- **Injection SKU hardcoded literal** → content-based detection (`_is_injection_product` scans the
  description for override markers), catalog-agnostic. (routes.py)
- **run_buyer_session.py /tmp not portable** → `tempfile.mkdtemp()` instead of hardcoded /tmp.
- **active_config global-version** → scoped to the active merchant's own latest version. (offer_wire.py)
- **_session_rows arbitrary CartMandate** → `.order_by(created_at.desc())`. (routes.py)
- **conversion rate diluted by OPEN** → denominator is terminal sessions only. (metrics.py)
- **margin_histogram drops 0bps** → `is not None` instead of truthiness. (metrics.py)

## Frontend (FIXED)
- **Buyer double-submit on remount** → in-flight derived from shared `thread.phase`, not local busy.
- **Stale-closure wipes turns** → catch/poll build on a LOCAL `turns` accumulator, never the stale prop.
- **Infinite poll** → iteration cap (60) + cancel-on-unmount ref (stops on thread switch / navigate away).
- **useData stale responses** → monotonic request-id + mounted guard; only the latest write wins.
- **Reports/Home one-sided error checks** → both `ov` and `an` errors surfaced, correct retry target.
- **₹0 price → Infinity% margin** → `Field(gt=0)` on `NewProduct` + `price > 0 ?` guard client-side.
- **effective_price falsy-zero** → `!= null` / `is not None` on both read and write paths.
- **paise() sign** → minus placed before the ₹ symbol.
- **Webhooks fake base URL** → shows the real same-origin `/api` proxy note, not an unset env var.

## Deferred (documented, not changed — scalability/informational, per the review's own classification)
- **/console/audit-trail full-ledger fetch** and **_session_rows N+1**: correct-by-design (global
  hash chain must verify from genesis); left as-is with this note. Revisit with incremental
  verification / pagination only if load-tested past demo volume.
- **backend/insight/ empty stub**: SKILL.md labels it upside scope; the numeric functionality lives
  in metrics.py. Left as-is; don't claim the named module in the demo.
