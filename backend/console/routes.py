"""Console API (backend/console/routes.py) — the read + management surface the
Next.js console binds to. Grouped by the three lenses:

  MERCHANT : /console/overview, /console/catalog(+onboard), /console/catalog/{sku}/envelope,
             /console/policy, /console/audit-trail/{session_id}
  RAIL     : /console/sessions, /console/sessions/{id}, /console/gate-log, /console/receipts
  BUYER    : /console/buyer/turn   (drives the Gemini stand-in; integration-only)

Read endpoints are thin projections over existing models. The four capability
endpoints reuse the PURE console modules (envelope/audit_view/metrics) and the pure
offer engine — no LLM anywhere except the clearly-labelled Buyer turn.

rule #8: the RAIL/MERCHANT projections never include the buyer agent's private
reasoning; that is only returned by /console/buyer/turn to the Buyer lens.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
import sqlalchemy as sa
from sqlalchemy import select

from backend.audit.chain import AuditRow
from backend.console.audit_view import reconstruct_trail
from backend.console.envelope import compute_offer_envelope
from backend.console.metrics import DenyRow, SessionRow, aggregate_overview
from backend.db.session import get_session
from backend.offer.engine import EngineProduct, parse_band

router = APIRouter(prefix="/console", tags=["console"])


# ---------------------------------------------------------------- helpers


def _db():
    return get_session()


import uuid as _uuid


def _uuid_or_404(session_id: str):
    """Reject a non-UUID path param with a clean 404 instead of a raw 500 when it
    hits a UUID-typed column (deep-review)."""
    try:
        return _uuid.UUID(str(session_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(404, "session not found")


def _lever(offer) -> str:
    return offer.lever_type.value if offer and offer.lever_type else ""


def _session_rows(db, merchant_id=None):
    from backend.models import Offer, Payment, Session, CartMandate

    # Scope to this merchant's sessions. Pre-migration rows have NULL merchant_id
    # and are treated as legacy/visible so a live demo DB doesn't go blank; on a
    # fresh DB every session is scoped, so cross-merchant contamination is gone.
    q = select(Session)
    if merchant_id is not None:
        q = q.where(sa.or_(Session.merchant_id == merchant_id, Session.merchant_id.is_(None)))

    sessions = db.execute(q).scalars().all()
    if not sessions:
        return []
    ids = [s.id for s in sessions]

    # Batched instead of 1-4 queries PER session (was an N+1 that made
    # /overview and /analytics take 5-6s on a few hundred sessions — every
    # page that reads them, e.g. merchant/home, felt "very slow" to switch
    # into). One query per related table, joined in Python via dict lookups.
    # setdefault, not a dict comprehension: a handful of sessions have MORE
    # THAN ONE Offer row with chosen=True — /acp/checkout_sessions/.../complete
    # used to fabricate a second NONE-lever "placeholder" offer even when the
    # engine's real offer already existed (fixed in acp/routes.py), so some
    # already-written rows still carry the duplicate. Order oldest-first and
    # keep the first one seen per session: the engine's real offer is always
    # created before that placeholder, so this deterministically prefers the
    # real lever over the stale NONE duplicate for those legacy rows.
    offer_by_session: dict = {}
    for o in db.execute(select(Offer).where(Offer.session_id.in_(ids), Offer.chosen == True)  # noqa: E712
                        .order_by(Offer.created_at.asc())).scalars().all():
        offer_by_session.setdefault(o.session_id, o)

    converted_ids = [s.id for s in sessions if s.status.value == "CONVERTED"]
    cart_by_session: dict = {}
    payment_by_cart: dict = {}
    if converted_ids:
        # ordered desc so the first cart seen per session_id is the newest one
        carts = db.execute(select(CartMandate).where(CartMandate.session_id.in_(converted_ids))
                           .order_by(CartMandate.created_at.desc())).scalars().all()
        for c in carts:
            cart_by_session.setdefault(c.session_id, c)
        cart_ids = [c.id for c in cart_by_session.values()]
        if cart_ids:
            payments = db.execute(select(Payment).where(Payment.cart_mandate_id.in_(cart_ids))).scalars().all()
            for p in payments:
                payment_by_cart.setdefault(p.cart_mandate_id, p)

    rows = []
    for s in sessions:
        chosen = offer_by_session.get(s.id)
        total = 0
        if s.status.value == "CONVERTED":
            cart = cart_by_session.get(s.id)
            pay = payment_by_cart.get(cart.id) if cart else None
            total = pay.amount_paise if pay else (chosen.transformation.get("total_paise", 0) if chosen else 0)
        rows.append((s, chosen, total))
    return rows


def _active_merchant_id(db):
    from backend.acp.offer_wire import active_merchant
    m = active_merchant(db)
    return m.id if m else None


# ---------------------------------------------------------------- MERCHANT: overview


@router.get("/auth/check")
def auth_check():
    """Cheap gated endpoint the console unlock screen probes to validate the key.
    Returns 200 only when the X-Console-Key header matches (middleware-enforced)."""
    return {"ok": True}


@router.get("/overview")
def overview():
    db = _db()
    try:
        from backend.models import AuditLog
        srows = [SessionRow(session_id=str(s.id), status=s.status.value, lever=_lever(o),
                            total_paise=t, outcome_reason=s.outcome_reason or "")
                 for (s, o, t) in _session_rows(db, _active_merchant_id(db))]
        # Gate decisions are audited via AuditLog (action="GATE_DENY"/"GATE_PASS"),
        # written at every gate call site (backend/acp/orchestration.py). The
        # `event` table these used to read is never populated by real traffic --
        # see ERRORS.md 2026-09-02 T3-FE2 -- so denies here undercounted (only
        # stray rows from a past integration test run showed up).
        deny_rows = db.execute(
            select(AuditLog).where(AuditLog.action == "GATE_DENY")).scalars().all()
        denies = [DenyRow(reason_code=e.detail.get("reason_code") or "UNKNOWN",
                          check_number=e.detail.get("check_index") or 0)
                  for e in deny_rows]
        m = aggregate_overview(srows, denies)
        return m.__dict__
    finally:
        db.close()


# ---------------------------------------------------------------- RAIL: sessions + gate log


@router.post("/sessions/reconcile")
def reconcile_sessions():
    """Poll every pending-payment session so links paid after the fact are reflected.

    Reuses the tested /acp/.../poll path (which converges with the webhook on one
    capture). The console calls this on the Transactions view so completing a link
    in the browser shows up as CONVERTED without a manual re-poll.
    """
    db = _db()
    try:
        from backend.models import Session, SessionStatus
        pend = db.execute(select(Session).where(
            Session.status == SessionStatus.OPEN,
            Session.outcome_reason == "PENDING_PAYMENT")).scalars().all()
        ids = [str(s.id) for s in pend]
    finally:
        db.close()
    if not ids:
        return {"reconciled": 0, "converted": 0}
    from fastapi.testclient import TestClient
    from backend.api.main import app
    client = TestClient(app)
    converted = 0
    for sid in ids:
        try:
            r = client.post(f"/acp/checkout_sessions/{sid}/poll").json()
            if r.get("status") in ("CONVERTED", "CAPTURED"):
                converted += 1
        except Exception:  # noqa: BLE001 — best-effort; a slow one shouldn't block the rest
            pass
    return {"reconciled": len(ids), "converted": converted}


@router.get("/analytics")
def analytics():
    """Merchant insights: funnel, lever effectiveness, margin distribution, and
    recovered-revenue over time — all from real session data."""
    db = _db()
    try:
        from backend.console.metrics import (SessionRow, funnel, lever_effectiveness, margin_histogram)
        from backend.models import Offer
        rows = _session_rows(db, _active_merchant_id(db))
        srows = [SessionRow(session_id=str(s.id), status=s.status.value, lever=_lever(o),
                            total_paise=t, outcome_reason=s.outcome_reason or "",
                            margin_bps=(o.resulting_margin_bps if o else 0))
                 for (s, o, t) in rows]
        offered = sum(1 for (_s, o, _t) in rows if o is not None)
        # recovered revenue over time (cumulative), converted sessions by start time
        conv = sorted([(s.started_at, t, _lever(o)) for (s, o, t) in rows
                       if s.status.value == "CONVERTED" and s.started_at],
                      key=lambda x: x[0])
        series, cum = [], 0
        for (ts, tot, lev) in conv:
            if lev not in ("NONE", ""):
                cum += tot
            series.append({"ts": ts.isoformat(), "cumulative_recovered_paise": cum,
                           "amount_paise": tot, "recovered": lev not in ("NONE", "")})
        return {
            "funnel": funnel(srows, offered),
            "lever_effectiveness": lever_effectiveness(srows),
            "margin_histogram": margin_histogram(srows),
            "recovered_over_time": series,
        }
    finally:
        db.close()


@router.get("/sessions")
def sessions(status: str | None = None):
    db = _db()
    try:
        out = []
        for (s, o, t) in _session_rows(db, _active_merchant_id(db)):
            if status and s.status.value != status:
                continue
            out.append({
                "session_id": str(s.id), "status": s.status.value, "agent_type": s.agent_type.value,
                "lever": _lever(o), "total_paise": t, "outcome_reason": s.outcome_reason,
                "margin_bps": (o.resulting_margin_bps if o else None),
                "started_at": s.started_at.isoformat() if s.started_at else None,
            })
        out.sort(key=lambda r: r["started_at"] or "", reverse=True)
        return {"sessions": out, "count": len(out)}
    finally:
        db.close()


@router.get("/sessions/{session_id}")
def session_detail(session_id: str):
    _uuid_or_404(session_id)
    db = _db()
    try:
        from backend.models import AuditLog, Offer, Receipt, Session
        s = db.get(Session, session_id)
        if s is None:
            raise HTTPException(404, "session not found")
        offer = db.execute(select(Offer).where(Offer.session_id == s.id, Offer.chosen == True)  # noqa: E712
                          ).scalars().first()
        # Gate ledger comes from AuditLog (action="GATE_PASS"/"GATE_DENY"), the
        # table every gate call site actually writes to -- not the unpopulated
        # `event` table. See ERRORS.md 2026-09-02 T3-FE2.
        gate_rows = db.execute(
            select(AuditLog).where(AuditLog.session_id == s.id,
                                   AuditLog.action.in_(("GATE_PASS", "GATE_DENY")))
            .order_by(AuditLog.seq)).scalars().all()
        receipt = db.execute(select(Receipt).where(Receipt.session_id == s.id)).scalars().first()
        return {
            "session_id": str(s.id), "status": s.status.value, "agent_type": s.agent_type.value,
            "outcome_reason": s.outcome_reason,
            "offer": None if not offer else {
                "base_sku": offer.base_sku, "lever": offer.lever_type.value,
                "total_paise": offer.transformation.get("total_paise"),
                "margin_bps": offer.resulting_margin_bps, "within_bounds": offer.within_bounds,
                "added_cost_paise": offer.transformation.get("added_cost_paise", 0),
                "transformation": offer.transformation},
            "gate_events": [{"seq": e.seq, "type": e.action,
                             "decision": "DENY" if e.action == "GATE_DENY" else "PASS",
                             "reason_code": e.detail.get("reason_code"),
                             # 0/None on PASS (all 11 ran clean); 1-11 on DENY = which
                             # check stopped it (gate.py short-circuits, TRD §7 order).
                             # Lets the console render the full 11-check breakdown
                             # instead of just the one aggregate pass/deny line.
                             "check_index": e.detail.get("check_index")} for e in gate_rows],
            "receipt": None if not receipt else {
                "chain": receipt.chain, "chain_head_hash": receipt.chain_head_hash},
        }
    finally:
        db.close()


@router.get("/gate-log")
def gate_log():
    """The enforcement view (our 'Radar'): every deny with its reason + check number."""
    db = _db()
    try:
        # See ERRORS.md 2026-09-02 T3-FE2: denies live in AuditLog (action=
        # "GATE_DENY"), not the unpopulated `event` table.
        from backend.models import AuditLog
        events = db.execute(
            select(AuditLog).where(AuditLog.action == "GATE_DENY")
            .order_by(AuditLog.ts.desc())).scalars().all()
        rows = [{"session_id": str(e.session_id), "type": e.action,
                 "reason_code": e.detail.get("reason_code"), "ts": e.ts.isoformat() if e.ts else None}
                for e in events]
        by_reason: dict = {}
        for r in rows:
            by_reason[r["reason_code"]] = by_reason.get(r["reason_code"], 0) + 1
        return {"denies": rows, "count": len(rows), "by_reason": by_reason}
    finally:
        db.close()


# ---------------------------------------------------------------- MERCHANT: audit trail


@router.get("/audit-trail/{session_id}")
def audit_trail(session_id: str):
    """Reconstruct + verify the tamper-evident trail for a transaction (dispute view).
    Merchant-side record only; never the buyer agent's private reasoning (rule #8)."""
    _uuid_or_404(session_id)
    db = _db()
    try:
        from backend.models import AuditLog, Receipt, Session
        # A well-formed but nonexistent session must 404 — not report a globally
        # "verified" empty trail (deep-review).
        if db.get(Session, session_id) is None:
            raise HTTPException(404, "session not found")
        logs = db.execute(select(AuditLog).order_by(AuditLog.seq)).scalars().all()
        rows = [AuditRow(seq=l.seq, actor=l.actor, action=l.action, detail=l.detail,
                         prev_hash=l.prev_hash, hash=l.hash) for l in logs]
        sids = [l.session_id for l in logs]
        rec = db.execute(select(Receipt).where(Receipt.session_id == session_id)).scalars().first()
        receipt = {"links": rec.chain, "chain_head_hash": rec.chain_head_hash} if rec else None
        view = reconstruct_trail(rows, session_id, sids, receipt=receipt)
        return {
            "session_id": view.session_id, "chain_verified": view.chain_verified,
            "first_bad_seq": view.first_bad_seq, "ledger_head_hash": view.ledger_head_hash,
            "receipt_present": view.receipt_present, "receipt_verified": view.receipt_verified,
            "receipt_head_hash": view.receipt_head_hash,
            "steps": [{"seq": s.seq, "action": s.action, "actor": s.actor,
                       "detail": s.detail, "hash": s.hash, "prev_hash": s.prev_hash}
                      for s in view.steps],
        }
    finally:
        db.close()


