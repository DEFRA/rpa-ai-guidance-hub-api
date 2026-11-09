"""Working messages off a queue, in the API's own process, as
[bbc/sqs-consumer](https://github.com/bbc/sqs-consumer) does. What a message holds,
and what a failure means, are for the handler to know.

When the handler returns, the message is deleted. When it raises, the message is left
for SQS to deliver again. The handler must be safe to repeat, as SQS may deliver a
message more than once anyway. While it works, the message is kept invisible, so no
other consumer takes it.

The handler is told when a delivery is the last: `last_delivery` is true once the
message's receive count reaches the queue's `maxReceiveCount`. If the handler raises
then, SQS moves the message to the dead-letter queue, so this is its last chance to
record the failure.

`concurrency` loops each take one message, not a batch, so a slow message holds up no
others. A queue that cannot be worked does not stop the API: the loops keep trying,
and `healthy` says so meanwhile.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Protocol

logger = logging.getLogger(__name__)

# How long to wait before polling again when the queue cannot be worked.
_RETRY_PAUSE_SECONDS = 5


@dataclasses.dataclass(frozen=True)
class Received:
    """A message as received: its id, its body, the receipt it is answered by, and how
    many times it has been delivered, this time included."""

    id: str
    body: str
    receipt: str
    receive_count: int


@dataclasses.dataclass(frozen=True)
class QueueSettings:
    """The queue's own settings, read from it, so the consumer always agrees with the
    queue."""

    visibility_timeout: int
    max_receive_count: int

    def is_last_delivery(self, message: Received) -> bool:
        return message.receive_count >= self.max_receive_count


class Queue(Protocol):
    async def settings(self) -> QueueSettings:
        """The queue's settings, raising for a queue that cannot be worked as it is."""

    async def receive(self, wait_seconds: int) -> Received | None:
        """The next message, or None if none arrives within the wait."""

    async def delete(self, receipt: str) -> None: ...

    async def extend(self, receipt: str, seconds: int) -> None: ...


class Handler(Protocol):
    async def __call__(self, body: str, *, last_delivery: bool) -> None:
        """Do the work: safe to repeat. Returning finishes with the message, and
        raising leaves it to be delivered again, unless this is its last delivery."""


class Consumer:
    """Receives and works messages from `start` until `stop`: see the module docstring."""

    def __init__(
        self,
        queue: Queue,
        handler: Handler,
        *,
        concurrency: int,
        wait_seconds: int = 20,
    ) -> None:
        self._queue = queue
        self._handler = handler
        self._concurrency = concurrency
        self._wait_seconds = wait_seconds
        self._healthy = True
        self._stopped = False
        self._polling: list[asyncio.Task[None]] = []

    @property
    def healthy(self) -> bool:
        """False from a failed poll until the next that succeeds. True until found
        otherwise, as an idle queue takes a whole long poll to answer."""
        return self._healthy

    def start(self) -> None:
        self._polling = [
            asyncio.create_task(self._poll()) for _ in range(self._concurrency)
        ]

    async def stop(self) -> None:
        """Receive nothing more, and wait for each loop to finish what it is doing:
        a poll under way, and the work on any message it answers with."""
        self._stopped = True
        await asyncio.gather(*self._polling)

    async def _poll(self) -> None:
        while not self._stopped:
            try:
                settings = await self._queue.settings()
                message = await self._queue.receive(self._wait_seconds)
            except Exception:
                self._found_unhealthy()
                await asyncio.sleep(_RETRY_PAUSE_SECONDS)
                continue
            self._found_healthy()
            if message is not None:
                await self._work(message, settings)

    def _found_unhealthy(self) -> None:
        """Logged only as it starts: it may go on for hours, and every loop finds it."""
        if self._healthy:
            logger.exception("The queue cannot be worked; trying again until it can")
        self._healthy = False

    def _found_healthy(self) -> None:
        if not self._healthy:
            logger.info("The queue can be worked again")
        self._healthy = True

    async def _work(self, message: Received, settings: QueueSettings) -> None:
        try:
            await self._handle(message, settings)
            await self._queue.delete(message.receipt)
        except Exception:
            logger.exception(
                "Message %s not finished on delivery %d of %d: left for SQS",
                message.id,
                message.receive_count,
                settings.max_receive_count,
            )

    async def _handle(self, message: Received, settings: QueueSettings) -> None:
        keeping_invisible = asyncio.create_task(
            self._keep_invisible(message, settings.visibility_timeout)
        )
        try:
            await self._handler(
                message.body,
                last_delivery=settings.is_last_delivery(message),
            )
        finally:
            keeping_invisible.cancel()

    async def _keep_invisible(self, message: Received, visibility_seconds: int) -> None:
        """Until cancelled. A failure to extend is logged and tried again next time,
        as there may still be time left on the message's visibility."""
        while True:
            await asyncio.sleep(visibility_seconds / 3)
            try:
                await self._queue.extend(message.receipt, visibility_seconds)
            except Exception:
                logger.exception("Could not keep message %s invisible", message.id)
