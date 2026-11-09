"""The API's startup, with nothing real behind it, for tests that run the lifespan."""

from __future__ import annotations

import types

import pytest


@pytest.fixture
def aws_at_startup(mocker) -> types.SimpleNamespace:
    """AWS as startup reaches it, none of it real: the credentials, the S3 client, and
    the queue's client and consumer. What startup leaves in the app's state is taken
    away again afterwards."""
    import app.entrypoints.fastapi  # noqa: PLC0415 - not before conftest sets the env

    state = app.entrypoints.fastapi.app.state
    mocker.patch.object(state, "queue_consumer", create=True)
    mocker.patch.object(state, "conversion_submitter", create=True)
    consumer_class = mocker.patch("app.background.consumer.Consumer")
    consumer_class.return_value.stop = mocker.AsyncMock()
    return types.SimpleNamespace(
        require_credentials=mocker.patch("app.common.aws.require_credentials"),
        # Not the real one: it reaches for credentials, and stays behind in a global.
        get_s3_client=mocker.patch("app.common.s3.get_s3_client"),
        sqs_client=mocker.patch("app.background.sqs.client"),
        consumer_class=consumer_class,
    )
