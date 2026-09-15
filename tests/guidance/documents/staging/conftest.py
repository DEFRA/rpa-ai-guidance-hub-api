"""A fake `store.StagingStore`, so `service.StagingService` (and `router.py`, via
`app.dependency_overrides`) can be tested without a real Mongo connection.

Keeps the same PENDING -> IN_PROGRESS -> {COMPLETE,FAILED} invariant `claim`
promises against the real store: only the first `claim` for a given file_id
returns True, which is what a test exercising AC8 needs to be able to assert
without spinning up Mongo to prove it. Keyed by file_id since one upload can
carry several files, each claimed and tracked independently.
"""

from __future__ import annotations

import pytest

from tests.fakes import staging_store as staging_store_fake


@pytest.fixture
def staging_store() -> staging_store_fake.InMemoryStagingStore:
    return staging_store_fake.InMemoryStagingStore()
