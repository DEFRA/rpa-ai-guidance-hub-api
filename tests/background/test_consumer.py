"""The consumer, against a queue in memory: what becomes of a message by what its
handler does, how many are worked at once, what it does while the queue has a problem,
and how it stops."""

from __future__ import annotations

import asyncio
import logging
import time

import pytest

from app.background import consumer
from tests.fakes import queue as fake_queue


class RecordingHandler:
    """Does what it is told with each message, and records what it was given."""

    def __init__(self, outcome: Exception | None = None) -> None:
        self.outcome = outcome
        self.given: list[tuple[str, bool]] = []
        self.finished: list[str] = []
        self.holding: set[str] = set()
        self.released = asyncio.Event()

    async def __call__(self, body: str, *, last_delivery: bool) -> None:
        self.given.append((body, last_delivery))
        if body in self.holding:
            await self.released.wait()
        if self.outcome is not None:
            raise self.outcome
        self.finished.append(body)


async def _until(condition, timeout: float = 3.0) -> None:
    """Wait for the consumer to have done something."""
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


def _consuming(queue, handler, concurrency=2) -> consumer.Consumer:
    started = consumer.Consumer(queue, handler, concurrency=concurrency, wait_seconds=0)
    started.start()
    return started


class TestAMessage:
    async def test_is_given_to_the_handler_then_deleted_when_it_returns(self):
        queue = fake_queue.FakeQueue()
        handler = RecordingHandler()
        receipt = queue.put("a message")
        working = _consuming(queue, handler)

        await _until(lambda: queue.deleted == [receipt])
        await working.stop()

        assert handler.given == [("a message", False)]

    async def test_is_left_to_be_delivered_again_when_the_handler_raises(self, caplog):
        queue = fake_queue.FakeQueue()
        handler = RecordingHandler(RuntimeError("database unavailable"))
        receipt = queue.put("a message")
        working = _consuming(queue, handler)

        await _until(lambda: len(handler.given) == 1)
        await working.stop()

        assert queue.deleted == []
        assert receipt in [message.receipt for message in queue.messages]
        assert "database unavailable" in caplog.text

    async def test_tells_the_handler_only_on_its_last_delivery(self):
        """So that what is following the work is not left waiting, as SQS then moves
        the message to the dead-letter queue."""
        queue = fake_queue.FakeQueue()
        handler = RecordingHandler(RuntimeError("database unavailable"))
        receipt = queue.put("a message")
        working = _consuming(queue, handler)

        for delivery in (1, 2):
            await _until(lambda delivery=delivery: len(handler.given) == delivery)
            await asyncio.sleep(0.02)  # for the consumer to finish answering it
            queue.expire(receipt)
        await _until(lambda: len(handler.given) == 3)  # noqa: PLR2004
        await working.stop()

        assert [last_delivery for _, last_delivery in handler.given] == [
            False,
            False,
            True,
        ]

    async def test_that_could_not_be_deleted_does_not_stop_the_next_being_worked(
        self, caplog
    ):
        """It is delivered again, and the handler is safe to repeat."""
        queue = fake_queue.FakeQueue()
        handler = RecordingHandler()
        queue.refuse_next("delete")
        queue.put("a message")
        working = _consuming(queue, handler, concurrency=1)

        await _until(lambda: handler.finished == ["a message"])
        await asyncio.sleep(0.02)
        receipt = queue.put("the next")
        await _until(lambda: receipt in queue.deleted)
        await working.stop()

        assert "delete refused" in caplog.text


class TestWorkingMessages:
    async def test_no_more_than_the_concurrency_at_once(self):
        queue = fake_queue.FakeQueue()
        handler = RecordingHandler()
        handler.holding = {f"message {n}" for n in range(5)}
        for name in handler.holding:
            queue.put(name)
        working = _consuming(queue, handler, concurrency=2)

        await _until(lambda: len(handler.given) == 2)  # noqa: PLR2004
        await asyncio.sleep(0.05)
        assert len(handler.given) == 2  # noqa: PLR2004 - still only two

        handler.released.set()
        await _until(lambda: len(queue.deleted) == 5)  # noqa: PLR2004
        await working.stop()

    async def test_a_slow_message_does_not_hold_up_the_others(self):
        """The reason to run a loop per message, rather than work a batch: a batch
        is not followed by the next until its slowest message is done."""
        queue = fake_queue.FakeQueue()
        handler = RecordingHandler()
        handler.holding = {"a big guide"}
        queue.put("a big guide")
        quick = [queue.put(f"small guide {n}") for n in range(3)]
        working = _consuming(queue, handler, concurrency=2)

        await _until(lambda: queue.deleted == quick)
        assert "a big guide" not in handler.finished

        handler.released.set()
        await _until(lambda: len(queue.deleted) == 4)  # noqa: PLR2004
        await working.stop()

    async def test_keeps_a_message_invisible_only_while_its_handler_works(self):
        """Its visibility is extended every third of its timeout, so a long handler is
        not given the same message twice."""
        queue = fake_queue.FakeQueue(visibility_timeout=1)
        handler = RecordingHandler()
        handler.holding = {"a message"}
        receipt = queue.put("a message")
        working = _consuming(queue, handler)

        await _until(lambda: len(queue.extended) >= 2)  # noqa: PLR2004
        handler.released.set()
        await _until(lambda: queue.deleted == [receipt])
        extended = len(queue.extended)
        await asyncio.sleep(0.6)
        await working.stop()

        assert set(queue.extended) == {(receipt, 1)}
        assert len(queue.extended) == extended

    async def test_tries_a_failed_extension_again_before_the_visibility_runs_out(self):
        """Otherwise one failed call could give a message still being worked to another
        consumer, and spend one of its deliveries."""
        queue = fake_queue.FakeQueue(visibility_timeout=1)
        handler = RecordingHandler()
        handler.holding = {"a message"}
        queue.refuse_next("extend")
        queue.put("a message")
        working = _consuming(queue, handler)
        await _until(lambda: handler.given)
        received = time.monotonic()

        await _until(lambda: queue.extended)
        extended_after = time.monotonic() - received
        handler.released.set()
        await working.stop()

        assert extended_after < queue.visibility_timeout

    async def test_keeps_a_message_invisible_past_a_failure_to_extend_it(self, caplog):
        queue = fake_queue.FakeQueue(visibility_timeout=1)
        handler = RecordingHandler()
        handler.holding = {"a message"}
        queue.refuse_next("extend")
        queue.put("a message")
        working = _consuming(queue, handler)

        await _until(lambda: len(queue.extended) == 1)
        handler.released.set()
        await working.stop()

        assert "extend refused" in caplog.text


