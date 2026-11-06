"""The API's queue in SQS, as the consumer's `Queue`. boto3 blocks, so each call runs
in a worker thread.

The queue's visibility timeout and how many deliveries it allows before dead-lettering
are read from the queue itself, so the consumer always agrees with the queue CDP's
tenant config made. A queue without a dead-letter queue is refused until it has one: a
message failing for ever would never reach the delivery that records its failure.
"""

from __future__ import annotations

import asyncio
import functools
import json
from concurrent import futures
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
    """One SQS queue, by its URL.

    An idle long poll holds its thread for the whole of its wait, and the consumer
    has a loop polling for each message it can work. So polls wait on `polling_threads`
    threads of their own, leaving those that `asyncio.to_thread` shares with the rest
    of the API free. Every other call is brief, and uses those.
    """

    def __init__(self, sqs_client: Any, url: str, *, polling_threads: int) -> None:
        self._client = sqs_client
        self._url = url
        self._polling_threads = futures.ThreadPoolExecutor(
            max_workers=polling_threads, thread_name_prefix="sqs-poll"
        )

    async def settings(self) -> consumer.QueueSettings:
        """The queue's visibility timeout, and how many deliveries it allows.

        Raises:
            QueueConfigurationError: for a queue with no dead-letter queue.
        """
        attributes = (
            await asyncio.to_thread(
                self._client.get_queue_attributes,
                QueueUrl=self._url,
                AttributeNames=["VisibilityTimeout", "RedrivePolicy"],
            )
        )["Attributes"]
        if "RedrivePolicy" not in attributes:
            message = f"{self._url} has no dead-letter queue"
            raise QueueConfigurationError(message)
        return consumer.QueueSettings(
            visibility_timeout=int(attributes["VisibilityTimeout"]),
            max_receive_count=int(
                json.loads(attributes["RedrivePolicy"])["maxReceiveCount"]
            ),
        )

    async def receive(self, wait_seconds: int) -> consumer.Received | None:
        poll = functools.partial(
            self._client.receive_message,
            QueueUrl=self._url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=wait_seconds,
            MessageSystemAttributeNames=["ApproximateReceiveCount"],
        )
        answer = await asyncio.get_running_loop().run_in_executor(
            self._polling_threads, poll
        )
        messages = answer.get("Messages")
        if not messages:
            return None
        message = messages[0]
        return consumer.Received(
            id=message["MessageId"],
            body=message["Body"],
            receipt=message["ReceiptHandle"],
            receive_count=int(message["Attributes"]["ApproximateReceiveCount"]),
        )

    async def delete(self, receipt: str) -> None:
        await asyncio.to_thread(
            self._client.delete_message, QueueUrl=self._url, ReceiptHandle=receipt
        )

    async def extend(self, receipt: str, seconds: int) -> None:
        await asyncio.to_thread(
            self._client.change_message_visibility,
            QueueUrl=self._url,
            ReceiptHandle=receipt,
            VisibilityTimeout=seconds,
        )

    async def send(self, body: str) -> None:
        await asyncio.to_thread(
            self._client.send_message, QueueUrl=self._url, MessageBody=body
        )
