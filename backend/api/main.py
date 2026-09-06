"""FastAPI entrypoint. T1: health + ACP routes (feed, checkout sessions)."""
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root before anything reads os.getenv(...).
# backend/db/session.py, backend/gateway/*, etc. all read env vars lazily via
# os.getenv, so nothing loaded credentials for the real `uvicorn` process
# (only conftest.py / alembic/env.py / scripts did). Same root cause as
# ERRORS.md's 2026-09-01 test-skip entry, but for the app entrypoint itself.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

import os

from fastapi import FastAPI

from fastapi.middleware.cors import CORSMiddleware

from backend.acp.routes import router as acp_router
from backend.console.routes import router as console_router

# Production hardening for a public deploy:
#  - hide the interactive API docs (don't advertise the surface to scanners)
#  - restrict CORS to the deployed frontend origin(s)
# Both are controlled by env so local dev stays convenient.
_PROD = os.getenv("ACG_ENV", "dev").strip().lower() in ("prod", "production")
_ORIGINS = [o.strip() for o in os.getenv("ACG_ALLOWED_ORIGINS", "*").split(",") if o.strip()] or ["*"]

app = FastAPI(
    title="Agent Storefront — Agentic Commerce Gateway",
    version="1.0.0",
    docs_url=None if _PROD else "/docs",
    redoc_url=None if _PROD else "/redoc",
    openapi_url=None if _PROD else "/openapi.json",
)
app.add_middleware(CORSMiddleware, allow_origins=_ORIGINS, allow_methods=["*"],
                   allow_headers=["*"])


# Demo gate: protect the merchant + rail console (data views AND mutations like
# policy/catalog edits) behind a single shared password from the environment. The
# BUYER chat stays open so judges can try it — in simulate mode it can't burn any
# quota. If ACG_CONSOLE_PASSWORD is unset, gating is off (local dev).
_CONSOLE_PW = os.getenv("ACG_CONSOLE_PASSWORD", "").strip()
_OPEN_PREFIXES = ("/console/buyer/chat/",)


@app.middleware("http")
async def _console_gate(request, call_next):
    if _CONSOLE_PW:
        path = request.url.path
        if path.startswith("/console/") and not any(path.startswith(p) for p in _OPEN_PREFIXES):
            if request.method != "OPTIONS" and request.headers.get("x-console-key", "") != _CONSOLE_PW:
                from starlette.responses import JSONResponse
                return JSONResponse({"detail": "console locked"}, status_code=401)
    return await call_next(request)


app.include_router(acp_router)
app.include_router(console_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "tier": "T1", "planes": ["commerce", "execution", "observability"]}
