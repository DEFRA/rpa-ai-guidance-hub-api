"""
The auth flow end to end: HTTP request -> JWKS fetch (played back by vcrpy) ->
signature/claim checks -> caller check -> response and logs.
"""

import json
import logging
import re
from typing import Annotated

import fastapi
import pytest

from app import config
from app.auth import callers, service
from app.entrypoints import fastapi as entrypoint
from tests.auth import jwks

AUTH_LOGGER = "app.auth.service"
LEVELS = ["global", "router", "include", "route", "param"]
LEVELS_WITH_OPEN_SIBLING = [level for level in LEVELS if level != "global"]


def build_app(
    level: str,
    *,
    guard=None,
    route_guard=None,
    calls: list[str] | None = None,
) -> fastapi.FastAPI:
    """
    Build an app with `/secure` guarded at the given level and `/open` beside it.

    Levels: `global` (whole app), `router` (the router's own dependencies),
    `include` (passed to `include_router`), `route` (the route decorator) and
    `param` (a handler parameter, which also hands the handler the caller).
    `route_guard` stacks a second guard on the route itself.
    """
    calls = calls if calls is not None else []
    guard = guard or service.allow_all_known()
    dep = fastapi.Depends(guard)
    scoped = [dep]

    app = fastapi.FastAPI(dependencies=scoped if level == "global" else [])
    router = fastapi.APIRouter(dependencies=scoped if level == "router" else [])
    route_deps = scoped if level == "route" else []
    if route_guard:
        route_deps = [*route_deps, fastapi.Depends(route_guard)]

    if level == "param":

        @router.get("/secure")
        async def secure_param(
            caller: Annotated[callers.ServiceCaller, dep],
        ) -> dict[str, str]:
            calls.append("handler")
            return {"caller": caller.name}

    else:

        @router.get("/secure", dependencies=route_deps)
        async def secure(n: int = 0) -> dict[str, int]:
            calls.append("handler")
            return {"n": n}

    open_router = fastapi.APIRouter()

    @open_router.get("/open")
    async def open_route() -> dict[str, str]:
        return {"open": "yes"}

    app.include_router(router, dependencies=scoped if level == "include" else [])
    app.include_router(open_router)
    return app


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def assert_no_leak(token: str, response, caplog) -> None:
    """Neither the token nor PyJWT's own wording may reach logs or the response."""
    assert token not in caplog.text
    assert token not in response.text
    assert "Traceback" not in caplog.text
    for record in caplog.records:
        assert record.exc_info is None


@pytest.fixture
def client(make_client):
    return make_client(build_app("route"))


class TestHappyPath:
    def test_valid_token_from_a_known_caller_is_admitted(self, client, jwks_cassette):
        with jwks_cassette("one_key"):
            response = client.get("/secure", headers=bearer(jwks.token("valid")))

        assert response.status_code == 200

    def test_the_key_set_is_fetched_once_across_requests(self, client, jwks_cassette):
        with jwks_cassette("one_key") as cassette:
            for _ in range(3):
                token = jwks.token("valid")
                assert client.get("/secure", headers=bearer(token)).status_code == 200

        assert cassette.play_count == 1