class TestAQueueWithAProblem:
    @pytest.fixture(autouse=True)
    def _no_pause_before_trying_again(self, monkeypatch):
        monkeypatch.setattr(consumer, "_RETRY_PAUSE_SECONDS", 0)

    def test_counts_as_healthy_until_found_otherwise(self):
        """An idle queue's first long poll takes 20 seconds to answer, and /health
        must not fail while it does."""
        assert consumer.Consumer(
            fake_queue.FakeQueue(), RecordingHandler(), concurrency=1
        ).healthy

    async def test_is_reported_until_it_is_over_and_its_messages_then_worked(self):
        queue = fake_queue.FakeQueue(down=True)
        handler = RecordingHandler()
        working = _consuming(queue, handler)

        await _until(lambda: not working.healthy)
        receipt = queue.put("a message")
        await asyncio.sleep(0.02)
        assert handler.given == []

        queue.down = False
        await _until(lambda: working.healthy and queue.deleted == [receipt])
        await working.stop()

    @pytest.mark.parametrize("call", ["settings", "receive"])
    async def test_is_found_by_either_of_the_calls_that_poll(self, call):
        queue = fake_queue.FakeQueue()
        handler = RecordingHandler()
        working = _consuming(queue, handler, concurrency=1)

        queue.refuse_next(call)
        await _until(lambda: not working.healthy)
        await _until(lambda: working.healthy)
        await working.stop()

    async def test_does_not_keep_the_consumer_from_stopping(self):
        queue = fake_queue.FakeQueue(down=True)
        working = _consuming(queue, RecordingHandler())
        await _until(lambda: not working.healthy)

        async with asyncio.timeout(1):
            await working.stop()

    async def test_is_logged_once_when_it_starts_and_once_when_it_ends(self, caplog):
        """Not at every attempt, by every loop, for as long as it lasts."""
        caplog.set_level(logging.INFO, logger="app.background.consumer")
        queue = fake_queue.FakeQueue(down=True)
        working = _consuming(queue, RecordingHandler())

        await _until(lambda: not working.healthy)
        await asyncio.sleep(0.05)
        queue.down = False
        await _until(lambda: working.healthy)
        await working.stop()

        assert caplog.text.count("cannot be worked") == 1
        assert caplog.text.count("can be worked again") == 1
        assert "settings refused" in caplog.text


class TestStopping:
    async def test_lets_a_message_being_worked_finish(self):
        queue = fake_queue.FakeQueue()
        handler = RecordingHandler()
        handler.holding = {"a message"}
        receipt = queue.put("a message")
        working = _consuming(queue, handler)
        await _until(lambda: len(handler.given) == 1)

        stopping = asyncio.create_task(working.stop())
        await asyncio.sleep(0.05)
        assert not stopping.done()
        handler.released.set()
        await stopping

        assert queue.deleted == [receipt]

    async def test_receives_nothing_more(self):
        queue = fake_queue.FakeQueue()
        handler = RecordingHandler()
        working = _consuming(queue, handler)
        await working.stop()

        queue.put("a message")
        await asyncio.sleep(0.05)

        assert handler.given == []


@pytest.mark.parametrize(
    ("receive_count", "last_delivery"), [(2, False), (3, True), (4, True)]
)
def test_a_delivery_is_the_last_from_the_queues_max_receive_count(
    receive_count, last_delivery
):
    """The count is approximate, so a message past the max is on its last too."""
    message = consumer.Received("id", "body", "receipt", receive_count)
    settings = consumer.QueueSettings(visibility_timeout=120, max_receive_count=3)

    assert settings.is_last_delivery(message) is last_delivery
