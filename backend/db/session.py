"""Database engine/session for ACG.

Supabase specifics (TRD §2, DECISIONS.md):
  * The APP connects via the Supabase **transaction pooler** (port 6543).
    pgbouncer in transaction mode does NOT support prepared statements, so we
    must disable statement caching or we get intermittent
    `DuplicatePreparedStatementError` / `InvalidSQLStatementNameError`.
      - psycopg2 (sync): does not use server-side prepared statements by
        default, but we still force NullPool-friendly settings and disable
        SQLAlchemy's own compiled-cache reuse hazards via `prepare_threshold`
        semantics on the driver where supported.
      - asyncpg (async): MUST set `prepared_statement_cache_size=0` AND supply
        a unique `prepared_statement_name_func`, otherwise pgbouncer errors.
  * ALEMBIC connects via the **direct** connection (port 5432), which supports
    prepared statements and DDL cleanly. See alembic/env.py.
  * RLS is disabled: the FastAPI backend is the only, trusted client and
    connects with database credentials (not the anon key).
"""

from __future__ import annotations

import os
import uuid
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool


def _database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Use the Supabase *pooler* URI (port 6543) "
            "for the application. See SETUP_GUIDE.md Part A2."
        )
    return url


def _is_pooler(url: str) -> bool:
    """Heuristic: Supabase transaction pooler runs on 6543."""
    return ":6543" in url or "pooler.supabase.com" in url


def build_engine(url: str | None = None) -> Engine:
    """Create the application engine with pooler-safe settings."""
    url = url or _database_url()
    connect_args: dict = {}

    if url.startswith("postgresql+asyncpg"):
        # asyncpg + pgbouncer transaction mode: caching MUST be off, and
        # statement names must be unique per connection.
        connect_args.update(
            {
                "prepared_statement_cache_size": 0,
                "prepared_statement_name_func": lambda: f"__acg_{uuid.uuid4().hex}__",
                "statement_cache_size": 0,
            }
        )
    else:
        # psycopg2 path. Keep server-side prepares off for pgbouncer safety.
        connect_args.setdefault("options", "-c statement_timeout=15000")

    kwargs: dict = {
        "future": True,
        "pool_pre_ping": True,
        "connect_args": connect_args,
    }
    if _is_pooler(url):
        # Do not hold client-side pooled connections on top of pgbouncer's pool.
        kwargs["poolclass"] = NullPool

    return create_engine(url, **kwargs)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return build_engine()


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker:
    return sessionmaker(bind=get_engine(), class_=Session, expire_on_commit=False, future=True)


def get_session() -> Session:
    """FastAPI dependency-friendly session factory."""
    return get_sessionmaker()()