class TestUnauthenticated:
    @pytest.mark.parametrize(
        "headers",
        [{}, {"Authorization": ""}, {"Authorization": "Bearer "}]
        + [{"Authorization": "Basic abc"}, {"Authorization": "abc.def.ghi"}],
        ids=["absent", "empty", "no-token", "basic-scheme", "no-scheme"],
    )
    def test_missing_or_non_bearer_credentials(
        self, client, jwks_cassette, caplog, headers
    ):
        with (
            caplog.at_level(logging.INFO, logger=AUTH_LOGGER),
            jwks_cassette("no_requests") as cassette,
        ):
            response = client.get("/secure", headers=headers)

        assert response.status_code == 401
        assert response.json()["detail"] == "Missing or malformed bearer token"
        assert "missing or malformed Authorization header" in caplog.text
        assert cassette.play_count == 0

    def test_expired_token(self, client, jwks_cassette, caplog):
        token = jwks.token("expired")

        with (
            caplog.at_level(logging.INFO, logger=AUTH_LOGGER),
            jwks_cassette("one_key"),
        ):
            response = client.get("/secure", headers=bearer(token))

        assert response.status_code == 401
        assert response.json()["detail"] == "Token has expired"
        assert "token expired" in caplog.text
        assert_no_leak(token, response, caplog)

    def test_a_token_that_is_not_a_jwt_is_unauthorised_not_a_server_error(
        self, client, jwks_cassette, caplog
    ):
        with (
            caplog.at_level(logging.INFO, logger=AUTH_LOGGER),
            jwks_cassette("one_key"),
        ):
            response = client.get("/secure", headers=bearer("not-a-jwt"))

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid token"
        assert_no_leak("not-a-jwt", response, caplog)

    def test_a_kid_the_issuer_does_not_publish(self, client, jwks_cassette, caplog):
        token = jwks.token("second_key")

        with (
            caplog.at_level(logging.INFO, logger=AUTH_LOGGER),
            jwks_cassette("one_key"),
        ):
            response = client.get("/secure", headers=bearer(token))

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid token"
        assert_no_leak(token, response, caplog)


class TestRejectedToken:
    @pytest.mark.parametrize(
        ("token_name", "logged_reason"),
        [
            ("forged_signature", "token signature is invalid"),
            ("wrong_issuer", "token issuer is not trusted"),
            ("wrong_audience", "token audience is not this API"),
            ("no_expiry", "token is missing a required claim"),
            ("not_yet_valid", "token is not yet valid"),
            ("hs256_confusion", "token is invalid"),
            ("alg_none", "token is invalid"),
        ],
    )
    def test_is_forbidden_and_the_reason_is_only_logged(
        self, client, jwks_cassette, caplog, token_name, logged_reason
    ):
        token = jwks.token(token_name)

        with (
            caplog.at_level(logging.INFO, logger=AUTH_LOGGER),
            jwks_cassette("one_key"),
        ):
            response = client.get("/secure", headers=bearer(token))

        assert response.status_code == 403
        assert response.json()["detail"] == "Invalid token"
        assert logged_reason in caplog.text
        assert_no_leak(token, response, caplog)


class TestRejectedSubject:
    @pytest.mark.parametrize(
        ("token_name", "sub"),
        [
            ("other_account", "arn:aws:iam::999999999999:role/rpa-ai-guidance-hub-ui"),
            ("unknown_role", f"arn:aws:iam::{jwks.ACCOUNT}:role/not-a-known-service"),
            ("no_sub", None),
        ],
    )
    def test_subject_that_is_not_a_known_caller(
        self, client, jwks_cassette, caplog, token_name, sub
    ):
        token = jwks.token(token_name)

        with (
            caplog.at_level(logging.INFO, logger=AUTH_LOGGER),
            jwks_cassette("one_key"),
        ):
            response = client.get("/secure", headers=bearer(token))

        assert response.status_code == 403
        assert response.json()["detail"] == "Forbidden"
        assert f"subject {sub!r} is not a known caller" in caplog.text
        assert_no_leak(token, response, caplog)

    def test_known_caller_the_route_does_not_admit(
        self, make_client, jwks_cassette, caplog
    ):
        client = make_client(build_app("route", guard=service.allow(jwks.OTHER_CALLER)))
        token = jwks.token("valid")

        with (
            caplog.at_level(logging.INFO, logger=AUTH_LOGGER),
            jwks_cassette("one_key"),
        ):
            response = client.get("/secure", headers=bearer(token))

        assert response.status_code == 403
        assert response.json()["detail"] == "Forbidden"
        assert (
            "caller rpa-ai-guidance-hub-ui is not permitted on GET /secure"
            in caplog.text
        )
        assert_no_leak(token, response, caplog)


