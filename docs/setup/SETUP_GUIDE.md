# SETUP_GUIDE.md — do everything in order, don't skip

Assumes you've done none of this before. Do steps in order; don't move on until the current one is confirmed working.

---

## Part A — Accounts (do all first)

**A1. GitHub** — create a **private** repo `acg` (or `razorpay-agent-storefront`). Keep the URL.

**A2. Supabase (your Postgres)** — at `supabase.com`, create a project. Then:
1. Project Settings → **Database** → **Connection string**. Copy **two** URIs:
   - **Transaction pooler** (host `...pooler.supabase.com`, port **6543**) → this is your `DATABASE_URL` (app).
   - **Direct connection** (port **5432**) → this is your `DIRECT_URL` (migrations).
2. Note your DB password (set/reset it here if needed). The URIs embed `postgres.<project-ref>` as the user.
3. You do **not** need the anon/service keys for this project (backend uses direct Postgres), but you can copy the Project URL if you want.

**A3. Upstash (Redis for nonces)** — at `upstash.com`, Create Database → Redis → free plan. Copy `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN`.

**A4. Google AI Studio (Gemini)** — at `aistudio.google.com`, Get API key → create key (`AIza...`). Note the free-tier limits shown (they change; trust the page).

**A5. Razorpay (test mode)** — at `razorpay.com`, sign up. Dashboard → set **Test Mode**. Settings → API Keys → **Generate Test Key**. Copy **Key ID** (`rzp_test_...`) and **Key Secret** (shown once).

**A6. Antigravity** — install from Google's official page. **A7. Claude.ai** — for planning/review. **A8. Claude Code** — install per Anthropic's docs (this runs the real integration steps).

**Checkpoint:** you have — Supabase `DATABASE_URL` + `DIRECT_URL`, Upstash URL+token, Gemini key, Razorpay Key ID+Secret — saved temporarily.

---

## Part B — Tools

Install and confirm `--version` for each: **Git**, **Python 3.11+** (check "Add to PATH" on Windows), **Node.js LTS**, **Docker Desktop** (for backend+frontend containers; Postgres/Redis are remote, so Docker is only for the app). Run all four `--version` checks in a fresh terminal before continuing.

---

## Part C — Project folder

1. `git clone <your repo url>` and `cd` in.
2. Copy the nine docs into the root: `PRD.md TRD.md AGENTS.md agent.md SKILL.md BUILD_PROMPT.md SETUP_GUIDE.md DECISIONS.md ERRORS.md`.
3. Create `.env` (leading dot) with your real values:
   ```
   DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
   DIRECT_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
   UPSTASH_REDIS_REST_URL=...
   UPSTASH_REDIS_REST_TOKEN=...
   GEMINI_API_KEY=...
   GEMINI_MODEL=gemini-2.5-flash
   RAZORPAY_KEY_ID=rzp_test_...
   RAZORPAY_KEY_SECRET=...
   MANDATE_USER_KID=user-test-1
   MANDATE_AGENT_KID=agent-test-1
   MANDATE_MERCHANT_KID=merchant-test-1
   ```
4. Create `.gitignore` with at least: `.env`, `venv/`, `node_modules/`, `__pycache__/`, `*.pyc`, `data/keys/`.
5. Create `.env.example` — same variable **names**, empty values — safe to commit.
6. First commit: `git add` the nine docs + `.gitignore` + `.env.example`; commit `"Project docs and scaffolding"`; `git push`.
7. Verify on GitHub the docs are there and that `git status` does **not** show `.env` (if it does, fix `.gitignore` before continuing).

**Checkpoint:** repo shows the docs + `.gitignore` + `.env.example`; `.env` is untracked.

---

## Part D — Hand to the coding agent

1. Open the folder in Antigravity (it auto-detects `AGENTS.md`) or start a Claude Code session in the folder.
2. First instruction: *"Read AGENTS.md, then PRD.md, then TRD.md, then agent.md, then SKILL.md, in that order, before writing any code. Then confirm back Tier 0's checkpoint criteria in your own words."* (This catches skimming.)
3. Then use **BUILD_PROMPT.md** — paste its MASTER CONTEXT block once, then the tier block you're building.

---

## Part E — Build tier by tier (golden rule: never approve a checkpoint you haven't personally verified)

For each tier (0 → 1 → 2 → 3):
1. Paste the tier's block from `BUILD_PROMPT.md`.
2. Let Claude chat author the deterministic core + unit tests; let Claude Code/Antigravity run migrations/integration.
3. **You verify the checkpoint yourself:** run `pytest backend/tests/unit -v` and `pytest backend/gateway/tests -v`; for integration, actually look — open the Razorpay **test** dashboard and confirm a real payment; read 3–5 event logs; confirm the audit chain reconstructs the purchase.
4. Only then: *"Confirmed, Tier N passed. Commit, push, proceed to Tier N+1."*
5. Never let an agent chain through tiers without your approval between them.

**When to use Claude chat vs Claude Code:** Claude chat for planning, authoring the deterministic core, unit tests, and debugging from pasted logs (it cannot reach Supabase/Razorpay/Gemini/Upstash). Claude Code/Antigravity for anything touching those real services and for integration tests. See `agent.md`.

---

## Final checklist before "done"

- [ ] All Part A keys are in local `.env` only, never committed.
- [ ] All Part B tools show working `--version`.
- [ ] GitHub has every doc, `.gitignore`, `.env.example`, and stays updated at every checkpoint.
- [ ] You personally verified every `SKILL.md` checkpoint for Tiers 1–2 (and 3 if built).
- [ ] `docs/what_broke.md` reflects a real `ERRORS.md` entry.
- [ ] Every non-goal (`PRD.md §4`) is visible where a judge will see it.
- [ ] 5-minute video recorded per `TRD.md §13`; repo pushed final.
