"""A search index that sends nothing anywhere: it keeps each version it is given, with
its details, or raises the failure it is told to."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.guidance import models


class InMemorySearchIndex:
    def __init__(self) -> None:
        self.indexed: list[tuple[models.Version, dict[str, Any]]] = []
        self.failure: Exception | None = None

    async def index(self, version: models.Version, metadata: Mapping[str, Any]) -> None:
        if self.failure is not None:
            raise self.failure
        self.indexed.append((version, dict(metadata)))
