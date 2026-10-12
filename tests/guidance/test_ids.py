"""The ids this service mints for what it stores."""

from __future__ import annotations

import uuid

from app.guidance import ids


class TestMintingIds:
    def test_a_new_document_id_is_a_time_ordered_uuid(self):
        document_id = ids.new_document_id()

        assert isinstance(document_id, uuid.UUID)
        assert document_id.version == 7

    def test_a_new_version_id_is_a_time_ordered_uuid(self):
        version_id = ids.new_version_id()

        assert isinstance(version_id, uuid.UUID)
        assert version_id.version == 7

    def test_ids_minted_one_after_another_sort_in_the_order_they_were_minted(self):
        """What a time-ordered id is for: the newest sorts last, in Mongo's index and
        in a listing of the bucket alike. Nothing reads meaning into the order -
        `createdAt` is what says which version is newest."""
        minted = [ids.new_document_id() for _ in range(100)]

        assert sorted(minted) == minted
        assert sorted(str(document_id) for document_id in minted) == [
            str(document_id) for document_id in minted
        ]
