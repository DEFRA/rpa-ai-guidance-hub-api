import os
import sys

import fastapi.testclient
import pytest

import app.config
import app.entrypoints.fastapi
from app.common import aws
from app.guidance import conversion

client = fastapi.testclient.TestClient(app.entrypoints.fastapi.app)


@pytest.mark.usefixtures("aws_at_startup")
def test_lifespan(mocker):
    mock_mongo_client = mocker.AsyncMock()
    mock_get_mongo = mocker.patch(
        "app.common.mongo.get_mongo_client", return_value=mock_mongo_client
    )
    mocker.patch(
        "app.common.mongo.get_db", mocker.AsyncMock(return_value=mocker.MagicMock())
    )
    # Startup also makes the index a guide's uniqueness rests on, so that whatever
    # races the first two requests is racing an index that already exists.
    ensure_indexes = mocker.patch(
        "app.guidance.records.ensure_indexes", mocker.AsyncMock()
    )
    # And the one that makes a user's identity unique, for the same reason.
    ensure_user_indexes = mocker.patch(
        "app.users.records.ensure_indexes", mocker.AsyncMock()
    )
    # And pins kept as guide ids become pins, before a request reads one.
    convert_pins = mocker.patch(
        "app.users.records.convert_pins_kept_as_ids", mocker.AsyncMock()
    )

    with fastapi.testclient.TestClient(app.entrypoints.fastapi.app):
        mock_get_mongo.assert_called_once()
        ensure_indexes.assert_awaited_once()
        ensure_user_indexes.assert_awaited_once()
        convert_pins.assert_awaited_once()

    mock_mongo_client.close.assert_awaited_once()


def _a_lifespan_with_no_database(mocker):
    mocker.patch("app.common.mongo.get_mongo_client", mocker.AsyncMock())
    mocker.patch(
        "app.common.mongo.get_db", mocker.AsyncMock(return_value=mocker.MagicMock())
    )
    mocker.patch("app.guidance.records.ensure_indexes", mocker.AsyncMock())
    mocker.patch("app.users.records.ensure_indexes", mocker.AsyncMock())
    mocker.patch("app.users.records.convert_pins_kept_as_ids", mocker.AsyncMock())


def test_lifespan_works_the_queue(mocker, aws_at_startup):
    """The consumer starts with the API and stops with it, and requests send their
    jobs to its queue."""
    _a_lifespan_with_no_database(mocker)
    consumer_class = aws_at_startup.consumer_class
    state = app.entrypoints.fastapi.app.state

    with fastapi.testclient.TestClient(app.entrypoints.fastapi.app):
        consumer_class.return_value.start.assert_called_once()
        assert state.queue_consumer is consumer_class.return_value
        assert isinstance(state.conversion_submitter, conversion.QueueSubmitter)
        consumer_class.return_value.stop.assert_not_awaited()

    concurrency = app.config.get_config().conversions_concurrency
    assert consumer_class.call_args.kwargs["concurrency"] == concurrency
    aws_at_startup.get_s3_client.assert_called_once()
    consumer_class.return_value.stop.assert_awaited_once()


def test_lifespan_refuses_to_start_without_aws_credentials(mocker, aws_at_startup):
    """The buckets and the queue need them, and boto3 makes a client without any."""
    _a_lifespan_with_no_database(mocker)
    aws_at_startup.require_credentials.side_effect = aws.MissingCredentialsError("none")

    with (
        pytest.raises(aws.MissingCredentialsError),
        fastapi.testclient.TestClient(app.entrypoints.fastapi.app),
    ):
        pass

    aws_at_startup.consumer_class.assert_not_called()


def test_root():
    response = client.get("/")
    assert response.status_code == 404


def test_main_sets_proxy_envs(mocker, monkeypatch):
    mocker.patch("app.entrypoints.fastapi.uvicorn.run")

    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)

    mock_config = mocker.Mock()
    mock_config.http_proxy = "http://proxy:8080"
    mock_config.host = "127.0.0.1"
    mock_config.port = 9000
    mock_config.log_config = None
    mock_config.python_env = "production"

    # Force reload of the fastapi entrypoint module to get fresh imports
    if "app.entrypoints.fastapi" in sys.modules:
        del sys.modules["app.entrypoints.fastapi"]

    # Patch before importing
    mocker.patch("app.config.get_config", return_value=mock_config)

    from app.entrypoints.fastapi import main

    main()

    assert os.environ.get("HTTP_PROXY") == "http://proxy:8080"
    assert os.environ.get("HTTPS_PROXY") == "http://proxy:8080"


def test_main_no_proxy_in_config(mocker, monkeypatch):
    mocker.patch("app.entrypoints.fastapi.uvicorn.run")

    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)

    mock_config = mocker.Mock()
    mock_config.http_proxy = None
    mock_config.host = "127.0.0.1"
    mock_config.port = 8085
    mock_config.log_config = None
    mock_config.python_env = "production"

    # Force reload of the fastapi entrypoint module to get fresh imports
    if "app.entrypoints.fastapi" in sys.modules:
        del sys.modules["app.entrypoints.fastapi"]

    # Patch before importing
    mocker.patch("app.config.get_config", return_value=mock_config)

    from app.entrypoints.fastapi import main

    main()

    assert os.environ.get("HTTP_PROXY") is None
    assert os.environ.get("HTTPS_PROXY") is None
