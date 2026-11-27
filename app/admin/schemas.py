"""The shapes the admin's search index routes take over HTTP. Pydantic, written into
the OpenAPI document, and spelt in camelCase on the wire.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import pydantic

if TYPE_CHECKING:
    from app.search import search_service


class IndexDeleted(pydantic.BaseModel):
    """What the search service deleted: the objects in its index's storage, and the
    records that track what has been applied to it."""

    deleted_objects: int = pydantic.Field(alias="deletedObjects")
    deleted_state_records: int = pydantic.Field(alias="deletedStateRecords")

    model_config = pydantic.ConfigDict(populate_by_name=True)

    @classmethod
    def from_model(cls, result: search_service.ResetResult) -> IndexDeleted:
        return cls.model_validate(
            {
                "deletedObjects": result.deleted_objects,
                "deletedStateRecords": result.deleted_state_records,
            }
        )


class RebuildRequest(pydantic.BaseModel):
    """Who asked for the rebuild: the hub user the UI vouches for. It is for the log
    only."""

    requested_by: str = pydantic.Field(
        alias="requestedBy", min_length=1, max_length=200
    )

    model_config = pydantic.ConfigDict(extra="forbid", populate_by_name=True)


class RebuildStarted(pydantic.BaseModel):
    """The rebuild has been started, not finished: how it went is in the log."""

    status: Literal["started"] = "started"
