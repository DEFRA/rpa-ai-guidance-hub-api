import os
import sys

import fastapi.testclient

import app.config
import app.entrypoints.fastapi
from app.guidance import conversion

client = fastapi.testclient.TestClient(app.entrypoints.fastapi.app)


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

    with fastapi.testclient.TestClient(app.entrypoints.fastapi.app):
        mock_get_mongo.assert_called_once()
        ensure_indexes.assert_awaited_once()
        ensure_user_indexes.assert_awaited_once()

    mock_mongo_client.close.assert_awaited_once()


def _a_lifespan_with_no_database(mocker):
    mocker.patch("app.common.mongo.get_mongo_client", mocker.AsyncMock())
    mocker.patch(
        "app.common.mongo.get_db", mocker.AsyncMock(return_value=mocker.MagicMock())
    )
    mocker.patch("app.guidance.records.ensure_indexes", mocker.AsyncMock())
    mocker.patch("app.users.records.ensure_indexes", mocker.AsyncMock())


def test_lifespan_works_the_queue_when_there_is_one(mocker):
    """The consumer starts with the API and stops with it, working the queue the
    setting names, and requests send their jobs to that queue."""
    _a_lifespan_with_no_database(mocker)
    real = app.config.get_config()
    mocker.patch(
        "app.config.get_config",
        return_value=real.model_copy(
            update={"conversions_sqs_queue_url": "http://floci:4566/0/q"}
        ),
    )
    mocker.patch("app.background.sqs.client")
    # Not the real one: it reaches for credentials, and stays behind in a global.
    get_s3_client = mocker.patch("app.common.s3.get_s3_client")
    consumer_class = mocker.patch("app.background.consumer.Consumer")
    consumer_class.return_value.stop = mocker.AsyncMock()
    state = app.entrypoints.fastapi.app.state
    state_before = (state.queue_consumer, state.conversion_submitter)

    try:
        with fastapi.testclient.TestClient(app.entrypoints.fastapi.app):
            consumer_class.return_value.start.assert_called_once()
            assert state.queue_consumer is consumer_class.return_value
            assert isinstance(state.conversion_submitter, conversion.QueueSubmitter)
            consumer_class.return_value.stop.assert_not_awaited()
    finally:
        state.queue_consumer, state.conversion_submitter = state_before

    assert (
        consumer_class.call_args.kwargs["concurrency"] == real.conversions_concurrency
    )
    get_s3_client.assert_called_once()
    consumer_class.return_value.stop.assert_awaited_once()


def test_lifespan_works_no_queue_when_there_is_none(mocker):
    _a_lifespan_with_no_database(mocker)
    consumer_class = mocker.patch("app.background.consumer.Consumer")
    state = app.entrypoints.fastapi.app.state

    with fastapi.testclient.TestClient(app.entrypoints.fastapi.app):
        assert state.queue_consumer is None
        assert isinstance(state.conversion_submitter, conversion.NoQueue)

    consumer_class.assert_not_called()


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
