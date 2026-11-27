"""Giving the search service every guide again, from a real MongoDB, to a fake search
index.

All fixture text is invented.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from app.admin import rebuild
from app.guidance import ids, models, records
from app.search import search_service
from tests.fakes import search_index

if TYPE_CHECKING:
    from collections.abc import Mapping

BEGAN = dt.datetime(2026, 11, 1, 9, 0, tzinfo=dt.UTC)


@pytest.fixture
async def database(mongo_database):
    """The database is shared by the session, and a rebuild reads all of it."""
    await records.ensure_indexes(mongo_database)
    await mongo_database[records.DOCUMENTS].delete_many({})
    await mongo_database[records.VERSIONS].delete_many({})
    return mongo_database


class _Index(search_index.InMemorySearchIndex):
    """An index that can fail a guide a number of times, takes a moment over each, and
    sees how many it is given at once."""

    def __init__(self) -> None:
        super().__init__()
        self.failures_of: dict[ids.DocumentId, list[Exception]] = {}
        self.calls: list[ids.DocumentId] = []
        self.resets = 0
        self._at_once = 0
        self.most_at_once = 0

    async def index(self, version: models.Version, metadata: Mapping[str, Any]) -> None:
        self.calls.append(version.document_id)
        self._at_once += 1
        self.most_at_once = max(self.most_at_once, self._at_once)
        try:
            await asyncio.sleep(0.005)
            failures = self.failures_of.get(version.document_id)
            if failures:
                raise failures.pop(0)
            await super().index(version, metadata)
        finally:
            self._at_once -= 1

    async def reset(self) -> None:
        self.resets += 1


async def _guide(
    database,
    *,
    details: dict[str, Any] | None = None,
    versions: int = 1,
    version_details: dict[str, Any] | None = None,
) -> tuple[models.Document, list[models.Version]]:
    """A document with `versions` versions, oldest first."""
    document = await records.create(
        database,
        models.Document(
            id=ids.new_document_id(),
            upload_id=str(uuid.uuid4()),
            file_id=str(uuid.uuid4()),
            metadata=details,
            created_at=BEGAN,
        ),
    )
    made = [
        models.Version(
            id=ids.new_version_id(),
            document_id=document.id,
            content_url=f"s3://docs/{document.id}/{number}/content.md",
            created_at=BEGAN + dt.timedelta(minutes=number),
            title=f"Guide {number}",
            metadata=version_details,
        )
        for number in range(versions)
    ]
    for version in made:
        await records.create_version(database, version)
    return document, made


async def _rebuilt(database, index, **options: Any) -> None:
    await rebuild.rebuild(database, index, "user-1", retry_after=0, **options)


class TestGivingEveryGuideAgain:
    async def test_each_documents_newest_version_is_sent_once(self, database):
        index = _Index()
        _, [_old, newest] = await _guide(database, versions=2)
        _, [only] = await _guide(database)

        await _rebuilt(database, index)

        assert sorted(version.id for version, _ in index.indexed) == sorted(
            [newest.id, only.id]
        )

    async def test_a_document_with_no_version_is_not_sent(self, database):
        index = _Index()
        await _guide(database, versions=0)
        _, [sent] = await _guide(database)

        await _rebuilt(database, index)

        assert [version.id for version, _ in index.indexed] == [sent.id]

    async def test_sends_nothing_when_there_are_no_guides(self, database, caplog):
        index = _Index()

        with caplog.at_level(logging.WARNING, logger="app.admin.rebuild"):
            await _rebuilt(database, index)

        assert index.calls == []
        assert "0 guides sent, 0 failed" in caplog.text
        assert "user-1" in caplog.text

    async def test_deletes_nothing_first(self, database):
        index = _Index()
        await _guide(database)

        await _rebuilt(database, index)

        assert index.resets == 0


class TestTheDetailsSent:
    async def test_a_version_gives_its_own_details_as_a_save_does(self, database):
        index = _Index()
        document, [version] = await _guide(
            database,
            details={"schemes": ["of the document"]},
            version_details={"schemes": ["SFI"]},
        )

        await _rebuilt(database, index)

        [(sent, details)] = index.indexed
        assert sent.id == version.id
        assert details == {"schemeYear": 2026, "schemes": ["SFI"]}
        assert details == models.search_details(document, version)

    async def test_a_version_saved_before_versions_kept_their_own_gets_its_documents(
        self, database
    ):
        index = _Index()
        document, [version] = await _guide(
            database, details={"schemes": [models.NOT_SCHEME_SPECIFIC], "owners": ["A"]}
        )

        await _rebuilt(database, index)

        [(sent, details)] = index.indexed
        assert details == {"schemeYear": 2026, "schemes": [], "owners": ["A"]}
        assert details == models.search_details(document, sent)
        assert sent.id == version.id


class TestWhenAVersionIsNotGiven:
    async def test_a_refused_version_is_logged_and_the_rest_are_sent(
        self, database, caplog
    ):
        index = _Index()
        refused, [refused_version] = await _guide(database)
        _, [other] = await _guide(database)
        index.failures_of[refused.id] = [search_service.IndexRefusedError("422 bad")]

        with caplog.at_level(logging.WARNING, logger="app.admin.rebuild"):
            await _rebuilt(database, index)

        assert [version.id for version, _ in index.indexed] == [other.id]
        assert index.calls.count(refused.id) == 1  # not tried again
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert str(refused.id) in errors[0].getMessage()
        assert str(refused_version.id) in errors[0].getMessage()
        assert "1 guides sent, 1 failed" in caplog.text

    async def test_a_version_given_up_on_is_tried_again_and_then_logged(
        self, database, caplog
    ):
        index = _Index()
        failing, _ = await _guide(database)
        _, [other] = await _guide(database)
        index.failures_of[failing.id] = [
            search_service.SearchServiceError("503") for _ in range(10)
        ]

        with caplog.at_level(logging.WARNING, logger="app.admin.rebuild"):
            await _rebuilt(database, index)

        assert index.calls.count(failing.id) == 3  # noqa: PLR2004 - once and twice more
        assert [version.id for version, _ in index.indexed] == [other.id]
        assert [r.levelno for r in caplog.records].count(logging.ERROR) == 1
        assert "1 guides sent, 1 failed" in caplog.text

    async def test_a_version_that_fails_and_is_then_given_counts_as_sent(
        self, database, caplog
    ):
        index = _Index()
        flaky, [version] = await _guide(database)
        index.failures_of[flaky.id] = [search_service.SearchServiceError("503")]

        with caplog.at_level(logging.WARNING, logger="app.admin.rebuild"):
            await _rebuilt(database, index)

        assert [sent.id for sent, _ in index.indexed] == [version.id]
        assert index.calls.count(flaky.id) == 2  # noqa: PLR2004
        assert "1 guides sent, 0 failed" in caplog.text

    async def test_never_raises_when_every_send_fails(self, database, caplog):
        index = _Index()
        index.failure = RuntimeError("down")
        for _ in range(3):
            await _guide(database)

        with caplog.at_level(logging.WARNING, logger="app.admin.rebuild"):
            await _rebuilt(database, index)

        assert index.indexed == []
        assert "0 guides sent, 3 failed" in caplog.text

    async def test_never_raises_when_the_guides_cannot_be_read(
        self, database, mocker, caplog
    ):
        index = _Index()
        mocker.patch.object(
            records, "every_latest_version", side_effect=RuntimeError("no mongo")
        )

        with caplog.at_level(logging.ERROR, logger="app.admin.rebuild"):
            await _rebuilt(database, index)

        assert index.calls == []
        assert "could not read guides" in caplog.text


class TestHowManyAtOnce:
    async def test_never_more_than_the_limit(self, database):
        index = _Index()
        for _ in range(8):
            await _guide(database)

        await _rebuilt(database, index, concurrency=3)

        assert len(index.indexed) == 8  # noqa: PLR2004
        assert index.most_at_once == 3  # noqa: PLR2004

    async def test_one_at_a_time_when_the_limit_is_one(self, database):
        index = _Index()
        for _ in range(4):
            await _guide(database)

        await _rebuilt(database, index, concurrency=1)

        assert index.most_at_once == 1


class TestCancelling:
    async def test_a_cancelled_rebuild_is_cancelled(self, database):
        """Shutdown cancels it: that is not a failure to swallow."""
        index = _Index()
        await _guide(database)
        task = asyncio.create_task(_rebuilt(database, index))
        await asyncio.sleep(0)

        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task
