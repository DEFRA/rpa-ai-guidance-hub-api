"""Factory for creating boto3 S3 clients from application config."""

from typing import Any

import boto3

from app import config

settings = config.get_config()


def create_s3_client() -> Any:
    """Create a boto3 S3 client using application configuration."""

    return boto3.client(
        "s3",
        region_name=settings.aws_region,
        endpoint_url=settings.floci_endpoint_url,
    )
