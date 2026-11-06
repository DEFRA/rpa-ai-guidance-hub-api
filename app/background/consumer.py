"""Working messages off a queue, in the API's own process beside its requests.

[sqs-consumer](https://github.com/sfitz42/sqs-consumer) does the work: it long-polls
the queue and gives a message to a handler, deleting it if the handler returns and
leaving it if the handler raises, so SQS delivers it again after its visibility timeout
until the queue's redrive policy moves it to the dead-letter queue. While the handler
works it keeps the message invisible, and stopping lets the work in progress finish, as
far as the platform waits for it.

So what a failure means is for the handler to decide: it returns from one that retrying
cannot fix, and it is told when a delivery is the last, to record a failure it would
otherwise leave nobody knowing about. SQS may deliver a message more than once anyway,
so a handler must be safe to repeat.

What is here is what the API asks of it beyond that:

- **One message at a time, `concurrency` times over.** A consumer waits for a whole batch
  before polling again, and a long poll can answer with a large guide among small ones,
  so one consumer per message means one large guide holds up nothing else.
- **The queue's settings are read from it,** at every poll, so that a queue put right
  differently is worked as it now is, and a message is never received from a queue that
  could not dead-letter it. How long to keep a message invisible is read once, on starting.
- **A queue that cannot be worked does not stop the API,** which has plenty to do without it.
  It keeps trying, `healthy` says so meanwhile, and the work carries on once the queue is
  put right.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from typing import TYPE_CHECKING, Any, Protocol

import sqs_consumer

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

logger = logging.getLogger(__name__)

# How long to wait before polling again when the queue cannot be worked.
_RETRY_PAUSE_SECONDS = 5

_RECEIVE_COUNT = "ApproximateReceiveCount"

# A consumer's calls never overlap but for its heartbeat, and it polls only between
# messages, so it never needs more than these.
_THREADS_PER_CONSUMER = 2


@dataclasses.dataclass(frozen=True)
class QueueSettings:
    """The queue's own settings, read from it, so the consumer always agrees with the
    queue."""

    visibility_timeout: int
    max_receive_count: int

    def is_last_delivery(self, receive_count: int) -> bool:
        return receive_count >= self.max_receive_count


class Queue(Protocol):
    client: Any
    url: str

    async def settings(self) -> QueueSettings:
        """The queue's settings, raising for a queue that cannot be worked as it is."""

    def read_settings(self) -> QueueSettings:
        """The same, on the calling thread."""


class Handler(Protocol):
    async def __call__(self, body: str, *, last_delivery: bool) -> None:
        """Do the work: safe to repeat. Returning finishes with the message, and
        raising leaves it to be delivered again, unless this is its last delivery."""


class _PollingClient:
    """The queue's client, polling as `Consumer.polled` does, for the library to use."""

    def __init__(self, polling: Consumer) -> None:
        self._polling = polling
        self._client = polling.queue.client

    def receive_message(self, **poll: Any) -> Mapping[str, Any]:
        return self._polling.polled(poll)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


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
        self.queue = queue
        self._handler = handler
        self._concurrency = concurrency
        self._wait_seconds = wait_seconds
        self._healthy = True
        # Read before anything is received, by `_start_consumers`.
        self._settings = QueueSettings(visibility_timeout=0, max_receive_count=0)
        self._starting: asyncio.Task[None] | None = None
        self._consumers: list[sqs_consumer.Consumer[Any]] = []

    @property
    def healthy(self) -> bool:
        """False from a failed poll until the next that succeeds. True until found
        otherwise, as an idle queue takes a whole long poll to answer."""
        return self._healthy

    def start(self) -> None:
        self._starting = asyncio.create_task(self._start_consumers())

    async def stop(self) -> None:
        """Receive nothing more, and wait for each consumer to finish what it is doing:
        a poll under way, and the work on any message it answers with."""
        if self._starting is not None:
            self._starting.cancel()
            await asyncio.gather(self._starting, return_exceptions=True)
        await asyncio.gather(*(consumer.stop() for consumer in self._consumers))

    def polled(self, poll: Mapping[str, Any]) -> Mapping[str, Any]:
        """The answer to one poll, on a consumer's thread: none, after a pause, when the
        queue cannot be worked, so that an outage is waited out and logged only as it
        starts, as it may go on for hours and every consumer finds it."""
        try:
            self._settings = self.queue.read_settings()
            answer: Mapping[str, Any] = self.queue.client.receive_message(**poll)
        except Exception:
            self._found_unhealthy()
            time.sleep(_RETRY_PAUSE_SECONDS)
            return {}
        self._found_healthy()
        return answer

    async def _start_consumers(self) -> None:
        settings = await self._settings_once_readable()
        self._settings = settings
        self._consumers = [self._consumer(settings) for _ in range(self._concurrency)]
        for consumer in self._consumers:
            await consumer.start()

    async def _settings_once_readable(self) -> QueueSettings:
        while True:
            try:
                settings = await self.queue.settings()
            except Exception:
                self._found_unhealthy()
                await asyncio.sleep(_RETRY_PAUSE_SECONDS)
            else:
                self._found_healthy()
                return settings

    def _consumer(self, settings: QueueSettings) -> sqs_consumer.Consumer[Any]:
        visibility = settings.visibility_timeout
        return sqs_consumer.Consumer(
            self.queue.url,
            self._handle,
            client=_PollingClient(self),  # type: ignore[arg-type]
            receive=sqs_consumer.Receive(
                wait_time_seconds=self._wait_seconds,
                max_messages=1,
                attribute_names=[_RECEIVE_COUNT],
            ),
            # Every third of its timeout, so that a failed extension can be tried again
            # before the visibility runs out.
            heartbeat=sqs_consumer.Heartbeat(visibility / 3, visibility),
            hooks=sqs_consumer.Hooks(on_processing_error=self._log_failure),
            io_threads=_THREADS_PER_CONSUMER,
        )

    async def _handle(self, message: sqs_consumer.Message[Any]) -> None:
        receive_count = int(message.attributes[_RECEIVE_COUNT])
        await self._handler(
            message.body,
            last_delivery=self._settings.is_last_delivery(receive_count),
        )

    async def _log_failure(
        self, failure: Exception, messages: Sequence[sqs_consumer.Message[Any]]
    ) -> None:
        for message in messages:
            logger.error(
                "Message %s not finished on delivery %s of %d: left for SQS",
                message.message_id,
                message.attributes[_RECEIVE_COUNT],
                self._settings.max_receive_count,
                exc_info=failure,
            )

    def _found_unhealthy(self) -> None:
        """Called while handling what was found."""
        if self._healthy:
            logger.exception("The queue cannot be worked; trying again until it can")
        self._healthy = False

    def _found_healthy(self) -> None:
        if not self._healthy:
            logger.info("The queue can be worked again")
        self._healthy = True
