"""The API's queue in SQS, as the consumer's `Queue`: the client it polls with, what it
needs to read, and how a job is sent. boto3 blocks, so these run in a worker thread.

The queue's visibility timeout and how many deliveries it allows before dead-lettering
are read from the queue itself, so the consumer always agrees with the queue CDP's
tenant config made. A queue without a dead-letter queue is refused until it has one: a
message failing for ever would never reach the delivery that records its failure.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import boto3
import botocore.config

from app import config as app_config
from app.background import consumer

# A long poll waits up to 20 seconds for a message, so an answer may take longer than
# that; a connection still opens quickly or fails.
_CONNECT_SECONDS = 5
_READ_SECONDS = 30


class QueueConfigurationError(RuntimeError):
    """Raised for a queue that cannot be worked as it is set up."""


def client() -> Any:
    """An SQS client for the API's queue, against floci where it is configured."""
    config = app_config.get_config()
    return boto3.client(
        "sqs",
        endpoint_url=config.floci_endpoint_url,
        region_name=config.aws_region,
        config=botocore.config.Config(
            connect_timeout=_CONNECT_SECONDS, read_timeout=_READ_SECONDS
        ),
    )


class SqsQueue:
    """One SQS queue, by its URL."""

    def __init__(self, sqs_client: Any, url: str) -> None:
        self.client = sqs_client
        self.url = url

    async def settings(self) -> consumer.QueueSettings:
        return await asyncio.to_thread(self.read_settings)

    def read_settings(self) -> consumer.QueueSettings:
        """The queue's visibility timeout, and how many deliveries it allows.

        Raises:
            QueueConfigurationError: for a queue with no dead-letter queue.
        """
        attributes = self.client.get_queue_attributes(
            QueueUrl=self.url,
            AttributeNames=["VisibilityTimeout", "RedrivePolicy"],
        )["Attributes"]
        if "RedrivePolicy" not in attributes:
            message = f"{self.url} has no dead-letter queue"
            raise QueueConfigurationError(message)
        return consumer.QueueSettings(
            visibility_timeout=int(attributes["VisibilityTimeout"]),
            max_receive_count=int(
                json.loads(attributes["RedrivePolicy"])["maxReceiveCount"]
            ),
        )

    async def send(self, body: str) -> None:
        await asyncio.to_thread(
            self.client.send_message, QueueUrl=self.url, MessageBody=body
        )
