# Deploying Agent Storefront (free tier)

**Frontend → Vercel · Backend → Render · DB/Redis stay on Supabase + Upstash.**

This build is deploy-safe: settlement defaults to **simulate** (no Razorpay keys, no
payment links, no quota can be burned), the merchant + rail console is behind a
password, the buyer chat is open, and API docs are hidden in production.

---

## 1. Backend on Render (free web service)

New → **Blueprint** (uses `render.yaml`) or New → **Web Service** with:

- **Build command:** `pip install -r requirements.txt && python data/generate_keys.py`
  (the key-gen step is required — ES256 keys are gitignored, so a fresh deploy has none)
- **Start command:** `uvicorn backend.api.main:app --host 0.0.0.0 --port $PORT`
- **Health check path:** `/health`

**Environment variables:**

| Var | Value |
|-----|-------|
| `ACG_SETTLEMENT_MODE` | `simulate` |
| `ACG_ENV` | `prod` |
| `ACG_ALLOWED_ORIGINS` | your Vercel URL, e.g. `https://agent-storefront.vercel.app` |
| `ACG_CONSOLE_PASSWORD` | a password of your choice (backend-only) |
| `DATABASE_URL` | Supabase **pooler** URI (port 6543) |
| `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN` | from Upstash |
| `GEMINI_API_KEY` | your Gemini key |
| `ACG_MERCHANT_ID` | `merchant://acg-sports` |
| `MANDATE_USER_KID` / `MANDATE_AGENT_KID` / `MANDATE_MERCHANT_KID` | `user-test-1` / `agent-test-1` / `merchant-test-1` |

**Do NOT set `RAZORPAY_*`.** With no Razorpay keys and `ACG_SETTLEMENT_MODE=simulate`,
the deploy cannot create a payment link or touch the 30-link cap.

## 2. Seed the catalog (once)

The seeded merchant + catalog live in Supabase, so if your DB is already seeded from
development, nothing to do. On a fresh DB: run `alembic upgrade head` (direct URL, port
5432), then open the console and **Merchant → Products → Import CSV** with
[`data/demo-catalog.csv`](../../data/demo-catalog.csv).

## 3. Frontend on Vercel (free Hobby)

Import the repo:

- **Root directory:** `frontend`
- **Framework:** Next.js (auto-detected)
- **Environment variable:** `ACG_API_BASE = https://<your-backend>.onrender.com`

## 4. Wire them together

1. Set Vercel's `ACG_API_BASE` to the Render URL; redeploy the frontend.
2. Set the backend's `ACG_ALLOWED_ORIGINS` to the Vercel URL; redeploy the backend.
3. Open the app. The **buyer chat** works with no password. **Merchant/rail** prompt for
   `ACG_CONSOLE_PASSWORD` (share it only if you want judges to explore those tabs).

## 5. Keep it awake (optional)

Render's free service sleeps after 15 min idle (~30–60s cold start). Point a free
uptime pinger (UptimeRobot / cron-job.org) at `https://<backend>.onrender.com/health`
every ~10 minutes during judging. Hit `/health` once before you record or demo.

## Notes

- **Suggested prompts** in the buyer chat are tuned to `data/demo-catalog.csv`; each one
  triggers a different offer-engine lever, so keep that catalog seeded.
- To capture a **real** Razorpay payment for a video, deploy a *separate* instance (or run
  locally) with `ACG_SETTLEMENT_MODE=razorpay` and real test keys — never on the public one.
