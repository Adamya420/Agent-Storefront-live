"""AP2-shaped mandate schemas + JWS (ES256) signing/verification.

Scope note (PRD §4, TRD §3): these are **AP2-shaped**, signed with JWS/ES256
against a local key registry. They are NOT full W3C Verifiable Credentials with
DID resolution. We say so openly; the structure and verification semantics
(signed intent → bound ceiling → signed cart → receipt chain) are faithful, the
credential machinery is simplified.

This module is pure and dependency-light so it can be unit-tested with no DB and
no network (agent.md: Claude chat owns these tests).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from jose import jws
from jose.exceptions import JOSEError
from pydantic import BaseModel, Field, field_validator

ALGORITHM = "ES256"


# ---------------------------------------------------------------- schemas


class IntentConstraints(BaseModel):
    """Hard constraints. The agent may NOT exceed these — they are the ceiling."""

    category: str
    max_price_paise: int = Field(gt=0)
    min_return_days: int = Field(ge=0)
    max_delivery_days: int = Field(ge=0)
    quantity: int = Field(gt=0, default=1)

    @field_validator("max_price_paise", "min_return_days", "max_delivery_days", "quantity")
    @classmethod
    def _no_bools(cls, v: int) -> int:
        if isinstance(v, bool):
            raise ValueError("must be an int, not bool")
        return v


class IntentTolerances(BaseModel):
    """Which concession levers the BUYER will accept.

    The Offer Engine may only use a lever if the buyer tolerates it AND the
    merchant's band permits it (the 'authorized space' = intersection).
    substitution_ok is False in v1 (TRD §8) — substitution is the most
    consent-fraught lever and is out of scope.
    """

    return_ok: bool = False
    bundle_ok: bool = False
    discount_ok: bool = False
    shipping_upgrade_ok: bool = False
    substitution_ok: Literal[False] = False


class IntentMandatePayload(BaseModel):
    type: Literal["IntentMandate"] = "IntentMandate"
    buyer_id: str
    agent_id: str
    constraints: IntentConstraints
    tolerances: IntentTolerances = Field(default_factory=IntentTolerances)
    allowed_merchants: list[str] = Field(default_factory=list)
    expiry: datetime
    nonce: str

    @field_validator("expiry")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("expiry must be timezone-aware (UTC)")
        return v.astimezone(timezone.utc)

    def is_expired(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return now >= self.expiry


class DelegatedTokenPayload(BaseModel):
    """ACP-shaped delegated payment token: single-use, scoped by amount+expiry+merchant."""

    type: Literal["DelegatedToken"] = "DelegatedToken"
    max_amount_paise: int = Field(gt=0)
    merchant_id: str
    expiry: datetime
    nonce: str

    @field_validator("expiry")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("expiry must be timezone-aware (UTC)")
        return v.astimezone(timezone.utc)


# ---------------------------------------------------------------- serialization


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    raise TypeError(f"not JSON serializable: {type(obj).__name__}")


def canonical_json(payload: dict) -> str:
    """Deterministic JSON: sorted keys, no whitespace. Used for signing AND hashing.

    Determinism matters: a re-serialization that reorders keys would change the
    hash and break both signature verification and the audit chain.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_json_default)


# ---------------------------------------------------------------- sign / verify


class MandateSignatureError(Exception):
    """Raised when a mandate's signature cannot be verified."""


class MandateMalformedError(Exception):
    """Raised when a mandate is not well-formed or fails schema validation."""


def sign_mandate(payload: BaseModel | dict, private_key_pem: str, kid: str) -> str:
    """Sign a mandate payload, returning a compact JWS string."""
    data = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    return jws.sign(
        json.loads(canonical_json(data)),
        private_key_pem,
        algorithm=ALGORITHM,
        headers={"kid": kid, "typ": "JWS"},
    )


def peek_header(token: str) -> dict:
    """Read the JWS header WITHOUT verifying — used only to resolve which key to use."""
    try:
        return jws.get_unverified_header(token)
    except JOSEError as exc:
        raise MandateMalformedError(f"cannot read JWS header: {exc}") from exc


def verify_jws(token: str, public_key_pem: str) -> dict:
    """Verify the signature and return the payload dict.

    Raises MandateSignatureError on any signature/format failure. We deliberately
    do NOT fall back to unverified decoding anywhere in the codebase.
    """
    try:
        raw = jws.verify(token, public_key_pem, algorithms=[ALGORITHM])
    except JOSEError as exc:
        raise MandateSignatureError(f"signature verification failed: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MandateMalformedError(f"payload is not valid JSON: {exc}") from exc


def parse_intent_mandate(token: str, public_key_pem: str) -> IntentMandatePayload:
    """Verify signature THEN validate schema. Order matters: never trust unverified data."""
    payload = verify_jws(token, public_key_pem)
    try:
        return IntentMandatePayload.model_validate(payload)
    except Exception as exc:  # pydantic ValidationError
        raise MandateMalformedError(f"intent mandate failed schema validation: {exc}") from exc


def parse_delegated_token(token: str, public_key_pem: str) -> DelegatedTokenPayload:
    payload = verify_jws(token, public_key_pem)
    try:
        return DelegatedTokenPayload.model_validate(payload)
    except Exception as exc:
        raise MandateMalformedError(f"delegated token failed schema validation: {exc}") from exc


# ---------------------------------------------------------------- helpers


def new_nonce() -> str:
    return uuid.uuid4().hex


def expiry_in(minutes: int, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now + timedelta(minutes=minutes)