# ---------------------------------------------------------------- MERCHANT: catalog + onboard


class NewProduct(BaseModel):
    sku: str
    title: str
    category: str
    list_price_paise: int = Field(gt=0)          # a ₹0 price would divide-by-zero the margin
    cost_paise: int = Field(ge=0)
    stock: int = Field(default=10, ge=0)
    return_days: int = Field(ge=0)
    shipping_days: int = Field(ge=0)
    effective_price_paise: int | None = Field(default=None, ge=0)
    promo_code: str | None = None
    description: str = ""


@router.get("/catalog")
def catalog():
    db = _db()
    try:
        from backend.acp.offer_wire import active_merchant
        from backend.models import Product
        m = active_merchant(db)
        q = select(Product)
        if m:
            q = q.where(Product.merchant_id == m.id)
        prods = db.execute(q).scalars().all()
        return {"products": [{
            "sku": p.sku, "title": p.title, "category": p.category,
            "list_price_paise": p.list_price_paise, "cost_paise": p.cost_paise, "stock": p.stock,
            "return_days": p.return_days, "shipping_days": p.shipping_days,
            "effective_price_paise": (p.attributes or {}).get("effective_price_paise"),
            "promo_code": (p.attributes or {}).get("promo_code"),
        } for p in prods], "count": len(prods)}
    finally:
        db.close()


