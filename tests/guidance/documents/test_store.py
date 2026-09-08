"""Storing a guide and getting it back.

Written against a real location rather than a stubbed one. The store's whole job is
to put bytes somewhere and find them again, and a fake that answered from a dict
would be asserting that this module calls the fake the way it thinks it does -
which is the one thing that cannot go wrong in a way anybody cares about.

`base` is a URL, so these run against `file://` - the scheme is what says how a guide
is reached, and the layout, the names and the order of the writes are the same
whatever answers it.

All fixture text is invented, as everywhere in this package.
"""

import re
from pathlib import Path

import pytest

from app.guidance.documents import store
from app.guidance.parsing import anchors, models

GUIDE = "01JBQ8"
PNG = b"\x89PNG\r\n\x1a\n"

# A cross-reference's target, as the stored document writes it.
_LINK = re.compile(r"\]\((#[^)]*)\)")


def _picture(name: str = "a3f9.png", data: bytes | None = PNG) -> models.Image:
    return models.Image(name=name, content_type="image/png", data=data)


def _document(*images: models.Image) -> models.MarkdownDocument:
    section = models.MarkdownSection(
        heading="Evidence",
        ordinal=1,
        content="".join(f"![]({image.name})" for image in images),
        images=list(images),
    )
    return models.MarkdownDocument(title="Claims", sections=[section])


def _base(tmp_path: Path) -> str:
    """`tmp_path` as the URL a caller would hand the store."""
    return tmp_path.as_uri()


def _assets_in(tmp_path: Path) -> list[str]:
    directory = tmp_path / GUIDE / "assets"
    return (
        sorted(path.name for path in directory.iterdir()) if directory.is_dir() else []
    )


class TestSaving:
    def test_a_guide_is_a_markdown_file_under_its_own_id(self, tmp_path):
        saved = store.save(_document(), GUIDE, _base(tmp_path))

        assert saved == f"{_base(tmp_path)}/{GUIDE}/content.md"
        assert (tmp_path / GUIDE / "content.md").is_file()

    def test_the_pictures_land_beside_the_document_that_draws_them(self, tmp_path):
        store.save(_document(_picture()), GUIDE, _base(tmp_path))

        assert (tmp_path / GUIDE / "assets" / "a3f9.png").read_bytes() == PNG

    def test_the_document_addresses_its_pictures_relatively(self, tmp_path):
        """The whole of what makes the file portable. An absolute path - a bucket
        name, or this machine's directory layout - would be configuration written
        into a file that outlives the configuration."""
        store.save(_document(_picture()), GUIDE, _base(tmp_path))

        stored = (tmp_path / GUIDE / "content.md").read_text(encoding="utf-8")
        assert "![](assets/a3f9.png)" in stored

    def test_one_picture_drawn_twice_is_one_file(self, tmp_path):
        """A section records a picture once per place it is drawn, and the name is
        the digest of the bytes, so both entries are the same file."""
        store.save(_document(_picture(), _picture()), GUIDE, _base(tmp_path))

        assert _assets_in(tmp_path) == ["a3f9.png"]

    def test_two_different_pictures_are_two_files(self, tmp_path):
        store.save(
            _document(_picture(), _picture("b7c2.png", b"other")),
            GUIDE,
            _base(tmp_path),
        )

        assert _assets_in(tmp_path) == ["a3f9.png", "b7c2.png"]

    def test_saving_a_guide_that_was_read_back_does_not_empty_its_pictures(
        self, tmp_path
    ):
        """A document read out of the store carries its pictures by name and not by
        value - the bytes are already there, under that very name. Writing it again
        must leave them alone rather than put an empty file over each one.
        """
        store.save(_document(_picture()), GUIDE, _base(tmp_path))
        loaded = store.load(GUIDE, _base(tmp_path))

        assert loaded is not None
        store.save(loaded, GUIDE, _base(tmp_path))

        assert store.load_asset(GUIDE, "a3f9.png", _base(tmp_path)) == PNG

    def test_a_guide_can_be_stored_beside_another(self, tmp_path):
        store.save(_document(_picture()), GUIDE, _base(tmp_path))
        store.save(_document(_picture()), "01JBQ9", _base(tmp_path))

        assert store.load(GUIDE, _base(tmp_path)) is not None
        assert store.load("01JBQ9", _base(tmp_path)) is not None


