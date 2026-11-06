"""What of the API's queue in SQS needs no queue: how it is reached."""

from __future__ import annotations

import threading
from unittest import mock

from app import config as app_config
from app.background import sqs


async def test_a_long_poll_waits_on_a_thread_of_its_own():
    """An idle poll holds its thread for 20 seconds, so a loop per message must not
    take up the threads the API's other work shares (`asyncio.to_thread`'s)."""
    polled_on: list[str] = []

    def _poll(**_kwargs):
        polled_on.append(threading.current_thread().name)
        return {}

    client = mock.Mock(receive_message=_poll)

    answer = await sqs.SqsQueue(client, "url", polling_threads=1).receive(20)

    assert answer is None
    assert polled_on[0].startswith("sqs-poll")


def test_the_client_waits_longer_for_an_answer_than_a_long_poll_lasts():
    config = mock.Mock(floci_endpoint_url=None, aws_region="eu-west-2")

    with mock.patch.object(app_config, "get_config", return_value=config):
        client = sqs.client()

    assert client.meta.config.read_timeout > 20  # noqa: PLR2004 - SQS's longest poll