class TestJwksEndpoint:
    def test_a_failing_endpoint_is_service_unavailable(
        self, client, jwks_cassette, caplog
    ):
        token = jwks.token("valid")

        with (
            caplog.at_level(logging.INFO, logger=AUTH_LOGGER),
            jwks_cassette("server_error"),
        ):
            response = client.get("/secure", headers=bearer(token))

        assert response.status_code == 503
        assert response.json()["detail"] == "Unable to verify token at this time"
        assert "could not fetch signing keys" in caplog.text
        assert_no_leak(token, response, caplog)

    def test_a_malformed_key_set_is_never_a_server_error(self, client, jwks_cassette):
        with jwks_cassette("malformed_keyset"):
            response = client.get("/secure", headers=bearer(jwks.token("valid")))

        assert response.status_code in {401, 503}

    def test_a_non_json_key_set_is_service_unavailable(self, client, auth_env, mocker):
        """
        Not played from a cassette: vcrpy's response stub swallows an exception
        raised inside `with response:`, which a real urllib response does not.
        """
        mocker.patch.object(
            auth_env, "fetch_data", side_effect=json.JSONDecodeError("bad", "<html>", 0)
        )

        response = client.get("/secure", headers=bearer(jwks.token("valid")))

        assert response.status_code == 503

    def test_an_unknown_kid_does_not_hammer_the_endpoint(self, client, jwks_cassette):
        """Within PyJWT's refresh cooldown a stranger's kid costs no extra fetches."""
        with jwks_cassette("one_key") as cassette:
            for _ in range(3):
                token = jwks.token("second_key")
                assert client.get("/secure", headers=bearer(token)).status_code == 401

        assert cassette.play_count == 1

    def test_a_rotated_key_is_picked_up_once_the_cooldown_has_passed(
        self, client, jwks_cassette, auth_env
    ):
        with jwks_cassette("rotated_keys") as cassette:
            old = client.get("/secure", headers=bearer(jwks.token("valid")))
            auth_env._last_successful_fetch -= 1_000
            new = client.get("/secure", headers=bearer(jwks.token("second_key")))

        assert (old.status_code, new.status_code) == (200, 200)
        assert cassette.play_count == 2


class TestGuardLevels:
    @pytest.fixture
    def serve(self, make_client, jwks_cassette):
        def _serve(
            level: str,
            path: str,
            token: str | None,
        ):
            client = make_client(build_app(level))
            headers = bearer(token) if token else {}
            with jwks_cassette("one_key") as c:
                return client.get(path, headers=headers), c

        return _serve

    @pytest.mark.parametrize("level", LEVELS)
    def test_no_token_is_unauthorised(self, serve, level):
        response, cassette = serve(level, "/secure", None)

        assert response.status_code == 401
        assert cassette.play_count == 0

    @pytest.mark.parametrize("level", LEVELS)
    def test_forged_token_is_forbidden(self, serve, level):
        response, _ = serve(level, "/secure", jwks.token("forged_signature"))

        assert response.status_code == 403

    @pytest.mark.parametrize("level", LEVELS)
    def test_valid_token_is_admitted(self, serve, level):
        response, _ = serve(level, "/secure", jwks.token("valid"))

        assert response.status_code == 200

    def test_a_parameter_guard_hands_the_handler_the_caller(self, serve):
        response, _ = serve("param", "/secure", jwks.token("valid"))

        assert response.json() == {"caller": "rpa-ai-guidance-hub-ui"}

    @pytest.mark.parametrize("level", LEVELS_WITH_OPEN_SIBLING)
    def test_a_guard_does_not_leak_onto_an_unguarded_sibling(self, serve, level):
        response, cassette = serve(level, "/open", None)

        assert response.status_code == 200
        assert cassette.play_count == 0

    def test_a_global_guard_covers_every_route(self, serve):
        response, _ = serve("global", "/open", None)

        assert response.status_code == 401