class TestWhereAGuideLives:
    """The scheme is the whole of what says how a guide is reached."""

    def test_the_urls_a_guide_is_written_to(self, tmp_path):
        base = _base(tmp_path)

        assert store.content_url(GUIDE, base) == f"{base}/{GUIDE}/content.md"
        assert (
            store.asset_url(GUIDE, "a3f9.png", base)
            == f"{base}/{GUIDE}/assets/a3f9.png"
        )

    def test_a_base_that_already_ends_in_a_slash_does_not_gain_another(self, tmp_path):
        assert store.content_url(GUIDE, f"{_base(tmp_path)}/") == store.content_url(
            GUIDE, _base(tmp_path)
        )

    def test_a_scheme_this_store_cannot_reach_says_so(self):
        """Naming the schemes it does speak, because writing the wrong URL is a
        configuration mistake and the fix is to write a different one."""
        with pytest.raises(store.UnsupportedSchemeError, match="file://"):
            store.load(GUIDE, "s3://a-bucket/guides")

    def test_a_url_escaping_a_character_a_path_may_hold_is_unescaped(self, tmp_path):
        """A guide's directory is named by whatever minted its id, and a URL escapes
        what a path is allowed to contain. Taking `.path` as it stands would look
        for a directory named with a literal %20."""
        spaced = tmp_path / "with a space"
        spaced.mkdir()

        store.save(_document(), GUIDE, spaced.as_uri())

        assert (spaced / GUIDE / "content.md").is_file()


class TestLoading:
    def test_a_guide_comes_back_as_the_model_that_wrote_it(self, tmp_path):
        store.save(_document(_picture()), GUIDE, _base(tmp_path))

        loaded = store.load(GUIDE, _base(tmp_path))

        assert loaded is not None
        assert loaded.title == "Claims"
        assert [section.heading for section in loaded.sections] == ["Evidence"]
        assert [image.name for image in loaded.images] == ["a3f9.png"]

    def test_a_guide_that_was_never_stored_is_not_an_error(self, tmp_path):
        """ "No such guide" is an ordinary answer to an ordinary question, so it is
        an answer rather than an exception a caller has to know to catch."""
        assert store.load("no-such-guide", _base(tmp_path)) is None

    def test_a_picture_is_read_only_when_something_asks_for_it(self, tmp_path):
        store.save(_document(_picture(data=b"the bytes")), GUIDE, _base(tmp_path))

        loaded = store.load(GUIDE, _base(tmp_path))

        assert loaded is not None
        assert loaded.images[0].data is None
        assert store.load_asset(GUIDE, "a3f9.png", _base(tmp_path)) == b"the bytes"

    def test_asking_for_a_picture_that_is_not_there(self, tmp_path):
        assert store.load_asset(GUIDE, "missing.png", _base(tmp_path)) is None


class TestTheRoundTrip:
    def test_a_guide_renders_the_same_after_the_trip(self, tmp_path):
        document = _document(_picture())

        store.save(document, GUIDE, _base(tmp_path))
        loaded = store.load(GUIDE, _base(tmp_path))

        assert loaded is not None
        assert loaded.markdown(store.ASSET_PREFIX) == document.markdown(
            store.ASSET_PREFIX
        )

    def test_writing_what_was_read_stores_the_same_bytes(self, tmp_path):
        """The guarantee the stored form actually offers. Anything the reader does
        not undo - the padding `alignment` puts in a table, an anchor a
        cross-reference was resolved to - has to survive being written again
        unchanged, or a guide would drift every time it was opened.
        """
        section = models.MarkdownSection(
            heading="Rates",
            ordinal=1,
            content=(
                "| Case | Amount |\n| --- | --- |\n| A much longer case | 100000 |"
            ),
        )
        store.save(
            models.MarkdownDocument(title="Claims", sections=[section]),
            GUIDE,
            _base(tmp_path),
        )

        first = (tmp_path / GUIDE / "content.md").read_bytes()
        for _ in range(2):
            written = store.load(GUIDE, _base(tmp_path))
            assert written is not None
            store.save(written, GUIDE, _base(tmp_path))
            assert (tmp_path / GUIDE / "content.md").read_bytes() == first

    def test_a_stored_guide_needs_nothing_but_itself(self, tmp_path):
        """Self-contained, stated as the two things it means: every picture the
        document names is on disk where the document says, and every anchor it
        links to is printed by a heading in the same file."""
        document = _document(_picture())
        store.save(document, GUIDE, _base(tmp_path))
        markdown = (tmp_path / GUIDE / "content.md").read_text(encoding="utf-8")

        for image in document.images:
            assert (tmp_path / GUIDE / "assets" / image.name).is_file()

        loaded = store.load(GUIDE, _base(tmp_path))
        assert loaded is not None
        printed = {
            f"#{anchor}" for anchor in anchors.of_document(loaded.sections).values()
        }
        assert all(target in printed for target in _LINK.findall(markdown))
