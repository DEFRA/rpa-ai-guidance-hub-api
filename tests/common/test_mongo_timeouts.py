"""Unit tests for the client-side MongoDB operation timeouts.

`pymongo.timeout` is patched to a no-op context manager that records the value it is
called with, so each test can assert the tier the decorator applied. A simulated driver
timeout error (``.timeout == True``) confirms it is translated to the domain exception,
while non-timeout errors are left to propagate unchanged.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

import pymongo
import pytest

from app.common import mongo_timeouts

_TIERS = {
    "FAST": mongo_timeouts.FAST,
    "STANDARD": mongo_timeouts.STANDARD,
    "HEAVY": mongo_timeouts.HEAVY,
}


class _DriverTimeout(pymongo.errors.NetworkTimeout):
    """A driver error carrying `.timeout == True`, as `pymongo.timeout` raises."""

    # emulate what the driver sets when the client-side deadline expires
    timeout = True


def _record_timeout(mocker):
    """Patch `pymongo.timeout` to record its argument and return a no-op context."""
    captured: dict[str, float | None] = {}

    def fake(timeout: float | None) -> Any:
        captured["seconds"] = timeout
        return contextlib.nullcontext()

    mocker.patch("app.common.mongo_timeouts.pymongo.timeout", side_effect=fake)
    return captured


@pytest.mark.parametrize("expected", _TIERS.values(), ids=_TIERS.keys())
async def test_async_wrapped_operation_applies_the_tier(expected, mocker):
    captured = _record_timeout(mocker)

    @mongo_timeouts.mongo(expected)
    async def op() -> str:
        return "ok"

    assert await op() == "ok"
    assert captured["seconds"] == expected


def test_method_name_and_wrapping_are_preserved(mocker):
    _record_timeout(mocker)

    @mongo_timeouts.mongo(mongo_timeouts.FAST)
    def op() -> str:
        return "ok"

    assert op.__name__ == "op"


async def test_timeout_error_is_translated_to_domain_exception(mocker):
    _record_timeout(mocker)

    @mongo_timeouts.mongo(mongo_timeouts.FAST)
    async def op() -> None:
        message = "the server would not reply in time"
        raise _DriverTimeout(message)

    with pytest.raises(mongo_timeouts.MongoTimeoutError) as info:
        await op()

    assert info.value.timeout == mongo_timeouts.FAST


async def test_non_timeout_error_is_not_translated(mocker):
    captured = _record_timeout(mocker)

    @mongo_timeouts.mongo(mongo_timeouts.FAST)
    async def op() -> None:
        message = "already there"
        raise pymongo.errors.DuplicateKeyError(message)

    with pytest.raises(pymongo.errors.DuplicateKeyError):
        await op()

    assert captured["seconds"] == mongo_timeouts.FAST


async def test_timeout_is_logged_with_ecs_extras(mocker, caplog):
    _record_timeout(mocker)

    @mongo_timeouts.mongo(mongo_timeouts.FAST)
    async def op() -> None:
        message = "the server would not reply in time"
        raise _DriverTimeout(message)

    with (
        caplog.at_level(logging.ERROR, logger="app.common.mongo_timeouts"),
        pytest.raises(mongo_timeouts.MongoTimeoutError),
    ):
        await op()

    record = caplog.records[0]
    assert record.exception_type == mongo_timeouts.MongoTimeoutError.exception_type
    assert record.operation.endswith("op")
    assert record.timeout_seconds == mongo_timeouts.FAST


async def test_a_timeout_is_answered_as_service_unavailable():
    response = await mongo_timeouts.handle(
        None, mongo_timeouts.MongoTimeoutError("slow", operation="op", timeout=2)
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"


def test_the_app_answers_a_timeout_with_the_handler():
    from app.entrypoints import fastapi as entrypoint  # noqa: PLC0415

    handlers = entrypoint.app.exception_handlers
    assert handlers[mongo_timeouts.MongoTimeoutError] is mongo_timeouts.handle


@pytest.mark.parametrize("raw", ["inf", "-inf", "nan", "0", "-1", "soon", ""])
def test_an_unusable_tier_setting_is_refused_by_name(raw, monkeypatch):
    monkeypatch.setenv("MONGO_FAST_SECONDS", raw)

    with pytest.raises(ValueError, match="MONGO_FAST_SECONDS"):
        mongo_timeouts._env_seconds("FAST", 2.0)


def test_a_tier_setting_is_read_and_the_default_used_when_unset(monkeypatch):
    monkeypatch.setenv("MONGO_FAST_SECONDS", "1.5")
    monkeypatch.delenv("MONGO_HEAVY_SECONDS", raising=False)

    assert mongo_timeouts._env_seconds("FAST", 2.0) == 1.5  # noqa: PLR2004
    assert mongo_timeouts._env_seconds("HEAVY", 30.0) == 30.0  # noqa: PLR2004