@router.post("/catalog")
def onboard_product(body: NewProduct):
    db = _db()
    try:
        from backend.acp.offer_wire import active_merchant
        from backend.models import Product
        m = active_merchant(db)
        if m is None:
            raise HTTPException(400, "no active merchant; seed a merchant first")
        if db.get(Product, body.sku):
            raise HTTPException(409, f"sku {body.sku} already exists")
        attrs = {}
        if body.effective_price_paise is not None:
            attrs["effective_price_paise"] = body.effective_price_paise
        if body.promo_code:
            attrs["promo_code"] = body.promo_code
        db.add(Product(sku=body.sku, merchant_id=m.id, title=body.title, category=body.category,
                       list_price_paise=body.list_price_paise, cost_paise=body.cost_paise,
                       stock=body.stock, stock_version=1, return_days=body.return_days,
                       shipping_days=body.shipping_days, attributes=attrs, media=[],
                       description=body.description))
        db.commit()
        return {"ok": True, "sku": body.sku}
    finally:
        db.close()


class CsvImport(BaseModel):
    csv: str


@router.get("/catalog/import/template")
def catalog_import_template():
    """The CSV header + example rows, so the merchant knows the exact format."""
    from backend.console.csv_import import TEMPLATE
    return {"filename": "agent-storefront-catalog-template.csv", "content": TEMPLATE}


