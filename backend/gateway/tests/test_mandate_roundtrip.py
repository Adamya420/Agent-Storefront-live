"""Unit tests: issued JWS parses in the verifier (SKILL.md T0 checkpoint).

Pure — generates throwaway keys in a tmp dir, no DB, no network.
T0 covers schema/signature/expiry/scope. The nonce/replay check is stateful
(Upstash) and lands in T1 as an integration concern.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.common.mandates import (  # noqa: E402
    IntentConstraints,
    IntentMandatePayload,
    IntentTolerances,
    MandateMalformedError,
    MandateSignatureError,
    canonical_json,
    expiry_in,
    new_nonce,
    parse_intent_mandate,
    sign_mandate,
)
from backend.gateway.verify import VerifyReason, build_ceiling, verify_intent_mandate  # noqa: E402
from data.generate_keys import generate_keypair  # noqa: E402
from data.issue_mandate import build_intent_mandate  # noqa: E402

MERCHANT = "merchant://acg-sports"
CATEGORIES = ["running_shoes", "socks"]


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    """Two keypairs: the legitimate user key, and an attacker key for forgery tests."""
    d = tmp_path_factory.mktemp("keys")
    generate_keypair("user-test-1", d)
    generate_keypair("attacker-1", d)
    return {
        "dir": d,
        "user_priv": (d / "user-test-1.pem").read_text(),
        "user_pub": (d / "user-test-1.pub.pem").read_text(),
        "attacker_priv": (d / "attacker-1.pem").read_text(),
        "attacker_pub": (d / "attacker-1.pub.pem").read_text(),
    }


@pytest.fixture
def resolver(keys):
    def _resolve(kid):
        from backend.common.keys import KeyNotFoundError

        mapping = {"user-test-1": keys["user_pub"], "attacker-1": keys["attacker_pub"]}
        if kid not in mapping:
            raise KeyNotFoundError(f"unknown kid {kid!r}")
        return mapping[kid]

    return _resolve


def make_token(keys, **overrides):
    payload = build_intent_mandate(
        category=overrides.get("category", "running_shoes"),
        max_price_paise=overrides.get("max_price_paise", 500000),
        min_return_days=overrides.get("min_return_days", 21),
        max_delivery_days=overrides.get("max_delivery_days", 3),
        quantity=overrides.get("quantity", 1),
        tolerate=overrides.get("tolerate", ["return", "bundle"]),
        allowed_merchants=overrides.get("allowed_merchants", [MERCHANT]),
        buyer_id="did:acg:test-user-1",
        agent_id="agent://acg-buyer-standin",
        ttl_minutes=overrides.get("ttl_minutes", 15),
    )
    if "expiry" in overrides:
        payload = payload.model_copy(update={"expiry": overrides["expiry"]})
    kid = overrides.get("kid", "user-test-1")
    priv = keys["attacker_priv"] if kid == "attacker-1" else keys["user_priv"]
    return sign_mandate(payload, priv, kid=kid), payload


class TestIssuerBuilder:
    def test_tolerance_flags_map_correctly(self):
        p = build_intent_mandate(
            category="running_shoes", max_price_paise=500000, min_return_days=21,
            max_delivery_days=3, quantity=1, tolerate=["return", "discount"],
            allowed_merchants=[MERCHANT], buyer_id="b", agent_id="a", ttl_minutes=15,
        )
        assert p.tolerances.return_ok is True
        assert p.tolerances.discount_ok is True
        assert p.tolerances.bundle_ok is False
        assert p.tolerances.shipping_upgrade_ok is False

    def test_substitution_cannot_be_enabled(self):
        # v1 forbids substitution; it must not be reachable via the CLI.
        with pytest.raises(ValueError, match="unknown tolerance"):
            build_intent_mandate(
                category="running_shoes", max_price_paise=500000, min_return_days=21,
                max_delivery_days=3, quantity=1, tolerate=["substitution"],
                allowed_merchants=[MERCHANT], buyer_id="b", agent_id="a", ttl_minutes=15,
            )

    def test_expiry_is_tz_aware_and_future(self):
        p = build_intent_mandate(
            category="running_shoes", max_price_paise=500000, min_return_days=21,
            max_delivery_days=3, quantity=1, tolerate=[], allowed_merchants=[MERCHANT],
            buyer_id="b", agent_id="a", ttl_minutes=15,
        )
        assert p.expiry.tzinfo is not None
        assert p.expiry > datetime.now(timezone.utc)


class TestRoundTrip:
    def test_issued_token_parses_and_verifies(self, keys, resolver):
        token, payload = make_token(keys)
        parsed = parse_intent_mandate(token, keys["user_pub"])
        assert parsed.constraints.max_price_paise == 500000
        assert parsed.constraints.min_return_days == 21
        assert parsed.tolerances.return_ok is True
        assert parsed.nonce == payload.nonce

    def test_verifier_accepts_valid_mandate(self, keys, resolver):
        token, _ = make_token(keys)
        result = verify_intent_mandate(
            token, merchant_id=MERCHANT, allowed_categories=CATEGORIES, key_resolver=resolver
        )
        assert result.ok is True
        assert result.reason_code == VerifyReason.OK
        assert result.payload is not None


class TestVerifierFailPaths:
    def test_malformed_token(self, resolver):
        result = verify_intent_mandate(
            "not-a-jws", merchant_id=MERCHANT, allowed_categories=CATEGORIES, key_resolver=resolver
        )
        assert result.ok is False
        assert result.reason_code == VerifyReason.MANDATE_MALFORMED

    def test_tampered_payload_fails_signature(self, keys, resolver):
        token, _ = make_token(keys)
        header, payload_b64, sig = token.split(".")
        # Swap in a payload with a raised ceiling, keeping the original signature.
        import base64, json

        forged = {
            "type": "IntentMandate", "buyer_id": "did:acg:test-user-1",
            "agent_id": "agent://acg-buyer-standin",
            "constraints": {"category": "running_shoes", "max_price_paise": 99999999,
                            "min_return_days": 21, "max_delivery_days": 3, "quantity": 1},
            "tolerances": {"return_ok": True, "bundle_ok": True, "discount_ok": False,
                           "shipping_upgrade_ok": False, "substitution_ok": False},
            "allowed_merchants": [MERCHANT],
            "expiry": "2099-01-01T00:00:00Z", "nonce": "x",
        }
        b = base64.urlsafe_b64encode(canonical_json(forged).encode()).rstrip(b"=").decode()
        result = verify_intent_mandate(
            f"{header}.{b}.{sig}", merchant_id=MERCHANT,
            allowed_categories=CATEGORIES, key_resolver=resolver,
        )
        assert result.ok is False
        assert result.reason_code == VerifyReason.SIGNATURE_INVALID

    def test_wrong_signer_rejected(self, keys, resolver):
        # Signed by the attacker's key: signature is internally valid but the
        # signer is not the authorised user. Must NOT be accepted as a user mandate.
        token, _ = make_token(keys, kid="attacker-1")
        result = verify_intent_mandate(
            token, merchant_id=MERCHANT, allowed_categories=CATEGORIES,
            key_resolver=lambda kid: keys["user_pub"],  # merchant expects the USER key
        )
        assert result.ok is False
        assert result.reason_code == VerifyReason.SIGNATURE_INVALID

    def test_unknown_kid_rejected(self, keys):
        token, _ = make_token(keys)

        def _resolver(kid):
            from backend.common.keys import KeyNotFoundError

            raise KeyNotFoundError("no such kid")

        result = verify_intent_mandate(
            token, merchant_id=MERCHANT, allowed_categories=CATEGORIES, key_resolver=_resolver
        )
        assert result.ok is False
        assert result.reason_code == VerifyReason.SIGNATURE_INVALID

    def test_expired_mandate(self, keys, resolver):
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        token, _ = make_token(keys, expiry=past)
        result = verify_intent_mandate(
            token, merchant_id=MERCHANT, allowed_categories=CATEGORIES, key_resolver=resolver
        )
        assert result.ok is False
        assert result.reason_code == VerifyReason.MANDATE_EXPIRED

    def test_merchant_out_of_scope(self, keys, resolver):
        token, _ = make_token(keys, allowed_merchants=["merchant://someone-else"])
        result = verify_intent_mandate(
            token, merchant_id=MERCHANT, allowed_categories=CATEGORIES, key_resolver=resolver
        )
        assert result.ok is False
        assert result.reason_code == VerifyReason.MERCHANT_SCOPE

    def test_category_out_of_scope(self, keys, resolver):
        token, _ = make_token(keys, category="electronics")
        result = verify_intent_mandate(
            token, merchant_id=MERCHANT, allowed_categories=CATEGORIES, key_resolver=resolver
        )
        assert result.ok is False
        assert result.reason_code == VerifyReason.CATEGORY_SCOPE

    def test_signature_checked_before_expiry(self, keys):
        """A forged AND expired token must report SIGNATURE_INVALID, not EXPIRED.

        Order matters: we must never read a field for a decision before proving
        the payload is authentic.
        """
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        token, _ = make_token(keys, expiry=past, kid="attacker-1")
        result = verify_intent_mandate(
            token, merchant_id=MERCHANT, allowed_categories=CATEGORIES,
            key_resolver=lambda kid: keys["user_pub"],
        )
        assert result.reason_code == VerifyReason.SIGNATURE_INVALID


class TestCeiling:
    def test_ceiling_snapshot_contains_authority(self, keys, resolver):
        token, _ = make_token(keys)
        result = verify_intent_mandate(
            token, merchant_id=MERCHANT, allowed_categories=CATEGORIES, key_resolver=resolver
        )
        ceiling = build_ceiling(result.payload)
        assert ceiling["constraints"]["max_price_paise"] == 500000
        assert ceiling["tolerances"]["return_ok"] is True
        assert ceiling["tolerances"]["substitution_ok"] is False
        assert "nonce" in ceiling and "expiry" in ceiling


class TestCanonicalJson:
    def test_key_order_does_not_change_output(self):
        a = canonical_json({"b": 1, "a": 2})
        b = canonical_json({"a": 2, "b": 1})
        assert a == b == '{"a":2,"b":1}'

    def test_datetime_serialized_as_z_iso(self):
        out = canonical_json({"t": datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)})
        assert out == '{"t":"2026-01-02T03:04:05Z"}'


class TestSchemaValidation:
    def test_negative_price_rejected(self):
        with pytest.raises(Exception):
            IntentConstraints(category="x", max_price_paise=-1, min_return_days=0, max_delivery_days=1)

    def test_naive_expiry_rejected(self):
        with pytest.raises(Exception):
            IntentMandatePayload(
                buyer_id="b", agent_id="a",
                constraints=IntentConstraints(
                    category="running_shoes", max_price_paise=1000,
                    min_return_days=0, max_delivery_days=1,
                ),
                tolerances=IntentTolerances(),
                allowed_merchants=[MERCHANT],
                expiry=datetime(2099, 1, 1),  # naive
                nonce=new_nonce(),
            )

    def test_bad_json_payload_is_malformed(self, keys):
        import base64

        header = base64.urlsafe_b64encode(b'{"alg":"ES256","kid":"user-test-1"}').rstrip(b"=").decode()
        body = base64.urlsafe_b64encode(b"not json").rstrip(b"=").decode()
        with pytest.raises((MandateSignatureError, MandateMalformedError)):
            parse_intent_mandate(f"{header}.{body}.sig", keys["user_pub"])


class TestSignerAndTtl:
    """Consent + replay-window hardening (deep-review findings)."""

    def test_non_user_signer_is_rejected_when_pinned(self, keys, resolver):
        # attacker (non-user) self-signs a structurally valid mandate.
        token, _ = make_token(keys, kid="attacker-1")
        r = verify_intent_mandate(token, merchant_id=MERCHANT, allowed_categories=CATEGORIES,
                                  key_resolver=resolver, expected_signer_kid="user-test-1")
        assert not r.ok and r.reason_code == VerifyReason.SIGNER_NOT_AUTHORIZED

    def test_user_signer_accepted_when_pinned(self, keys, resolver):
        token, _ = make_token(keys)  # user-test-1
        r = verify_intent_mandate(token, merchant_id=MERCHANT, allowed_categories=CATEGORIES,
                                  key_resolver=resolver, expected_signer_kid="user-test-1")
        assert r.ok

    def test_signer_check_runs_before_signature(self, keys, resolver):
        # a user-kid header but attacker signature would fail signature; but an
        # attacker-kid header is rejected as SIGNER before we even verify the sig.
        token, _ = make_token(keys, kid="attacker-1")
        r = verify_intent_mandate(token, merchant_id=MERCHANT, allowed_categories=CATEGORIES,
                                  key_resolver=resolver, expected_signer_kid="user-test-1")
        assert r.reason_code == VerifyReason.SIGNER_NOT_AUTHORIZED  # not SIGNATURE_INVALID

    def test_expiry_beyond_max_ttl_rejected(self, keys, resolver):
        far = datetime.now(timezone.utc) + timedelta(hours=6)
        token, _ = make_token(keys, expiry=far)
        r = verify_intent_mandate(token, merchant_id=MERCHANT, allowed_categories=CATEGORIES,
                                  key_resolver=resolver, expected_signer_kid="user-test-1",
                                  max_ttl_seconds=3600)
        assert not r.ok and r.reason_code == VerifyReason.MANDATE_EXPIRED

    def test_expiry_within_max_ttl_ok(self, keys, resolver):
        token, _ = make_token(keys, ttl_minutes=15)
        r = verify_intent_mandate(token, merchant_id=MERCHANT, allowed_categories=CATEGORIES,
                                  key_resolver=resolver, expected_signer_kid="user-test-1",
                                  max_ttl_seconds=3600)
        assert r.ok

    def test_backward_compatible_when_no_signer_pinned(self, keys, resolver):
        # default (no expected_signer_kid) preserves old behavior for any valid signer.
        token, _ = make_token(keys)
        r = verify_intent_mandate(token, merchant_id=MERCHANT, allowed_categories=CATEGORIES,
                                  key_resolver=resolver)
        assert r.ok
