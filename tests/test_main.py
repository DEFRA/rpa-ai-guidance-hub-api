import os
import sys

import fastapi.testclient

import app.entrypoints.fastapi

client = fastapi.testclient.TestClient(app.entrypoints.fastapi.app)


def test_lifespan(mocker):
    mock_mongo_client = mocker.AsyncMock()
    mock_get_mongo = mocker.patch(
        "app.common.mongo.get_mongo_client", return_value=mock_mongo_client
    )

    with fastapi.testclient.TestClient(app.entrypoints.fastapi.app):
        mock_get_mongo.assert_called_once()

    mock_mongo_client.close.assert_awaited_once()


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
