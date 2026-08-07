from logging import getLogger

import fastapi
import pymongo

from app import config as app_config
from app.common import tls

logger = getLogger(__name__)

client: pymongo.AsyncMongoClient | None = None
db: pymongo.asynchronous.database.AsyncDatabase | None = None


async def get_mongo_client() -> pymongo.AsyncMongoClient:
    global client
    if client is None:
        config = app_config.get_config()
        cert = tls.custom_ca_certs.get(config.mongo_truststore)
        if cert:
            logger.info(
                "Creating MongoDB client with custom TLS cert %s",
                config.mongo_truststore,
            )
            client = pymongo.AsyncMongoClient(
                config.mongo_uri,
                tlsCAFile=cert,
                uuidRepresentation="standard",
            )
        else:
            logger.info("Creating MongoDB client")
            client = pymongo.AsyncMongoClient(
                config.mongo_uri, uuidRepresentation="standard"
            )

        logger.info("Testing MongoDB connection to %s", config.mongo_uri)
        await check_connection(client)
    return client


async def get_db(
    client: pymongo.AsyncMongoClient = fastapi.Depends(get_mongo_client),
) -> pymongo.asynchronous.database.AsyncDatabase:
    global db
    if db is None:
        db = client.get_database(app_config.get_config().mongo_database)
    return db


async def check_connection(client: pymongo.AsyncMongoClient) -> None:
    database = await get_db(client)
    response = await database.command("ping")
    logger.info("MongoDB PING %s", response)