class TestStackedGuards:
    def test_the_most_restrictive_guard_wins(self, make_client, jwks_cassette, caplog):
        """Admitted by the global guard, refused by the one on the route."""
        app = build_app("global", route_guard=service.allow(jwks.OTHER_CALLER))
        client = make_client(app)

        with (
            caplog.at_level(logging.INFO, logger=AUTH_LOGGER),
            jwks_cassette("one_key"),
        ):
            response = client.get("/secure", headers=bearer(jwks.token("valid")))

        assert response.status_code == 403
        assert "not permitted on GET /secure" in caplog.text

    def test_a_failed_guard_never_reaches_the_handler(self, make_client, jwks_cassette):
        calls: list[str] = []
        client = make_client(build_app("router", calls=calls))

        with jwks_cassette("one_key"):
            client.get("/secure", headers=bearer(jwks.token("forged_signature")))

        assert calls == []

    def test_the_guard_answers_before_validation_does(self, make_client, jwks_cassette):
        client = make_client(build_app("global"))

        with jwks_cassette("no_requests"):
            response = client.get("/secure?n=not-a-number")

        assert response.status_code == 401

    def test_one_guard_instance_is_verified_once_per_request(
        self, mocker, make_client, jwks_cassette
    ):
        verify = mocker.spy(service, "_verify_caller")
        guard = service.allow_all_known()
        client = make_client(build_app("global", guard=guard, route_guard=guard))

        with jwks_cassette("one_key") as cassette:
            response = client.get("/secure", headers=bearer(jwks.token("valid")))

        assert response.status_code == 200
        assert verify.call_count == 1
        assert cassette.play_count == 1

    def test_separate_guards_verify_separately_but_share_the_key_set(
        self, mocker, make_client, jwks_cassette
    ):
        verify = mocker.spy(service, "_verify_caller")
        client = make_client(build_app("global", route_guard=service.allow_all_known()))

        with jwks_cassette("one_key") as cassette:
            response = client.get("/secure", headers=bearer(jwks.token("valid")))

        assert response.status_code == 200
        assert verify.call_count == 2
        assert cassette.play_count == 1


class TestRealAppWiring:
    @pytest.fixture
    def real_app(self, request, make_client):
        request.getfixturevalue("auth_env")
        return make_client(entrypoint.app)

    def test_health_stays_open_and_never_touches_the_issuer(
        self, real_app, jwks_cassette
    ):
        with jwks_cassette("no_requests") as cassette:
            response = real_app.get("/health")

        assert response.status_code == 200
        assert cassette.play_count == 0

    def test_a_protected_route_demands_a_token(self, real_app, jwks_cassette):
        with jwks_cassette("no_requests") as cassette:
            response = real_app.get("/reference/schemes")

        assert response.status_code == 401
        assert cassette.play_count == 0

    def test_a_protected_route_admits_a_valid_token(self, real_app, jwks_cassette):
        with jwks_cassette("one_key"):
            response = real_app.get(
                "/reference/schemes", headers=bearer(jwks.token("valid"))
            )

        assert response.status_code == 200

    def test_a_protected_route_refuses_a_forged_token(self, real_app, jwks_cassette):
        with jwks_cassette("one_key"):
            response = real_app.get(
                "/reference/schemes",
                headers=bearer(jwks.token("forged_signature")),
            )

        assert response.status_code == 403

    @pytest.mark.usefixtures("auth_env")
    def test_auth_can_be_switched_off(self, mocker, make_client):
        off = config.get_config().model_copy(update={"use_auth": False})
        mocker.patch("app.config.get_config", return_value=off)
        client = make_client(entrypoint.app)

        assert client.get("/reference/schemes").status_code == 200

    def test_every_documented_operation_demands_a_token(self, real_app, jwks_cassette):
        """
        A new router cannot ship unauthenticated without someone editing this.

        Walks the OpenAPI document rather than FastAPI's route tree, whose
        include-time structure is private and changes between releases. Guards
        answer before path/body validation, so placeholder values are enough.
        """
        paths = real_app.app.openapi()["paths"]
        assert paths

        with jwks_cassette("no_requests") as cassette:
            statuses = {
                (method.upper(), path): real_app.request(
                    method, re.sub(r"\{[^}]+\}", "x", path)
                ).status_code
                for path, operations in paths.items()
                for method in operations
            }

        assert {op: code for op, code in statuses.items() if code != 401} == {}
        assert cassette.play_count == 0
