"""The API's queue in SQS, proved against floci over real HTTP: sending, receiving,
deleting, keeping a message invisible, dead-lettering, and going back to work when a
queue with a problem is put right, as the local stack and CDP's queue have them
(standard, dead-lettered after 3 receives). The visibility timeout is a second here,
not CDP's 120, so a redelivery takes a second.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import boto3
import pytest

from app.background import consumer, sqs

# The queue's own settings, as CDP's are but quicker.
VISIBILITY_SECONDS = 1
MAX_RECEIVES = 3


@pytest.fixture
def client(floci_endpoint: str) -> Any:
    return boto3.client(
        "sqs",
        endpoint_url=floci_endpoint,
        region_name="eu-west-2",
        aws_access_key_id="test",
        aws_secret_access_key="test",  # noqa: S106 - floci takes any credentials
    )


def _with_dead_letters(client, name: str) -> dict[str, str]:
    """The attributes of a queue called `name`, sending to its dead-letter queue."""
    dead_letters = client.create_queue(QueueName=f"{name}-deadletter")["QueueUrl"]
    arn = client.get_queue_attributes(
        QueueUrl=dead_letters, AttributeNames=["QueueArn"]
    )["Attributes"]["QueueArn"]
    redrive = json.dumps(
        {"deadLetterTargetArn": arn, "maxReceiveCount": str(MAX_RECEIVES)}
    )
    return {"VisibilityTimeout": str(VISIBILITY_SECONDS), "RedrivePolicy": redrive}


@pytest.fixture
def queues(client) -> tuple[str, str]:
    """A queue of its own for each test, and its dead-letter queue: the URLs."""
    name = f"conversions-{uuid.uuid4().hex[:12]}"
    url = client.create_queue(
        QueueName=name, Attributes=_with_dead_letters(client, name)
    )["QueueUrl"]
    return url, client.get_queue_url(QueueName=f"{name}-deadletter")["QueueUrl"]


class Recorder:
    """A handler doing what it is told, and recording what it was given."""

    def __init__(self, outcome: Exception | None = None, seconds: float = 0) -> None:
        self.outcome = outcome
        self.seconds = seconds
        self.given: list[tuple[str, bool]] = []

    async def __call__(self, body: str, *, last_delivery: bool) -> None:
        self.given.append((body, last_delivery))
        await asyncio.sleep(self.seconds)
        if self.outcome is not None:
            raise self.outcome


def _count(client, url: str) -> int:
    """Every message the queue holds, visible or not."""
    attributes = client.get_queue_attributes(
        QueueUrl=url,
        AttributeNames=[
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
        ],
    )["Attributes"]
    return sum(int(value) for value in attributes.values())


async def _until(condition, timeout: float) -> None:
    async with asyncio.timeout(timeout):
        while not await asyncio.to_thread(condition):
            await asyncio.sleep(0.1)


def _working(queue: sqs.SqsQueue, handler: Recorder) -> consumer.Consumer:
    working = consumer.Consumer(queue, handler, concurrency=2, wait_seconds=1)
    working.start()
    return working


class TestTheQueue:
    async def test_has_its_settings_read_from_it(self, client, queues):
        url, _ = queues

        settings = await sqs.SqsQueue(client, url).settings()

        assert settings == consumer.QueueSettings(VISIBILITY_SECONDS, MAX_RECEIVES)

    async def test_without_a_dead_letter_queue_is_refused(self, client):
        url = client.create_queue(QueueName=f"plain-{uuid.uuid4().hex[:12]}")[
            "QueueUrl"
        ]

        queue = sqs.SqsQueue(client, url)

        with pytest.raises(sqs.QueueConfigurationError):
            await queue.settings()


class TestAMessageSent:
    async def test_is_handled_and_deleted(self, client, queues):
        url, _ = queues
        queue = sqs.SqsQueue(client, url)
        handler = Recorder()
        working = _working(queue, handler)

        await queue.send("a message")
        await _until(lambda: len(handler.given) == 1 and _count(client, url) == 0, 10)
        await working.stop()

        assert handler.given == [("a message", False)]

    async def test_worked_longer_than_its_visibility_is_worked_once(
        self, client, queues
    ):
        """Its visibility is kept up while the handler works, so it is not
        delivered again meanwhile."""
        url, _ = queues
        queue = sqs.SqsQueue(client, url)
        handler = Recorder(seconds=3 * VISIBILITY_SECONDS)
        working = _working(queue, handler)

        await queue.send("a message")
        await _until(lambda: _count(client, url) == 0 and len(handler.given) == 1, 15)
        await working.stop()

        assert len(handler.given) == 1

    async def test_failing_every_time_is_dead_lettered_after_its_last_delivery(
        self, client, queues
    ):
        url, dead_letters = queues
        queue = sqs.SqsQueue(client, url)
        handler = Recorder(RuntimeError("database unavailable"))
        working = _working(queue, handler)

        await queue.send("a message")
        await _until(lambda: _count(client, dead_letters) == 1, 20)
        await working.stop()

        assert handler.given == [
            ("a message", False),
            ("a message", False),
            ("a message", True),
        ]
        assert _count(client, url) == 0


class TestAQueueWithAProblem:
    @pytest.fixture(autouse=True)
    def _a_short_pause_before_trying_again(self, monkeypatch):
        monkeypatch.setattr(consumer, "_RETRY_PAUSE_SECONDS", 0.1)

    async def test_without_a_dead_letter_queue_is_not_worked_until_it_has_one(
        self, client
    ):
        name = f"plain-{uuid.uuid4().hex[:12]}"
        url = client.create_queue(QueueName=name)["QueueUrl"]
        queue = sqs.SqsQueue(client, url)
        handler = Recorder()
        await queue.send("a message")
        working = _working(queue, handler)

        await _until(lambda: not working.healthy, 10)
        await asyncio.sleep(0.5)
        assert handler.given == []

        client.set_queue_attributes(
            QueueUrl=url, Attributes=_with_dead_letters(client, name)
        )
        await _until(lambda: working.healthy and _count(client, url) == 0, 15)
        await working.stop()

        assert handler.given == [("a message", False)]

    async def test_deleted_then_made_again_is_worked_again(self, client):
        name = f"conversions-{uuid.uuid4().hex[:12]}"
        attributes = _with_dead_letters(client, name)
        url = client.create_queue(QueueName=name, Attributes=attributes)["QueueUrl"]
        queue = sqs.SqsQueue(client, url)
        handler = Recorder()
        working = _working(queue, handler)

        client.delete_queue(QueueUrl=url)
        await _until(lambda: not working.healthy, 15)

        client.create_queue(QueueName=name, Attributes=attributes)
        await _until(lambda: working.healthy, 15)
        await queue.send("a message")
        await _until(lambda: _count(client, url) == 0 and len(handler.given) == 1, 15)
        await working.stop()
