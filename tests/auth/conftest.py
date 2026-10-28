import contextlib
from collections.abc import Callable, Iterator
from typing import Any

import jwt
import pytest
import vcr
from fastapi import testclient

from app import config
from app.auth import callers, service
from tests.auth import jwks


@pytest.fixture
def auth_env(mocker, monkeypatch) -> jwt.PyJWKClient:
    """
    Point the auth flow at the test issuer with a cold JWKS client.

    `service._jwk_client` is module-level and caches the key set, so each test
    needs its own or they would leak keys into one another. Config is a copy of
    the real one, so unrelated settings (reference lists etc.) still work.
    """
    patched = config.get_config().model_copy(
        update={
            "aws_account": jwks.ACCOUNT,
            "cdp_jwt_issuer": jwks.ISSUER,
            "cdp_jwt_jwks_uri": jwks.JWKS_URI,
            "use_auth": True,
        }
    )
    mocker.patch("app.config.get_config", return_value=patched)

    client = jwt.PyJWKClient(jwks.JWKS_URI)
    monkeypatch.setattr(service, "_jwk_client", client)
    monkeypatch.setitem(
        callers.CALLERS_BY_NAME,
        jwks.OTHER_CALLER,
        callers.ServiceCaller(name=jwks.OTHER_CALLER),
    )
    return client


@pytest.fixture
def jwks_cassette(request) -> Callable[[str], contextlib.AbstractContextManager]:
    """
    Play back `cassettes/<name>.yaml` as the JWKS endpoint.

    Responses are served in order and any further request fails the test. Yields
    the vcr cassette so tests can assert on `play_count`.
    """
    request.getfixturevalue("auth_env")

    @contextlib.contextmanager
    def _use(name: str) -> Iterator[Any]:
        recorder = vcr.VCR(record_mode="none")
        path = jwks.CASSETTES / f"{name}.yaml"
        with recorder.use_cassette(str(path)) as cassette:
            yield cassette

    return _use


@pytest.fixture
def make_client() -> Callable[..., testclient.TestClient]:
    def _make(app) -> testclient.TestClient:
        return testclient.TestClient(app, raise_server_exceptions=False)

    return _make
