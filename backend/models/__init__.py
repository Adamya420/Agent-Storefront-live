"""SQLAlchemy models — every table in TRD §4.

Conventions (AGENTS.md §4):
  * All money columns are integer paise. No floats anywhere.
  * All timestamps are timezone-aware UTC.
  * JSON columns are JSONB.
  * audit_log is append-only and hash-chained; never UPDATE or DELETE a row.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------- enums


class AuthorizationStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"


class AgentType(str, enum.Enum):
    GEMINI = "gemini"
    BASELINE_PASSIVE = "baseline_passive"
    DETERMINISTIC = "deterministic"


class SessionStatus(str, enum.Enum):
    OPEN = "OPEN"
    CONVERTED = "CONVERTED"
    ABANDONED = "ABANDONED"
    DENIED = "DENIED"


class GateDecision(str, enum.Enum):
    PASS = "PASS"
    DENY = "DENY"
    NA = "NA"


class LeverType(str, enum.Enum):
    NONE = "NONE"
    RETURN_EXTENSION = "RETURN_EXTENSION"
    SHIPPING_UPGRADE = "SHIPPING_UPGRADE"
    BUNDLE = "BUNDLE"
    DISCOUNT = "DISCOUNT"
    MULTI = "MULTI"


class PaymentStatus(str, enum.Enum):
    CREATED = "CREATED"
    CAPTURED = "CAPTURED"
    FAILED = "FAILED"


# ---------------------------------------------------------------- merchant


class Merchant(Base):
    __tablename__ = "merchant"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    active_config_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    configs: Mapped[list["MerchantConfig"]] = relationship(back_populates="merchant")


class MerchantConfig(Base):
    """Versioned merchant policy. A change creates a NEW row; never mutate."""

    __tablename__ = "merchant_config"
    __table_args__ = (UniqueConstraint("merchant_id", "version", name="uq_merchant_config_version"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_uuid)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchant.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    margin_floor_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=1500)
    discount_budget_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=800)
    return_band_min_days: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    return_band_max_days: Mapped[int] = mapped_column(Integer, nullable=False, default=18)
    shipping_upgrade_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    shipping_upgrade_max_cost_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=15000)
    allowed_categories: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    bundle_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    bundle_max_addon_categories: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    velocity_max_offers_per_buyer_per_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    merchant: Mapped[Merchant] = relationship(back_populates="configs")


# ---------------------------------------------------------------- catalog


class Product(Base):
    __tablename__ = "product"

    sku: Mapped[str] = mapped_column(String(64), primary_key=True)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchant.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    list_price_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cost_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    return_days: Mapped[int] = mapped_column(Integer, nullable=False)
    shipping_days: Mapped[int] = mapped_column(Integer, nullable=False)

    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    media: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# ---------------------------------------------------------------- mandates


class IntentMandate(Base):
    __tablename__ = "intent_mandate"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_uuid)
    raw_jws: Mapped[str] = mapped_column(Text, nullable=False)
    buyer_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(200), nullable=False)

    constraints: Mapped[dict] = mapped_column(JSONB, nullable=False)
    tolerances: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    allowed_merchants: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    expiry: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verify_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mandate_source: Mapped[str] = mapped_column(String(32), nullable=False, default="ap2_full")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Authorization(Base):
    """The bound ceiling created after a mandate verifies. Nothing may exceed it."""

    __tablename__ = "authorization"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_uuid)
    intent_mandate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("intent_mandate.id"), nullable=False)
    ceiling: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[AuthorizationStatus] = mapped_column(
        SAEnum(AuthorizationStatus, name="authorization_status"),
        nullable=False,
        default=AuthorizationStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# ---------------------------------------------------------------- sessions & events


class Session(Base):
    __tablename__ = "session"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_uuid)
    intent_mandate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("intent_mandate.id"), nullable=False)
    # Scopes a session to the merchant it was created against, so console dashboards
    # filter to "this merchant" instead of aggregating every merchant globally
    # (deep-review). Nullable for backward-compat with pre-migration rows.
    merchant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("merchant.id"), nullable=True, index=True)
    agent_type: Mapped[AgentType] = mapped_column(SAEnum(AgentType, name="agent_type"), nullable=False)
    status: Mapped[SessionStatus] = mapped_column(
        SAEnum(SessionStatus, name="session_status"), nullable=False, default=SessionStatus.OPEN
    )
    outcome_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Event(Base):
    """Exactly one row per tool call and per gate decision (FR-5)."""

    __tablename__ = "event"
    __table_args__ = (UniqueConstraint("session_id", "seq", name="uq_event_session_seq"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("session.id"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)

    tool_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_input: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    tool_output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    gate_decision: Mapped[GateDecision] = mapped_column(
        SAEnum(GateDecision, name="gate_decision"), nullable=False, default=GateDecision.NA
    )
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# ---------------------------------------------------------------- offers & carts


class Offer(Base):
    __tablename__ = "offer"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("session.id"), nullable=False, index=True)
    base_sku: Mapped[str] = mapped_column(String(64), nullable=False)
    lever_type: Mapped[LeverType] = mapped_column(SAEnum(LeverType, name="lever_type"), nullable=False)
    transformation: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    computed_cost_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    resulting_margin_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    within_bounds: Mapped[bool] = mapped_column(Boolean, nullable=False)
    chosen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class CartMandate(Base):
    __tablename__ = "cart_mandate"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("session.id"), nullable=False, index=True)
    offer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("offer.id"), nullable=False)
    raw_jws: Mapped[str] = mapped_column(Text, nullable=False)

    items: Mapped[list] = mapped_column(JSONB, nullable=False)
    total_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tax_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    shipping_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    return_terms_days: Mapped[int] = mapped_column(Integer, nullable=False)

    cart_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    merchant_sig: Mapped[str] = mapped_column(Text, nullable=False)
    buyer_sig: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class DelegatedToken(Base):
    __tablename__ = "delegated_token"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("session.id"), nullable=False, index=True)
    raw_jws: Mapped[str] = mapped_column(Text, nullable=False)
    max_amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expiry: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(200), nullable=False)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    consumed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


# ---------------------------------------------------------------- payment & receipt


class Payment(Base):
    __tablename__ = "payment"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_uuid)
    cart_mandate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cart_mandate.id"), nullable=False)
    razorpay_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus, name="payment_status"), nullable=False, default=PaymentStatus.CREATED
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Receipt(Base):
    __tablename__ = "receipt"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("session.id"), nullable=False, index=True)
    chain: Mapped[dict] = mapped_column(JSONB, nullable=False)
    chain_head_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# ---------------------------------------------------------------- audit


class AuditLog(Base):
    """Append-only, hash-chained. NEVER updated or deleted (TRD §11)."""

    __tablename__ = "audit_log"
    __table_args__ = (UniqueConstraint("seq", name="uq_audit_seq"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_uuid)
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("session.id"), nullable=True, index=True)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    hash: Mapped[str] = mapped_column(String(128), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


__all__ = [
    "Base",
    "Merchant",
    "MerchantConfig",
    "Product",
    "IntentMandate",
    "Authorization",
    "Session",
    "Event",
    "Offer",
    "CartMandate",
    "DelegatedToken",
    "Payment",
    "Receipt",
    "AuditLog",
    "AuthorizationStatus",
    "AgentType",
    "SessionStatus",
    "GateDecision",
    "LeverType",
    "PaymentStatus",
    "utcnow",
]
