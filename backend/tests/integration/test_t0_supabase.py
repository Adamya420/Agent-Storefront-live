"""T0 INTEGRATION checks — these hit Supabase. Claude chat CANNOT run these.

Run with real .env present (agent.md Role 2/3):
    pytest backend/tests/integration/test_t0_supabase.py -v -m integration

Each test states what it proves. If one fails, log the REAL error message to
ERRORS.md with root cause before fixing.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "merchant", "merchant_config", "product", "intent_mandate", "authorization",
    "session", "event", "offer", "cart_mandate", "delegated_token",
    "payment", "receipt", "audit_log",
}


@pytest.fixture(scope="module")
def engine():
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set — integration tests require a real Supabase .env")
    from backend.db.session import build_engine

    return build_engine()


def test_pooler_connection_works(engine):
    """Proves: the app can connect via the Supabase POOLER (6543).

    If this fails with DuplicatePreparedStatement / InvalidSQLStatementName, the
    prepared-statement cache was not disabled — see backend/db/session.py.
    """
    with engine.connect() as conn:
        assert conn.execute(text("select 1")).scalar_one() == 1


def test_repeated_queries_do_not_break_on_pgbouncer(engine):
    """Proves the pooler fix under REPEATED execution.

    A single query can pass while statement caching is still broken; the failure
    typically appears on re-execution of the same statement across connections.
    """
    with engine.connect() as conn:
        for _ in range(25):
            conn.execute(text("select :v"), {"v": 1}).scalar_one()


def test_all_trd_tables_exist(engine):
    """Proves: `alembic upgrade head` created every TRD §4 table."""
    found = set(inspect(engine).get_table_names())
    missing = EXPECTED_TABLES - found
    assert not missing, f"missing tables (did alembic upgrade head run?): {sorted(missing)}"


def test_alembic_is_at_head():
    """Proves migrations are applied, not just that tables happen to exist."""
    out = subprocess.run(
        ["alembic", "current"], cwd=ROOT, capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, f"alembic current failed: {out.stderr}"
    assert "0001_initial_schema" in out.stdout, f"not at head: {out.stdout!r}"


def test_catalog_persists_and_reads_back(engine):
    """Proves: generated catalog can be written to and read from Supabase.

    Writes into a transaction and ROLLS BACK so the check is repeatable and
    leaves no residue.
    """
    from sqlalchemy.orm import Session as OrmSession

    from backend.models import Merchant, Product
    from data.generate_catalog import SKU_INJECTION, generate_catalog

    products = generate_catalog(count=30, seed=42)

    with OrmSession(engine) as s:
        merchant = Merchant(name="ACG Sports (test)")
        s.add(merchant)
        s.flush()

        for p in products:
            s.add(
                Product(
                    sku=f"IT-{p.sku}",
                    merchant_id=merchant.id,
                    title=p.title,
                    category=p.category,
                    list_price_paise=p.list_price_paise,
                    cost_paise=p.cost_paise,
                    stock=p.stock,
                    stock_version=p.stock_version,
                    return_days=p.return_days,
                    shipping_days=p.shipping_days,
                    attributes=p.attributes,
                    media=p.media,
                    description=p.description,
                )
            )
        s.flush()

        count = s.query(Product).filter(Product.merchant_id == merchant.id).count()
        assert count == len(products)

        # JSONB round-trip must preserve structure (a real failure mode).
        injected = s.query(Product).filter(Product.sku == f"IT-{SKU_INJECTION}").one()
        assert isinstance(injected.attributes, dict)
        assert "rating_tenths" in injected.attributes
        assert isinstance(injected.media, list)

        # Money must come back as int, never float/Decimal drift.
        assert isinstance(injected.list_price_paise, int)

        s.rollback()


def test_audit_log_append_only_hash_chain_shape(engine):
    """Proves the audit table accepts a chained insert and enforces seq uniqueness."""
    import hashlib
    import uuid

    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session as OrmSession

    from backend.models import AuditLog

    with OrmSession(engine) as s:
        seq = int(uuid.uuid4().int % 1_000_000_000)
        h = hashlib.sha256(b"genesis").hexdigest()
        s.add(AuditLog(seq=seq, actor="test", action="T0_CHECK", detail={"k": "v"},
                       prev_hash="0" * 64, hash=h))
        s.flush()

        s.add(AuditLog(seq=seq, actor="test", action="DUP", detail={},
                       prev_hash=h, hash=hashlib.sha256(b"dup").hexdigest()))
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()
