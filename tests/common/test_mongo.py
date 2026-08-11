import pytest

from app import config as app_config
from app.common import mongo


@pytest.fixture(autouse=True)
def reset_mongo_client():
    mongo.client = None
    mongo.db = None
    yield
    mongo.client = None
    mongo.db = None


@pytest.mark.asyncio
async def test_get_mongo_client_initialization(mocker):
    mock_client_cls = mocker.patch("app.common.mongo.pymongo.AsyncMongoClient")
    mock_instance = mock_client_cls.return_value

    mock_db = mocker.MagicMock()
    mock_instance.get_database.return_value = mock_db
    mock_db.command = mocker.AsyncMock(return_value={"ok": 1})

    client = await mongo.get_mongo_client()

    assert client == mock_instance
    mock_client_cls.assert_called_once_with(
        app_config.get_config().mongo_uri, uuidRepresentation="standard"
    )
    mock_db.command.assert_awaited_once_with("ping")


@pytest.mark.asyncio
async def test_get_mongo_client_with_custom_tls(mocker):
    mock_config = mocker.Mock()
    mock_config.mongo_truststore = "custom-cert-key"
    mock_config.mongo_uri = "mongodb://localhost:27017"
    mock_config.mongo_database = "rpa-ai-guidance-hub-api"

    mocker.patch("app.config.get_config", return_value=mock_config)

    mocker.patch.dict(
        "app.common.tls.custom_ca_certs", {"custom-cert-key": "/path/to/cert.pem"}
    )

    mock_client_cls = mocker.patch("app.common.mongo.pymongo.AsyncMongoClient")
    mock_instance = mock_client_cls.return_value
    mock_db = mocker.MagicMock()
    mock_instance.get_database.return_value = mock_db
    mock_db.command = mocker.AsyncMock(return_value={"ok": 1})

    await mongo.get_mongo_client()

    mock_client_cls.assert_called_once_with(
        "mongodb://localhost:27017",
        tlsCAFile="/path/to/cert.pem",
        uuidRepresentation="standard",
    )


@pytest.mark.asyncio
async def test_get_mongo_client_returns_existing(mocker):
    existing_client = mocker.Mock()
    mongo.client = existing_client

    mock_client_cls = mocker.patch("app.common.mongo.pymongo.AsyncMongoClient")

    result = await mongo.get_mongo_client()

    assert result == existing_client
    mock_client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_get_db(mocker):
    mock_client = mocker.MagicMock()
    mock_db = mocker.Mock()
    mock_client.get_database.return_value = mock_db

    result = await mongo.get_db(mock_client)
    assert result == mock_db
    mock_client.get_database.assert_called_once_with(
        app_config.get_config().mongo_database
    )

    result2 = await mongo.get_db(mock_client)
    assert result2 == mock_db
    assert mock_client.get_database.call_count == 1
