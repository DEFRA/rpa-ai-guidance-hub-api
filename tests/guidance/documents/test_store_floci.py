"""Storing a guide in floci: the few behaviours moto cannot vouch for.

Everything about storing a guide is tested against moto in `test_store_s3.py`, which
is fast and needs nothing running. These repeat only what the service's correctness
rests on and an imitation could get wrong: that a guide written over real HTTP reads
back, that S3 itself refuses to replace what is there, and that saves racing for one
version - a doubled submission - all succeed and leave one of them.

All fixture text is invented, as everywhere in this package.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from botocore.exceptions import ClientError

from app.guidance.documents import store
from app.guidance.parsing import models

PNG = b"\x89PNG\r\n\x1a\n"


def _guide(title: str, pictures: int = 0) -> models.MarkdownDocument:
    images = [
        models.Image(
            name=f"p{n:02}.png", content_type="image/png", data=PNG + bytes([n])
        )
        for n in range(pictures)
    ]
    section = models.MarkdownSection(
        heading="Evidence",
        ordinal=1,
        content="".join(f"![]({image.name})" for image in images),
        images=images,
    )
    return models.MarkdownDocument(title=title, sections=[section])


def test_a_guide_and_its_pictures_read_back(floci_s3: tuple[Any, str]) -> None:
    client, bucket = floci_s3
    version = f"s3://{bucket}/doc/v1"

    content = store.save(
        _guide("Claims", pictures=12), version, "../assets", s3_client=client
    )

    loaded = store.load(version, s3_client=client)
    assert loaded is not None
    assert loaded.title == "Claims"
    assert store.read(content, s3_client=client) is not None
    for n in range(12):
        assert store.load_asset(
            version, "../assets", f"p{n:02}.png", s3_client=client
        ) == (PNG + bytes([n]))


def test_saving_again_leaves_what_was_stored(floci_s3: tuple[Any, str]) -> None:
    """floci refuses the second create-only write, and the store takes the refusal
    as the earlier attempt's work."""
    client, bucket = floci_s3
    version = f"s3://{bucket}/doc/v1"
    store.save(_guide("First"), version, "../assets", s3_client=client)

    store.save(_guide("Second"), version, "../assets", s3_client=client)

    loaded = store.load(version, s3_client=client)
    assert loaded is not None
    assert loaded.title == "First"


class _Recording:
    """The real client, noting which writes floci accepted and which it refused."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.accepted: list[str] = []
        self.refused: list[str] = []
        self._lock = threading.Lock()

    def put_object(self, **kwargs: Any) -> Any:
        try:
            answer = self._client.put_object(**kwargs)
        except ClientError:
            with self._lock:
                self.refused.append(kwargs["Key"])
            raise
        with self._lock:
            self.accepted.append(kwargs["Key"])
        return answer

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def test_saves_racing_for_one_version_all_succeed_and_one_is_written(
    floci_s3: tuple[Any, str],
) -> None:
    """A doubled submission: every save of the same version, at once, completes
    without an error, floci accepts exactly one of their Markdown writes and
    refuses the rest, and what is stored is that one."""
    client, bucket = floci_s3
    recording = _Recording(client)
    version = f"s3://{bucket}/doc/v1"
    titles = [f"Attempt {n}" for n in range(8)]

    with ThreadPoolExecutor(max_workers=len(titles)) as pool:
        saves = [
            pool.submit(
                store.save,
                _guide(title, pictures=3),
                version,
                "../assets",
                s3_client=recording,
            )
            for title in titles
        ]
        results = [save.result() for save in saves]

    content_key = "doc/v1/content.md"
    assert len(set(results)) == 1
    assert recording.accepted.count(content_key) == 1
    assert recording.refused.count(content_key) == len(titles) - 1
    loaded = store.load(version, s3_client=client)
    assert loaded is not None
    assert loaded.title in titles
