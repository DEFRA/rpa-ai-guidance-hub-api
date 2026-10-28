import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import fastapi
import jwt

from app import config
from app.auth import callers

logger = logging.getLogger(__name__)

# Reasons are logged only. Responses stay generic so callers cannot probe which
# check failed.
_INVALID_TOKEN_REASONS: dict[type[jwt.InvalidTokenError], str] = {
    jwt.InvalidSignatureError: "token signature is invalid",
    jwt.InvalidIssuerError: "token issuer is not trusted",
    jwt.InvalidAudienceError: "token audience is not this API",
    jwt.MissingRequiredClaimError: "token is missing a required claim",
    jwt.ImmatureSignatureError: "token is not yet valid",
}

_jwk_client: jwt.PyJWKClient | None = None


def allow(
    *names: str,
) -> Callable[[fastapi.Request], Awaitable[callers.ServiceCaller]]:
    """
    Build a dependency that admits only the named callers.

    Names are resolved when the route is declared, so a typo fails at import
    rather than turning every request into a 403.

    Args:
        *names: Names of the callers to admit, as listed in `KnownCallers`.
    """
    if not names:
        msg = "At least one caller must be specified."
        raise ValueError(msg)

    allowed: set[callers.ServiceCaller] = set()

    for name in names:
        caller = callers.get_caller_by_name(name)

        if caller is None:
            msg = f"Unknown caller: {name}"
            raise ValueError(msg)

        allowed.add(caller)

    async def _check_caller(request: fastapi.Request) -> callers.ServiceCaller:
        claims = await _verify_caller(request)
        sub = claims.get("sub")
        caller = callers.get_caller_by_arn(sub) if sub else None

        if caller is None:
            logger.warning("Auth rejected: subject %r is not a known caller", sub)
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_403_FORBIDDEN,
                detail="Forbidden",
            )

        if caller not in allowed:
            logger.warning(
                "Auth rejected: caller %s is not permitted on %s %s",
                caller.name,
                request.method,
                request.url.path,
            )
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_403_FORBIDDEN,
                detail="Forbidden",
            )

        return caller

    return _check_caller


def allow_all_known() -> Callable[[fastapi.Request], Awaitable[callers.ServiceCaller]]:
    """Build a dependency that admits every caller in `KnownCallers`."""
    return allow(*callers.KnownCallers)


def _get_jwk_client() -> jwt.PyJWKClient:
    """Return the JWKS client, creating it if necessary."""
    global _jwk_client

    jwks_uri = config.get_config().cdp_jwt_jwks_uri

    if not jwks_uri:
        msg = "WebIdentity auth is enabled but no JWKS URI is configured."
        raise RuntimeError(msg)

    if _jwk_client is None:
        _jwk_client = jwt.PyJWKClient(config.get_config().cdp_jwt_jwks_uri or "")

    return _jwk_client


async def _verify_caller(request: fastapi.Request) -> dict:
    """
    Verify the caller of the API request.

    This function checks if the request is coming from an allowed source.
    If not, it raises an HTTPException with a 403 status code.

    Args:
        request (fastapi.Request): The incoming API request.
    """

    jwt_client = _get_jwk_client()

    scheme, _, token = request.headers.get("Authorization", "").partition(" ")

    if scheme.lower() != "bearer" or not token:
        logger.warning("Auth rejected: missing or malformed Authorization header")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed bearer token",
        )

    try:
        signing_key: jwt.PyJWK = await asyncio.to_thread(
            jwt_client.get_signing_key_from_jwt, token
        )
    except (jwt.PyJWKClientConnectionError, json.JSONDecodeError) as err:
        logger.error("Auth failed: could not fetch signing keys")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to verify token at this time",
        ) from err
    except jwt.PyJWTError as err:
        logger.warning("Auth rejected: no usable signing key for token: %s", err)
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from err

    try:
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=config.get_config().cdp_jwt_issuer,
            audience="rpa_ai_guidance_hub_api",
            options={"require": ["exp"]},
        )
    except jwt.ExpiredSignatureError as err:
        logger.warning("Auth rejected: token expired")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        ) from err
    except jwt.InvalidTokenError as err:
        reason = _INVALID_TOKEN_REASONS.get(type(err), "token is invalid")
        logger.warning("Auth rejected: %s (%s)", reason, err)
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_403_FORBIDDEN,
            detail="Invalid token",
        ) from err
