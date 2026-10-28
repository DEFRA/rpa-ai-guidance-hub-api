import asyncio
from collections.abc import Awaitable, Callable

import fastapi
import jwt

from app import config
from app.auth import callers

_jwk_client = jwt.PyJWKClient(config.get_config().cdp_jwt_jwks_url or "")


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

        if caller is None or caller not in allowed:
            raise fastapi.HTTPException(status_code=fastapi.status.HTTP_403_FORBIDDEN)

        return caller

    return _check_caller


def allow_all_known() -> Callable[[fastapi.Request], Awaitable[callers.ServiceCaller]]:
    """Build a dependency that admits every caller in `KnownCallers`."""
    return allow(*callers.KnownCallers)


async def _verify_caller(request: fastapi.Request) -> dict:
    """
    Verify the caller of the API request.

    This function checks if the request is coming from an allowed source.
    If not, it raises an HTTPException with a 403 status code.

    Args:
        request (fastapi.Request): The incoming API request.
    """

    scheme, _, token = request.headers.get("Authorization", "").partition(" ")

    if scheme.lower() != "bearer" or not token:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_401_UNAUTHORIZED)

    signing_key: jwt.PyJWK = await asyncio.to_thread(
        _jwk_client.get_signing_key_from_jwt, token
    )

    try:
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=config.get_config().cdp_jwt_issuer,
            audience="rpa_ai_guidance_hub_api",
        )
    except jwt.ExpiredSignatureError as err:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_401_UNAUTHORIZED
        ) from err
    except jwt.InvalidTokenError as err:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_403_FORBIDDEN
        ) from err
