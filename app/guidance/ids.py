"""The ids of what the guidance service stores, and of what it is handed.

Each kind of id has a type of its own, so the type checker refuses one where another
belongs: a document id and a version id travel together everywhere, and swapping them
would otherwise go unnoticed. A `NewType` costs nothing at run time - `DocumentId(x)`
is `x` - and Pydantic validates one as the type beneath it.

**Ours** are uuids, version 7: ordered by when they were minted, so the newest sorts
last in Mongo's `_id` index and in a listing of the bucket. Nothing reads meaning into
that order; `createdAt` is what says which version is newest. They are stored in Mongo
as native UUIDs, and become text only as an S3 prefix or on the wire.

**Other services'** are taken as they are given. cdp-uploader names an upload and a
file, and says nothing about what form those names take, so they are `str` - named,
but not claimed to be uuids.
"""

from __future__ import annotations

import uuid
from typing import NewType

DocumentId = NewType("DocumentId", uuid.UUID)
VersionId = NewType("VersionId", uuid.UUID)

UploadId = NewType("UploadId", str)
FileId = NewType("FileId", str)


def new_document_id() -> DocumentId:
    return DocumentId(uuid.uuid7())


def new_version_id() -> VersionId:
    return VersionId(uuid.uuid7())
