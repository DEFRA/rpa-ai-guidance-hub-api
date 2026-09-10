"""A shared S3 client, lazily built the same way `common.mongo` builds its client.

`floci_endpoint_url` is passed explicitly rather than relying on boto3's own
`AWS_ENDPOINT_URL` env-var detection: every other client in this app takes its
endpoint from `AppConfig` rather than an ambient env var, and doing the same here
keeps `get_s3_client` overridable in a test the way `get_mongo_client` already is.
`None` locally falls through to boto3's normal region/credential resolution, which
is what talking to real AWS in CDP needs.

Typed `Any`, not a boto3 stub type: boto3 clients are generated dynamically at
runtime and carry no such static type to reference, which is also why `boto3.*` is
blanket-`ignore_missing_imports`d in `pyproject.toml`.
"""

from logging import getLogger
from typing import Any

import boto3

from app import config as app_config

logger = getLogger(__name__)

client: Any = None


def get_s3_client() -> Any:
    global client

    if client is None:
        config = app_config.get_config()

        logger.info(
            "Creating S3 client%s",
            f" against {config.floci_endpoint_url}"
            if config.floci_endpoint_url
            else "",
        )

        client = boto3.client(
            "s3",
            endpoint_url=config.floci_endpoint_url,
            region_name=config.aws_region,
        )

    return client
