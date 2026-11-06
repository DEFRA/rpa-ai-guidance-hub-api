"""A submitter that sends nothing anywhere: it keeps each job, and gives it at once to
its handler as the consumer would, so a test sees the work done when the request is
answered. What the handler raises is kept, as SQS would keep the message to deliver
again."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.background import consumer
    from app.guidance import conversion


class InMemorySubmitter:
    def __init__(self, handler: consumer.Handler | None = None) -> None:
        self.handler = handler
        self.sent: list[str] = []
        self.left: list[Exception] = []
        self.refusal: Exception | None = None

    async def submit(self, job: conversion.ConversionJob) -> None:
        if self.refusal is not None:
            raise self.refusal
        body = job.model_dump_json(by_alias=True)
        self.sent.append(body)
        if self.handler is not None:
            try:
                await self.handler(body, last_delivery=False)
            except Exception as failure:  # noqa: BLE001 - kept, as SQS would keep it
                self.left.append(failure)
