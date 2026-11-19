"""The search service, as the contract tests run against it.

They run against it only when SEARCH_SERVICE_FOR_CONTRACT_TESTS gives the address of
one that is running, as the local stack's is at http://localhost:8086. Each test
publishes its guides there, waits until the service can search them, and deletes them
after. A test the service cannot pass yet is an expected failure there, with the
reason: that list is what the service must do before a search moves to it.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
from typing import Any, ClassVar, Protocol

import httpx2
import pytest

from app.common import http_client
from app.guidance import ids

ADDRESS = "SEARCH_SERVICE_FOR_CONTRACT_TESTS"
SECONDS_TO_WAIT = 30
SECONDS_BETWEEN_TRIES = 0.25


class Implementation(Protocol):
    # The tests it cannot pass yet, by name, each with the reason.
    expected_failures: ClassVar[dict[str, str]]


def param(implementation: type[Implementation]) -> Any:
    """The search service's implementation, as a fixture's param: skipped without an
    address."""
    return pytest.param(
        implementation,
        marks=pytest.mark.skipif(
            ADDRESS not in os.environ,
            reason=f"{ADDRESS} does not give a search service",
        ),
        id="in the search service",
    )


def expect_failures(
    request: pytest.FixtureRequest, implementation: type[Implementation]
) -> None:
    """Marks the test an expected failure where the implementation lists it."""
    reason = implementation.expected_failures.get(request.function.__name__)
    if reason is not None:
        request.applymarker(pytest.mark.xfail(reason=reason, strict=True))


class Published:
    """The guides a test publishes to the search service."""

    def __init__(self) -> None:
        self.address = os.environ[ADDRESS]
        self.versions: dict[ids.DocumentId, ids.VersionId] = {}

    async def guide(self, markdown: str, title: str) -> ids.DocumentId:
        guide_id = ids.new_document_id()
        version_id = ids.new_version_id()
        async with http_client.create_async_client() as client:
            response = await client.put(
                f"{self.address}/documents/{guide_id}/versions/{version_id}",
                json={
                    "sourceType": "guide",
                    "status": "published",
                    "occurredAt": dt.datetime.now(dt.UTC).isoformat(),
                    "content": {"markdown": markdown},
                    "metadata": {"guideTitle": title},
                },
            )
        response.raise_for_status()
        self.versions[guide_id] = version_id
        return guide_id

    async def until_searchable(self) -> None:
        """Waits until the service serves an index with every guide published."""
        try:
            async with (
                asyncio.timeout(SECONDS_TO_WAIT),
                http_client.create_async_client() as client,
            ):
                for guide_id in self.versions:
                    while not await _is_indexed(client, self.address, guide_id):
                        await asyncio.sleep(SECONDS_BETWEEN_TRIES)
        except TimeoutError:
            pytest.fail(
                f"The search service did not serve the guides in {SECONDS_TO_WAIT} seconds"
            )

    async def deleted(self) -> None:
        async with http_client.create_async_client() as client:
            for guide_id, version_id in self.versions.items():
                response = await client.delete(
                    f"{self.address}/documents/{guide_id}/versions/{version_id}"
                )
                response.raise_for_status()


async def _is_indexed(
    client: httpx2.AsyncClient, address: str, guide_id: ids.DocumentId
) -> bool:
    response = await client.get(
        f"{address}/v1/documents/{guide_id}/matches", params={"q": "any"}
    )
    return response.status_code != httpx2.codes.NOT_FOUND
