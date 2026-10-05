"""Testcontainers floci support, for the few S3 behaviours moto cannot vouch for.

moto imitates S3 in memory, and is what the store is tested against: it is fast and
needs nothing running. floci is the S3 stand-in the local stack runs, and it has
disagreed with moto before (ranged reads failing a checksum, found only in floci).
So the handful of S3 behaviours the service relies on to stay correct - a
create-only write being refused, and racing writes to one key leaving one winner -
are checked against floci too, started here the way `tests.support.mongo` starts
MongoDB: one container for the whole session, in CI as on a laptop.

The image is the one both compose files pin, so the tests and the local stack run
the same floci.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from botocore.exceptions import BotoCoreError, ClientError
from testcontainers.core.container import DockerContainer

FLOCI_IMAGE = (
    "floci/floci@sha256:"
    "b604da97e8c481c407f8e8d3300a8317d68058ac757dfa2af15fd6c4e9d25c59"  # 1.5.19
)
_PORT = 4566
_READY_WITHIN_SECONDS = 60


def _client(endpoint_url: str) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name="eu-west-2",
        aws_access_key_id="test",
        aws_secret_access_key="test",  # noqa: S106 - floci takes any credentials
    )


@pytest.fixture(scope="session")
def floci_endpoint() -> Iterator[str]:
    """A running floci for the session, answered as its S3 endpoint URL."""
    with DockerContainer(FLOCI_IMAGE).with_exposed_ports(_PORT) as container:
        endpoint = (
            f"http://{container.get_container_host_ip()}:"
            f"{container.get_exposed_port(_PORT)}"
        )
        deadline = time.monotonic() + _READY_WITHIN_SECONDS
        while True:
            try:
                _client(endpoint).list_buckets()
                break
            except BotoCoreError, ClientError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        yield endpoint


@pytest.fixture
def floci_s3(floci_endpoint: str) -> Iterator[tuple[Any, str]]:
    """A client against floci, and a bucket of the test's own, emptied after."""
    client = _client(floci_endpoint)
    bucket = f"test-{uuid.uuid4()}"
    client.create_bucket(
        Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": "eu-west-2"}
    )
    yield client, bucket
    listing = client.list_objects_v2(Bucket=bucket)
    for stored in listing.get("Contents", []):
        client.delete_object(Bucket=bucket, Key=stored["Key"])
    client.delete_bucket(Bucket=bucket)
