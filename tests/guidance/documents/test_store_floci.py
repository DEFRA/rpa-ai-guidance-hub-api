"""Storing a guide against a mocked AWS S3 (floci): the HTTP-level behaviours.

Everything about storing a guide is tested against moto in `test_store_s3.py`, which
is fast and needs nothing running. These repeat the few things the service's
correctness rests on, over real HTTP against floci, the AWS emulator the local stack
runs: that a guide read back what was written, that a second create-only write
leaves what is there, and that a doubled submission completes without error.

They test our store, not floci: nothing here asserts how the emulator itself
arbitrates concurrent writes.

All fixture text is invented, as everywhere in this package.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

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


def test_saves_racing_for_one_version_all_succeed(
    floci_s3: tuple[Any, str],
) -> None:
    """A doubled submission: every save of the same version, at once, completes
    without an error, answers alike, and leaves one of the attempts stored."""
    client, bucket = floci_s3
    version = f"s3://{bucket}/doc/v1"
    titles = [f"Attempt {n}" for n in range(4)]

    with ThreadPoolExecutor(max_workers=len(titles)) as pool:
        saves = [
            pool.submit(
                store.save,
                _guide(title, pictures=3),
                version,
                "../assets",
                s3_client=client,
            )
            for title in titles
        ]
        results = [save.result() for save in saves]

    assert len(set(results)) == 1
    loaded = store.load(version, s3_client=client)
    assert loaded is not None
    assert loaded.title in titles
