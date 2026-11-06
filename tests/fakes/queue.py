"""A queue in memory, answering as boto3's SQS client does, so that the real consumer
runs against it, from its own threads: a message is received, which makes it invisible,
and is then deleted or kept invisible longer. Nothing expires on its own: a test that
wants a message delivered again says so (`expire`). A test that wants the queue out of
reach says that too (`down`)."""

from __future__ import annotations

import dataclasses
import itertools
import json
import threading
import time
from typing import Any


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
    has_dead_letter_queue: bool = True
    messages: list[_Message] = dataclasses.field(default_factory=list)
    deleted: list[str] = dataclasses.field(default_factory=list)
    extended: list[tuple[str, int]] = dataclasses.field(default_factory=list)
    polled_on: list[str] = dataclasses.field(default_factory=list)
    _refusing: list[str] = dataclasses.field(default_factory=list)
    _receipts: itertools.count = dataclasses.field(default_factory=itertools.count)
    _lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)

    def put(self, body: str) -> str:
        receipt = f"receipt-{next(self._receipts)}"
        self.messages.append(_Message(body, receipt))
        return receipt

    def expire(self, receipt: str) -> None:
        """The message's visibility timeout runs out: it can be received again."""
        self._message(receipt).visible = True

    def refuse_next(self, call: str) -> None:
        """The next `settings`, `receive`, `delete` or `extend` fails, on its own."""
        self._refusing.append(call)

    def get_queue_attributes(self, **_call: Any) -> dict[str, Any]:
        self._refuse_if_told("settings")
        attributes = {"VisibilityTimeout": str(self.visibility_timeout)}
        if self.has_dead_letter_queue:
            attributes["RedrivePolicy"] = json.dumps(
                {"maxReceiveCount": str(self.max_receive_count)}
            )
        return {"Attributes": attributes}

    def receive_message(self, **call: Any) -> dict[str, Any]:
        self.polled_on.append(threading.current_thread().name)
        self._refuse_if_told("receive")
        with self._lock:
            visible = [message for message in self.messages if message.visible]
            found = visible[: call["MaxNumberOfMessages"]]
            for message in found:
                message.visible = False
                message.receive_count += 1
        if found:
            return {"Messages": [_as_received(message) for message in found]}
        time.sleep(0.01)  # a long poll is a short sleep here
        return {}

    def delete_message_batch(self, **call: Any) -> dict[str, Any]:
        self._refuse_if_told("delete")
        for entry in call["Entries"]:
            self.deleted.append(entry["ReceiptHandle"])
            self.messages.remove(self._message(entry["ReceiptHandle"]))
        return {"Failed": []}

    def change_message_visibility_batch(self, **call: Any) -> dict[str, Any]:
        self._refuse_if_told("extend")
        for entry in call["Entries"]:
            self.extended.append((entry["ReceiptHandle"], entry["VisibilityTimeout"]))
        return {"Failed": []}

    def _refuse_if_told(self, call: str) -> None:
        if self.down or call in self._refusing:
            if call in self._refusing:
                self._refusing.remove(call)
            message = f"{call} refused"
            raise RuntimeError(message)

    def _message(self, receipt: str) -> _Message:
        return next(message for message in self.messages if message.receipt == receipt)


def _as_received(message: _Message) -> dict[str, Any]:
    return {
        "MessageId": f"id-of-{message.receipt}",
        "Body": message.body,
        "ReceiptHandle": message.receipt,
        "Attributes": {"ApproximateReceiveCount": str(message.receive_count)},
    }