@router.post("/catalog/import")
def import_catalog(body: CsvImport):
    """Bulk add/update SKUs from CSV. Prices are in rupees. Upserts by SKU (safe to
    re-import) and reports per-row errors without aborting the whole import."""
    from backend.acp.offer_wire import active_merchant
    from backend.console.csv_import import parse_catalog_csv
    from backend.models import Product

    rows, errors = parse_catalog_csv(body.csv)
    db = _db()
    created = updated = 0
    try:
        m = active_merchant(db)
        if m is None:
            raise HTTPException(400, "no active merchant; seed a merchant first")
        # de-dupe within the file by SKU (last row wins), but SURFACE the collision so
        # a copy-paste duplicate isn't silently dropped with no trace (E-05).
        by_sku: dict = {}
        for r in rows:
            if r["sku"] in by_sku:
                errors.append({"line": 0, "sku": r["sku"],
                               "error": f"duplicate SKU in file — an earlier row for {r['sku']} was overwritten"})
            by_sku[r["sku"]] = r
        for sku, r in by_sku.items():
            fields = {k: v for k, v in r.items() if k not in ("attributes", "description", "sku")}
            existing = db.get(Product, sku)
            if existing is None:
                db.add(Product(sku=sku, merchant_id=m.id, media=[], stock_version=1,
                               attributes=r["attributes"], description=r["description"], **fields))
                created += 1
            else:
                for k, v in fields.items():
                    setattr(existing, k, v)
                existing.merchant_id = m.id
                existing.attributes = r["attributes"]
                existing.description = r["description"]
                updated += 1
        db.commit()
        return {"created": created, "updated": updated, "errors": errors,
                "rows_ok": len(by_sku), "rows_failed": len(errors)}
    finally:
        db.close()


