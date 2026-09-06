"""Alembic environment for ACG.

IMPORTANT (TRD §2): migrations run against **DIRECT_URL** (Supabase direct
connection, port 5432), NOT the transaction pooler (6543). pgbouncer in
transaction mode does not support the prepared statements and session-level
behaviour DDL relies on, and running migrations through it produces confusing
intermittent failures. The application, by contrast, uses DATABASE_URL (pooler).
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from backend.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _migration_url() -> str:
    """Prefer DIRECT_URL; fall back to DATABASE_URL with a loud warning."""
    direct = os.getenv("DIRECT_URL")
    if direct:
        return direct
    fallback = os.getenv("DATABASE_URL")
    if not fallback:
        raise RuntimeError(
            "Neither DIRECT_URL nor DATABASE_URL is set. Migrations need the Supabase "
            "DIRECT connection (port 5432). See SETUP_GUIDE.md Part A2."
        )
    print(
        "WARNING: DIRECT_URL not set; falling back to DATABASE_URL. If that is the "
        "pooler (:6543), migrations may fail intermittently.",
        file=sys.stderr,
    )
    return fallback


def run_migrations_offline() -> None:
    context.configure(
        url=_migration_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _migration_url()

    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
