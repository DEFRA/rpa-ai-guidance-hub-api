"""Find guides by title: the interface the rest of the hub calls.

An implementation in the hub, or one that asks the search service, gives it; the
contract tests (`tests/search/contract/`) say what each must do. A guide's title is
its latest version's.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.guidance import ids


@dataclasses.dataclass(frozen=True)
class GuideTitle:
    guide_id: ids.DocumentId
    title: str


class TitleSearch(Protocol):
    async def like(self, title: str, limit: int) -> tuple[GuideTitle, ...]:
        """The guides with titles like `title`, the likest first, `limit` at most."""
        ...