@router.get("/catalog/{sku}/envelope")
def offer_envelope(sku: str):
    """The bounded offer envelope the engine can legally make on this SKU."""
    db = _db()
    try:
        from backend.acp.offer_wire import _config_dict, active_config, active_merchant
        from backend.models import Product
        p = db.get(Product, sku)
        if p is None:
            raise HTTPException(404, "sku not found")
        cfg = active_config(db)
        if cfg is None:
            raise HTTPException(400, "no merchant config")
        band = parse_band(_config_dict(cfg))
        m = active_merchant(db)
        catalog_products = db.execute(
            select(Product).where(Product.merchant_id == m.id)).scalars().all() if m else [p]

        def _ep(pr: Product) -> EngineProduct:
            a = pr.attributes or {}
            return EngineProduct(pr.sku, pr.category, pr.list_price_paise, pr.cost_paise, pr.stock,
                                 pr.stock_version, pr.return_days, pr.shipping_days, title=pr.title,
                                 effective_price_paise=int(a.get("effective_price_paise", 0) or 0),
                                 promo_code=str(a.get("promo_code", "") or ""))

        base = _ep(p)
        env = compute_offer_envelope(base, band, [_ep(x) for x in catalog_products])
        return {"sku": sku, "envelope": [e.__dict__ for e in env]}
    finally:
        db.close()


# ---------------------------------------------------------------- MERCHANT: policy / band


class BandPreview(BaseModel):
    margin_floor_bps: int = Field(ge=0, le=10000)
    discount_budget_bps: int = Field(ge=0, le=10000)
    return_band_max_days: int = Field(ge=0, le=365)
    shipping_upgrade_max_cost_paise: int = Field(ge=0, le=10_000_000)
    bundle_enabled: bool = True
    sku: str | None = None


