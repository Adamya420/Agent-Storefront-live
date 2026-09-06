# WEBHOOK_SETUP.md — Payment Link + Polling + Webhook (step by step)

This is how settlement works now, and how to set it up. Do the steps in order.

## What changed and why (30-second version)

Your Razorpay test account does **not** have S2S enabled, so the backend can't
create a payment purely server-side. Instead we **create a Payment Link** (works
with ordinary test keys), and detect that it was paid two ways:

- **Polling** — the backend calls `payment_link.fetch` until status is `paid`.
  Needs nothing public; works entirely on your laptop. This is the **default**.
- **Webhook** — Razorpay POSTs `payment_link.paid` to your `/acp/webhooks/razorpay`
  endpoint. Needs a **public URL** (a tunnel), verifies a signature, and is the
  production-grade path.

Both converge on **one idempotent finalize step**, so it's safe if both fire.

> **Autonomy note:** the human signs the mandate once; completing the link is a
> **test-mode stand-in for the autonomous settlement rail** (UPI Reserve Pay/UAP),
> not the human re-entering the loop. For a hands-free demo, use the Playwright
> autopay helper (Part E).

---

## Part A — env vars

Add/confirm these in `.env` (see `.env.example`):

```
ACG_CAPTURE_MODE=link
RAZORPAY_WEBHOOK_SECRET=            # fill in Part C, step 4
ACG_POLL_TIMEOUT=120               # optional, seconds the integration test waits
```

Install deps (the integration env needs the full set):

```bash
pip install -r requirements.txt
```

---

## Part B — Polling path (no public URL — start here)

Polling needs no setup beyond your existing keys. To test the whole flow locally:

```bash
# 1. run the backend
uvicorn backend.api.main:app --reload

# 2. in another terminal, run the full-purchase integration test.
#    It creates a link, PRINTS the URL, and polls for ACG_POLL_TIMEOUT seconds.
pytest backend/tests/integration/test_t1_execution_spine.py::test_full_purchase_via_link_and_poll -v -m integration -s
```

When it prints `>>> COMPLETE THIS PAYMENT LINK ...`, open that URL in a browser,
pay with a **test method** (UPI id `success@razorpay`, or test card
`4111 1111 1111 1111`, any future expiry, any CVV). The poller then returns
`CONVERTED` with a real `razorpay_payment_id`. Confirm it in the Razorpay
**Test mode → Transactions** dashboard.

`-s` is required so you can see the printed link URL.

---

## Part C — Webhook path (public URL + signature)

### C1. Expose your local backend with a tunnel

Webhook URLs must use port 80/443, so you need a public HTTPS URL to your local
`:8000`. Use cloudflared (no signup) or ngrok.

**cloudflared (recommended, no account):**
```bash
# install (mac)                     # install (windows: winget install --id Cloudflare.cloudflared)
brew install cloudflared
# with the backend running on :8000, start a tunnel:
cloudflared tunnel --url http://localhost:8000
```
It prints a URL like `https://random-words.trycloudflare.com`. Your webhook URL is
that + `/acp/webhooks/razorpay`.

**ngrok (needs a free account + authtoken):**
```bash
ngrok config add-authtoken <YOUR_TOKEN>
ngrok http 8000
```
Use the printed `https://….ngrok-free.app/acp/webhooks/razorpay`.

Keep this terminal open — the tunnel must stay up while testing webhooks.

### C2. Register the webhook in the Razorpay dashboard (Test mode)

1. Razorpay Dashboard → make sure the mode toggle is **Test Mode**.
2. **Settings → Webhooks → Add New Webhook**.
3. **Webhook URL:** paste your tunnel URL + `/acp/webhooks/razorpay`
   (e.g. `https://random-words.trycloudflare.com/acp/webhooks/razorpay`).
4. **Secret:** enter any strong string you choose (e.g. generate one below). This
   is the secret Razorpay signs with — copy the SAME value into `.env` as
   `RAZORPAY_WEBHOOK_SECRET`.
   ```bash
   python -c "import secrets; print('whsec_'+secrets.token_hex(24))"
   ```
5. **Active Events:** check **`payment_link.paid`** and **`payment.captured`**.
6. Save. Restart the backend so it picks up the new `RAZORPAY_WEBHOOK_SECRET`.

### C3. Verify the webhook endpoint (signed simulation — no tunnel needed)

This proves your signature verification + finalize wiring without waiting on
Razorpay:

```bash
pytest backend/tests/integration/test_t1_webhook.py -v -m integration
```
Expected: bad-signature rejected (400), unrelated event ignored (200), and a
signed `payment_link.paid` finalizes a pending link (`CAPTURE` or `ALREADY`).

### C4. Real end-to-end webhook

With the tunnel up and the webhook registered:
```bash
# create a pending link via the flow, print its URL:
pytest backend/tests/integration/test_t1_execution_spine.py::test_full_purchase_via_link_and_poll -v -m integration -s
```
Open the printed URL and pay it. Razorpay will POST `payment_link.paid` to your
tunnel → your endpoint verifies the signature and finalizes. You can watch the
webhook delivery + response code under **Settings → Webhooks → (your webhook) →
Recent Deliveries** in the dashboard.

> Polling and the webhook can BOTH finalize the same payment; the second is a
> safe no-op by design. That's intentional — belt and suspenders.

---

## Part D — What the buyer flow looks like end to end

1. `POST /acp/checkout_sessions` with a signed Intent Mandate → returns an offer.
2. `POST /acp/checkout_sessions/{id}/complete` with the signed cart + token →
   gate runs, nonce is consumed, a **payment link is created**, response is
   `PENDING_PAYMENT` + `payment_link_url`.
3. The link is completed (click, Playwright, or — in production — the autonomous
   rail).
4. `POST /acp/checkout_sessions/{id}/poll` **and/or** the webhook finalizes:
   payment → `CAPTURED`, receipt written, session → `CONVERTED`.

---

## Part E — Optional: hands-free demo (Playwright autopay)

To complete the link without a human click (matches "the rail fired
automatically"):

```bash
pip install playwright
playwright install chromium
# then run the full-purchase test with autopay on:
ACG_AUTOPAY=1 pytest backend/tests/integration/test_t1_execution_spine.py::test_full_purchase_via_link_and_poll -v -m integration -s
```
The `_autopay` helper drives the hosted test page (UPI `success@razorpay`).
Selectors on Razorpay's hosted page can change; if it can't drive the page it
prints a message and you complete the link manually.

---

## Troubleshooting

- **`invalid webhook signature` (400):** the `RAZORPAY_WEBHOOK_SECRET` in `.env`
  doesn't match the secret you entered in the dashboard, or the backend wasn't
  restarted after setting it. They must be identical (test-mode secret).
- **Webhook never arrives:** tunnel down, wrong URL in the dashboard, or the event
  wasn't checked. Use polling meanwhile — it doesn't depend on the tunnel.
- **Link creation fails:** confirm the key is `rzp_test_…` and that you haven't hit
  the test-mode cap of 30 payment links per business (create fewer, or reuse).
- **Poll never returns CONVERTED:** the link wasn't actually paid, or was paid
  after `ACG_POLL_TIMEOUT`. Raise the timeout or pay faster, then re-poll.
