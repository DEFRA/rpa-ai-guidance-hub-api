"""A queue in memory, as the consumer sees SQS: a message is received, which makes it
invisible, and is then deleted or kept invisible longer. Nothing expires on its own:
a test that wants a message delivered again says so (`expire`). A test that wants the
queue out of reach says that too (`down`)."""

from __future__ import annotations

import asyncio
import dataclasses
import itertools

from app.background import consumer


@dataclasses.dataclass
class _Message:
    body: str
    receipt: str
    receive_count: int = 0
    visible: bool = True


@dataclasses.dataclass
class FakeQueue:
    visibility_timeout: int = 120
    max_receive_count: int = 3
    down: bool = False
    messages: list[_Message] = dataclasses.field(default_factory=list)
    deleted: list[str] = dataclasses.field(default_factory=list)
    extended: list[tuple[str, int]] = dataclasses.field(default_factory=list)
    _refusing: list[str] = dataclasses.field(default_factory=list)
    _receipts: itertools.count = dataclasses.field(default_factory=itertools.count)

    def put(self, body: str) -> str:
        receipt = f"receipt-{next(self._receipts)}"
        self.messages.append(_Message(body, receipt))
        return receipt

    def expire(self, receipt: str) -> None:
        """The message's visibility timeout runs out: it can be received again."""
        self._message(receipt).visible = True

    def refuse_next(self, call: str) -> None:
        """The next `delete` or `extend` fails, on its own."""
        self._refusing.append(call)

    async def settings(self) -> consumer.QueueSettings:
        self._refuse_if_told("settings")
        return consumer.QueueSettings(self.visibility_timeout, self.max_receive_count)

    async def receive(
        self,
        wait_seconds: int,  # noqa: ARG002 - a long poll is a short sleep here
    ) -> consumer.Received | None:
        self._refuse_if_told("receive")
        for message in self.messages:
            if message.visible:
                message.visible = False
                message.receive_count += 1
                return consumer.Received(
                    message.receipt,
                    message.body,
                    message.receipt,
                    message.receive_count,
                )
        await asyncio.sleep(0.01)
        return None

    async def delete(self, receipt: str) -> None:
        self._refuse_if_told("delete")
        self.deleted.append(receipt)
        self.messages.remove(self._message(receipt))

    async def extend(self, receipt: str, seconds: int) -> None:
        self._refuse_if_told("extend")
        self.extended.append((receipt, seconds))

    def _refuse_if_told(self, call: str) -> None:
        if self.down or call in self._refusing:
            if call in self._refusing:
                self._refusing.remove(call)
            message = f"{call} refused"
            raise RuntimeError(message)

    def _message(self, receipt: str) -> _Message:
        return next(message for message in self.messages if message.receipt == receipt)