@router.post("/policy/preview")
def policy_preview(body: BandPreview):
    """Preview the offer envelope for a DRAFT band without saving — powers the live
    slider feedback in Settings."""
    db = _db()
    try:
        from backend.acp.offer_wire import _config_dict, active_config, active_merchant
        from backend.console.envelope import compute_offer_envelope
        from backend.offer.engine import EngineProduct, parse_band
        from backend.models import Product
        cfg = active_config(db)
        base_cfg = _config_dict(cfg) if cfg else {
            "margin_floor_bps": 1500, "discount_budget_bps": 800, "return_band_min_days": 14,
            "return_band_max_days": 18, "shipping_upgrade_allowed": True,
            "shipping_upgrade_max_cost_paise": 15000, "bundle_enabled": True,
            "bundle_max_addon_categories": ["socks"], "allow_promo_stacking": False}
        base_cfg.update({"margin_floor_bps": body.margin_floor_bps,
                         "discount_budget_bps": body.discount_budget_bps,
                         "return_band_max_days": body.return_band_max_days,
                         "shipping_upgrade_max_cost_paise": body.shipping_upgrade_max_cost_paise,
                         "bundle_enabled": body.bundle_enabled})
        band = parse_band(base_cfg)
        m = active_merchant(db)
        prods = db.execute(select(Product).where(Product.merchant_id == m.id)).scalars().all() if m else []
        if not prods:
            return {"sku": None, "envelope": []}
        pick = next((p for p in prods if p.sku == body.sku), prods[0])

        def _ep(pr):
            a = pr.attributes or {}
            return EngineProduct(pr.sku, pr.category, pr.list_price_paise, pr.cost_paise, pr.stock,
                                 pr.stock_version, pr.return_days, pr.shipping_days, title=pr.title,
                                 effective_price_paise=int(a.get("effective_price_paise", 0) or 0),
                                 promo_code=str(a.get("promo_code", "") or ""))
        env = compute_offer_envelope(_ep(pick), band, [_ep(x) for x in prods])
        return {"sku": pick.sku, "envelope": [e.__dict__ for e in env]}
    finally:
        db.close()


@router.get("/policy")
def get_policy():
    db = _db()
    try:
        from backend.acp.offer_wire import _config_dict, active_config
        cfg = active_config(db)
        if cfg is None:
            raise HTTPException(404, "no config")
        return _config_dict(cfg)
    finally:
        db.close()


class PolicyPatch(BaseModel):
    # Bounds enforced at the API boundary so a negative/absurd value can't be
    # persisted into the config that gates every real checkout (deep-review): a
    # negative margin floor would silently disable margin protection; a negative
    # discount budget 500s the engine mid-checkout via apply_bps.
    margin_floor_bps: int | None = Field(default=None, ge=0, le=10000)
    discount_budget_bps: int | None = Field(default=None, ge=0, le=10000)
    return_band_max_days: int | None = Field(default=None, ge=0, le=365)
    shipping_upgrade_max_cost_paise: int | None = Field(default=None, ge=0, le=10_000_000)
    bundle_enabled: bool | None = None


@router.put("/policy")
def put_policy(patch: PolicyPatch):
    """Bump a new config version (configs are versioned; never mutate in place)."""
    db = _db()
    try:
        from backend.acp.offer_wire import active_config, active_merchant
        from backend.models import MerchantConfig
        cur = active_config(db)
        m = active_merchant(db)
        if cur is None or m is None:
            raise HTTPException(400, "no active merchant/config")
        fields = dict(
            margin_floor_bps=cur.margin_floor_bps, discount_budget_bps=cur.discount_budget_bps,
            return_band_min_days=cur.return_band_min_days, return_band_max_days=cur.return_band_max_days,
            shipping_upgrade_allowed=cur.shipping_upgrade_allowed,
            shipping_upgrade_max_cost_paise=cur.shipping_upgrade_max_cost_paise,
            allowed_categories=cur.allowed_categories, bundle_enabled=cur.bundle_enabled,
            bundle_max_addon_categories=cur.bundle_max_addon_categories,
            velocity_max_offers_per_buyer_per_hour=cur.velocity_max_offers_per_buyer_per_hour)
        for k, v in patch.model_dump(exclude_none=True).items():
            fields[k] = v
        db.add(MerchantConfig(merchant_id=m.id, version=cur.version + 1, **fields))
        m.active_config_version = cur.version + 1
        db.commit()
        return {"ok": True, "version": cur.version + 1}
    finally:
        db.close()


