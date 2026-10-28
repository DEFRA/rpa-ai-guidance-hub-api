"""
Fake keys, tokens and constants for exercising the JWKS flow offline.

Everything here is FAKE and protects nothing. The throwaway private keys in
`keys/` sign the test tokens; the matching public halves are served by the vcrpy
cassettes in `cassettes/` in place of the issuer's JWKS endpoint. The real
`PyJWKClient` and `jwt.decode` still run, and any unexpected request fails the
test. If a key is regenerated, regenerate its public JWK in every cassette that
serves it (`one_key`, `rotated_keys`).
"""

import json
import pathlib
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

HERE = pathlib.Path(__file__).parent
CASSETTES = HERE / "cassettes"
JWKS_URI = "http://localhost/.well-known/jwks.json"
ISSUER = "https://cdp.test/issuer"
AUDIENCE = "rpa_ai_guidance_hub_api"
ACCOUNT = "123456789012"
UI_ARN = f"arn:aws:iam::{ACCOUNT}:role/rpa-ai-guidance-hub-ui"
OTHER_CALLER = "rpa-other-service"


def _load(name: str) -> tuple[str, rsa.RSAPrivateKey]:
    raw = (HERE / "keys" / f"{name}.jwk.json").read_text()
    key = jwt.algorithms.RSAAlgorithm.from_jwk(raw)
    assert isinstance(key, rsa.RSAPrivateKey)
    return json.loads(raw)["kid"], key


_KID_1, _KEY_1 = _load("signing-key")
_KID_2, _KEY_2 = _load("other-key")

# What each named token differs from a valid one by; `None` omits the claim.
_CLAIMS = {
    "valid": {},
    "expired": {"exp": 1_000_000_000},
    "wrong_issuer": {"iss": "https://evil.test"},
    "wrong_audience": {"aud": "some-other-api"},
    "no_expiry": {"exp": None},
    "not_yet_valid": {"nbf": lambda now: now + 3600},
    "other_account": {"sub": "arn:aws:iam::999999999999:role/rpa-ai-guidance-hub-ui"},
    "unknown_role": {"sub": f"arn:aws:iam::{ACCOUNT}:role/not-a-known-service"},
    "no_sub": {"sub": None},
}


def token(name: str) -> str:
    """
    A signed token for a named scenario.

    Claim scenarios are in `_CLAIMS`. The others are about keys and algorithms:
    `second_key` (valid, signed by the second key, for rotation),
    `forged_signature` (the first key's `kid` over the second key's signature),
    `hs256_confusion` and `alg_none`.
    """
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": UI_ARN,
        "iat": now,
        "exp": now + 300,
    }
    for claim, value in _CLAIMS.get(name, {}).items():
        claims[claim] = value(now) if callable(value) else value
    claims = {k: v for k, v in claims.items() if v is not None}

    if name == "second_key":
        return jwt.encode(claims, _KEY_2, "RS256", headers={"kid": _KID_2})
    if name == "forged_signature":
        return jwt.encode(claims, _KEY_2, "RS256", headers={"kid": _KID_1})
    if name == "hs256_confusion":
        return jwt.encode(claims, "x" * 64, "HS256", headers={"kid": _KID_1})
    if name == "alg_none":
        return jwt.encode(claims, None, "none", headers={"kid": _KID_1})
    return jwt.encode(claims, _KEY_1, "RS256", headers={"kid": _KID_1})