# ---------------------------------------------------------------- BUYER: chat turn


# ---------------------------------------------------------------- BUYER lens (case 3)
# extract (LLM under strict template, deterministic fallback) -> client shows the
# proposed constraints -> USER's tap calls /run which SIGNS the mandate and drives
# the real agent loop on the tested ACP rails -> /poll returns the settlement turn.


BUYER_ID = "did:acg:buyer-lens"
AGENT_ID = "agent://buyer-lens"
MERCHANT_URI = "merchant://acg-sports"


def _gemini_llm():
    """Return a callable(prompt)->text using Gemini, or None if unavailable."""
    try:
        from backend.agent.buyer import GeminiBuyerAgent
        g = GeminiBuyerAgent()

        def call(prompt: str) -> str:
            model = g._genai.GenerativeModel(g._model_name)
            return getattr(model.generate_content(prompt), "text", "") or ""

        return call
    except Exception:  # noqa: BLE001
        return None


class ExtractReq(BaseModel):
    text: str
    prior: dict | None = None   # standing draft intent, for conversational edits


@router.post("/buyer/chat/extract")
def buyer_extract(body: ExtractReq):
    """Propose (or EDIT) an intent from the user's words. NOTHING is signed here.
    When `prior` is supplied, only the fields the message mentions change."""
    from backend.console.buyer_session import confirm_prompt, extract_intent_llm
    intent, source = extract_intent_llm(body.text, llm=_gemini_llm(), prior=body.prior)
    return {"intent": intent, "narration": confirm_prompt(intent), "source": source,
            "confirm_required": True}


class RunReq(BaseModel):
    intent: dict

    @field_validator("intent")
    @classmethod
    def _validate_intent(cls, v: dict) -> dict:
        # This is the exact body the Buyer chat posts after "confirm". Validate it
        # here so a tampered/stale/malformed intent returns a clean 422 naming the
        # bad field instead of a raw KeyError/500 deep in build_intent_mandate.
        if not isinstance(v, dict):
            raise ValueError("intent must be an object")
        cat = v.get("category")
        if not isinstance(cat, str) or not cat.strip():
            raise ValueError("category is required and must be a non-empty string")
        price = v.get("max_price_paise")
        if not isinstance(price, int) or isinstance(price, bool) or price <= 0:
            raise ValueError("max_price_paise must be a positive integer (paise)")
        for k, lo in (("min_return_days", 0), ("max_delivery_days", 0), ("quantity", 1)):
            if k in v and v[k] is not None:
                x = v[k]
                if not isinstance(x, int) or isinstance(x, bool) or x < lo:
                    raise ValueError(f"{k} must be an integer >= {lo}")
        return v


@router.post("/buyer/chat/run")
def buyer_run(body: RunReq):
    """User approved the constraints. Sign the mandate, drive the agent loop, return
    ordered turns up to the payment link (or a terminal outcome)."""
    from backend.common.keys import load_private_key
    from backend.common.mandates import sign_mandate
    from backend.console.buyer_session import (
        compose_settlement_turn, compose_shop_turns)
    from backend.console.buyer_chat import extract_intent  # for validation defaults
    from data.issue_mandate import build_intent_mandate
    from scripts.sign_cart import main as sign_cart_main

    intent = body.intent
    # The buyer-chat flow signs a SINGLE-item cart, so it can't reproduce a
    # multi-item BUNDLE offer's cart hash (that would fail CART_INTEGRITY at the
    # gate). Bundles stay a real, visible lever on the merchant side; the chat
    # demo drives the single-item levers (as-is, return, shipping, discount).
    _tolerate = [t for t in intent.get("tolerate", ["return"]) if t != "bundle"] or ["return"]
    payload = build_intent_mandate(
        category=intent["category"], max_price_paise=intent["max_price_paise"],
        min_return_days=intent.get("min_return_days", 0),
        max_delivery_days=intent.get("max_delivery_days", 7), quantity=intent.get("quantity", 1),
        tolerate=_tolerate, allowed_merchants=[MERCHANT_URI],
        buyer_id=BUYER_ID, agent_id=AGENT_ID, ttl_minutes=15)
    intent_jws = sign_mandate(payload, load_private_key("user-test-1"), kid="user-test-1")

    from fastapi.testclient import TestClient
    from backend.api.main import app
    client = TestClient(app)
    created = client.post("/acp/checkout_sessions", json={"intent_mandate_jws": intent_jws}).json()
    session_id = created.get("session_id")
    offer = created.get("offer")

    # the pure decision rule (the injection hard-stop) drives the accept path
    from backend.agent.buyer import decide_on_offer
    from backend.offer.engine import parse_ceiling
    ceiling = parse_ceiling({"constraints": {
        "category": intent["category"], "max_price_paise": intent["max_price_paise"],
        "min_return_days": intent.get("min_return_days", 0),
        "max_delivery_days": intent.get("max_delivery_days", 7),
        "quantity": intent.get("quantity", 1)}, "tolerances": {}})

    if offer is None:
        decision = {"action": "ABANDON", "reason": created.get("no_offer_reason", "NO_OFFER")}
        turns = compose_shop_turns(intent, None, decision)
        return {"session_id": session_id, "turns": turns, "next": "done"}

    # reconstruct an engine Offer-shaped object for the pure rule
    from backend.offer.engine import Offer, OfferItem
    eng_offer = Offer(
        base_sku=offer["base_sku"], lever_type=offer.get("lever_type", "NONE"),
        items=(OfferItem(offer["base_sku"], intent.get("quantity", 1), offer["unit_price_paise"]),),
        total_paise=offer["total_paise"], return_terms_days=offer["return_days"],
        delivery_days=offer["shipping_days"], computed_cost_paise=0, resulting_margin_bps=0)
    dec = decide_on_offer(eng_offer, ceiling)
    decision = {"action": dec.action, "reason": dec.reason}
    turns = compose_shop_turns(intent, offer, decision)

    if dec.action != "ACCEPT":
        return {"session_id": session_id, "turns": turns, "next": "done"}

    # accept -> sign the cart server-side (stand-in holds test keys) and settle
    import tempfile, os
    d = tempfile.mkdtemp()
    cart_p, tok_p = os.path.join(d, "c.jws"), os.path.join(d, "t.jws")
    sign_cart_main(["--sku", offer["base_sku"], "--price", str(offer["unit_price_paise"]),
                    "--return-days", str(offer["return_days"]), "--session", session_id,
                    "--out-cart", cart_p, "--out-token", tok_p])
    init = client.post(f"/acp/checkout_sessions/{session_id}/complete", json={
        "cart_mandate_jws": open(cart_p).read(), "delegated_token_jws": open(tok_p).read()}).json()
    _sim = os.getenv("ACG_SETTLEMENT_MODE", "simulate").strip().lower() not in ("razorpay", "real", "live")
    turns.append(compose_settlement_turn(init, simulated=_sim))
    nxt = "poll" if init.get("status") == "PENDING_PAYMENT" else "done"
    return {"session_id": session_id, "turns": turns, "next": nxt,
            "payment_link": None if _sim else init.get("payment_link_url")}


class PollReq(BaseModel):
    session_id: str


@router.post("/buyer/chat/poll")
def buyer_poll(body: PollReq):
    """Poll settlement; return a receipt turn once the link is paid."""
    from fastapi.testclient import TestClient
    from backend.api.main import app
    from backend.console.buyer_session import compose_receipt_turn
    client = TestClient(app)
    poll = client.post(f"/acp/checkout_sessions/{body.session_id}/poll").json()
    turn = compose_receipt_turn(poll)
    return {"status": poll.get("status"), "turns": [turn] if turn else [],
            "next": "done" if turn else "poll"}
