"""Storing a guide and getting it back.

Written against a real location rather than a stubbed one. The store's whole job is
to put bytes somewhere and find them again, and a fake that answered from a dict
would be asserting that this module calls the fake the way it thinks it does -
which is the one thing that cannot go wrong in a way anybody cares about.

A guide is addressed by URL, so these run against `file://` - the scheme is what says
how it is reached, and the names and the order of the writes are the same whatever
answers it.

All fixture text is invented, as everywhere in this package.
"""

import re
from pathlib import Path
from urllib.parse import urljoin

import pytest

from app.guidance.documents import store
from app.guidance.parsing import anchors, models

GUIDE = "01JBQ8"

# The pictures beside the document, said the way a document says it.
RELATIVE = "./assets"
PNG = b"\x89PNG\r\n\x1a\n"

# A cross-reference's target, and a picture's address, as the stored document
# writes them.
_LINK = re.compile(r"\]\((#[^)]*)\)")
_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")


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


def _elsewhere(tmp_path: Path) -> str:
    """Somewhere for pictures that is not beneath the guide that draws them."""
    return f"{tmp_path.as_uri()}/pictures"


def _guide(tmp_path: Path, document_id: str = GUIDE) -> str:
    """The URL of a guide kept under `tmp_path`, as a caller would compose it."""
    return store.document_url(tmp_path.as_uri(), document_id)


def _save(document: models.MarkdownDocument, guide: str) -> str:
    """Store a guide laid out the way the local tooling lays one out: pictures in a
    directory beside the document. That the two prefixes need not be that pair is
    what `TestPicturesKeptElsewhere` is about; everywhere else they are."""
    return store.save(document, guide, RELATIVE)


def _assets_in(tmp_path: Path) -> list[str]:
    directory = tmp_path / GUIDE / "assets"
    return (
        sorted(path.name for path in directory.iterdir()) if directory.is_dir() else []
    )


class TestSaving:
    def test_a_guide_is_a_markdown_file_under_its_own_id(self, tmp_path):
        saved = _save(_document(), _guide(tmp_path))

        assert saved == f"{_guide(tmp_path)}/content.md"
        assert (tmp_path / GUIDE / "content.md").is_file()

    def test_the_pictures_land_beside_the_document_that_draws_them(self, tmp_path):
        _save(_document(_picture()), _guide(tmp_path))

        assert (tmp_path / GUIDE / "assets" / "a3f9.png").read_bytes() == PNG

    def test_the_document_addresses_its_pictures_relatively(self, tmp_path):
        """The whole of what makes the file portable. An absolute path - a bucket
        name, or this machine's directory layout - would be configuration written
        into a file that outlives the configuration."""
        _save(_document(_picture()), _guide(tmp_path))

        stored = (tmp_path / GUIDE / "content.md").read_text(encoding="utf-8")
        assert "![](./assets/a3f9.png)" in stored

    def test_one_picture_drawn_twice_is_one_file(self, tmp_path):
        """A section records a picture once per place it is drawn, and the name is
        the digest of the bytes, so both entries are the same file."""
        _save(_document(_picture(), _picture()), _guide(tmp_path))

        assert _assets_in(tmp_path) == ["a3f9.png"]

    def test_two_different_pictures_are_two_files(self, tmp_path):
        _save(_document(_picture(), _picture("b7c2.png", b"other")), _guide(tmp_path))

        assert _assets_in(tmp_path) == ["a3f9.png", "b7c2.png"]

    def test_saving_a_guide_that_was_read_back_does_not_empty_its_pictures(
        self, tmp_path
    ):
        """A document read out of the store carries its pictures by name and not by
        value - the bytes are already there, under that very name. Writing it again
        must leave them alone rather than put an empty file over each one.
        """
        _save(_document(_picture()), _guide(tmp_path))
        loaded = store.load(_guide(tmp_path))

        assert loaded is not None
        _save(loaded, _guide(tmp_path))

        assert store.load_asset(_guide(tmp_path), RELATIVE, "a3f9.png") == PNG

    def test_a_guide_can_be_stored_beside_another(self, tmp_path):
        _save(_document(_picture()), _guide(tmp_path))
        _save(_document(_picture()), _guide(tmp_path, "01JBQ9"))

        assert store.load(_guide(tmp_path)) is not None
        assert store.load(_guide(tmp_path, "01JBQ9")) is not None


class TestWhereAGuideLives:
    """The scheme is the whole of what says how a guide is reached."""

    def test_what_a_guide_url_holds(self, tmp_path):
        guide = _guide(tmp_path)

        assert store.content_url(guide) == f"{guide}/content.md"
        assert store.assets_url(guide, RELATIVE) == f"{guide}/assets/"
        assert (
            store.asset_url(guide, RELATIVE, "a3f9.png") == f"{guide}/assets/a3f9.png"
        )

    def test_a_documents_own_image_path_resolves_to_where_the_picture_went(
        self, tmp_path
    ):
        """Resolved the way a reader resolves it - against the document's own URL -
        the relative path in the stored Markdown reaches the file that was written.
        That the two agree is the whole of what makes the stored file readable on
        its own, so it is asserted rather than assumed.
        """
        guide = _guide(tmp_path)
        _save(_document(_picture()), guide)

        referenced = urljoin(store.content_url(guide), f"{store.ASSET_PREFIX}a3f9.png")

        assert referenced == store.asset_url(guide, RELATIVE, "a3f9.png")
        assert store.load_asset(guide, RELATIVE, "a3f9.png") == PNG

    def test_a_url_that_already_ends_in_a_slash_does_not_gain_another(self, tmp_path):
        guide = _guide(tmp_path)

        assert store.content_url(f"{guide}/") == store.content_url(guide)

    def test_a_scheme_this_store_cannot_reach_says_so(self):
        """Naming the schemes it does speak, because writing the wrong URL is a
        configuration mistake and the fix is to write a different one."""
        with pytest.raises(store.UnsupportedSchemeError) as refused:
            store.load("https://example.org/guides/01JBQ8")

        assert "file://" in str(refused.value)
        assert "s3://" in str(refused.value)

    def test_composing_a_guide_url_escapes_the_id(self, tmp_path):
        """The one place an id is escaped. The dev tooling names a guide after the
        document it converted, and those hold spaces; a `#` in one would truncate
        every URL built from it."""
        base = tmp_path.as_uri()

        assert (
            store.document_url(base, "CS Revenue 2026") == f"{base}/CS%20Revenue%202026"
        )

        _save(_document(), store.document_url(base, "CS Revenue 2026"))

        assert (tmp_path / "CS Revenue 2026" / "content.md").is_file()
        assert store.load(store.document_url(base, "CS Revenue 2026")) is not None

    def test_a_url_escaping_a_character_a_path_may_hold_is_unescaped(self, tmp_path):
        """A guide's directory is named by whatever minted its id, and a URL escapes
        what a path is allowed to contain. Taking `.path` as it stands would look
        for a directory named with a literal %20."""
        spaced = tmp_path / "with a space"
        spaced.mkdir()

        _save(_document(), store.document_url(spaced.as_uri(), GUIDE))

        assert (spaced / GUIDE / "content.md").is_file()


class TestPicturesKeptElsewhere:
    """A guide's pictures need not sit beneath it, which is why `save` is told where
    they go rather than working it out. What the stored document calls them follows
    from that, so neither form is a mode anybody selects."""

    def test_the_pictures_go_where_the_assets_prefix_says(self, tmp_path):
        store.save(_document(_picture()), _guide(tmp_path), _elsewhere(tmp_path))

        assert (tmp_path / "pictures" / "a3f9.png").read_bytes() == PNG

    def test_a_document_whose_pictures_are_elsewhere_names_them_in_full(self, tmp_path):
        """It has no neighbour to point at, so a relative path would dangle."""
        store.save(_document(_picture()), _guide(tmp_path), _elsewhere(tmp_path))

        stored = (tmp_path / GUIDE / "content.md").read_text(encoding="utf-8")
        assert f"![]({_elsewhere(tmp_path)}/a3f9.png)" in stored

    def test_such_a_document_reads_back_naming_the_same_pictures(self, tmp_path):
        """A name is the last segment of whatever the document points at, so the two
        forms read alike and nothing has to be told which it is holding."""
        store.save(_document(_picture()), _guide(tmp_path), _elsewhere(tmp_path))

        loaded = store.load(_guide(tmp_path))

        assert loaded is not None
        assert [image.name for image in loaded.images] == ["a3f9.png"]

    def test_a_relative_prefix_is_resolved_against_the_document(self, tmp_path):
        """The same call reaching the other answer: `./assets` is an address in the
        document, so the pictures land where it resolves to rather than at a
        directory named `./assets`."""
        guide = _guide(tmp_path)
        store.save(_document(_picture()), guide, RELATIVE)

        stored = (tmp_path / GUIDE / "content.md").read_text(encoding="utf-8")
        assert "![](./assets/a3f9.png)" in stored
        assert (tmp_path / GUIDE / "assets" / "a3f9.png").read_bytes() == PNG

    def test_a_prefix_reaching_outside_the_guide_resolves_where_it_points(
        self, tmp_path
    ):
        """A relative prefix is a path and not a name, so `../shared` names the
        directory a sibling guide would draw from rather than one with dots in it.
        Written into the document unchanged, it resolves there for a reader too."""
        store.save(_document(_picture()), _guide(tmp_path), "../shared")

        stored = (tmp_path / GUIDE / "content.md").read_text(encoding="utf-8")
        assert "![](../shared/a3f9.png)" in stored
        assert (tmp_path / "shared" / "a3f9.png").read_bytes() == PNG

    def test_the_address_the_document_gives_is_the_one_that_answers(self, tmp_path):
        """Following the URL in the file reaches the picture, with nothing working
        out where it ought to have been."""
        guide = _guide(tmp_path)
        store.save(_document(_picture()), guide, _elsewhere(tmp_path))

        stored = (tmp_path / GUIDE / "content.md").read_text(encoding="utf-8")
        addressed = _IMAGE.search(stored).group(1)

        assert store.read(addressed) == PNG

    @pytest.mark.parametrize("prefix", [RELATIVE, "assets", "../shared"])
    def test_what_the_document_says_resolves_to_where_the_picture_went(
        self, tmp_path, prefix
    ):
        """The property every form owes, and the only one that matters to a reader:
        resolve the address in the file the way a reader resolves it, and arrive at
        the bytes the store wrote."""
        guide = _guide(tmp_path)
        store.save(_document(_picture()), guide, prefix)

        stored = (tmp_path / GUIDE / "content.md").read_text(encoding="utf-8")
        said = _IMAGE.search(stored).group(1)

        assert urljoin(store.content_url(guide), said) == store.asset_url(
            guide, prefix, "a3f9.png"
        )
        assert store.load_asset(guide, prefix, "a3f9.png") == PNG


class TestLoading:
    def test_a_guide_comes_back_as_the_model_that_wrote_it(self, tmp_path):
        _save(_document(_picture()), _guide(tmp_path))

        loaded = store.load(_guide(tmp_path))

        assert loaded is not None
        assert loaded.title == "Claims"
        assert [section.heading for section in loaded.sections] == ["Evidence"]
        assert [image.name for image in loaded.images] == ["a3f9.png"]

    def test_a_guide_that_was_never_stored_is_not_an_error(self, tmp_path):
        """ "No such guide" is an ordinary answer to an ordinary question, so it is
        an answer rather than an exception a caller has to know to catch."""
        assert store.load(_guide(tmp_path, "no-such-guide")) is None

    def test_a_picture_is_read_only_when_something_asks_for_it(self, tmp_path):
        _save(_document(_picture(data=b"the bytes")), _guide(tmp_path))

        loaded = store.load(_guide(tmp_path))

        assert loaded is not None
        assert loaded.images[0].data is None
        assert store.load_asset(_guide(tmp_path), RELATIVE, "a3f9.png") == b"the bytes"

    def test_asking_for_a_picture_that_is_not_there(self, tmp_path):
        assert store.load_asset(_guide(tmp_path), RELATIVE, "missing.png") is None


class TestTheRoundTrip:
    def test_a_guide_renders_the_same_after_the_trip(self, tmp_path):
        document = _document(_picture())

        _save(document, _guide(tmp_path))
        loaded = store.load(_guide(tmp_path))

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
        _save(
            models.MarkdownDocument(title="Claims", sections=[section]),
            _guide(tmp_path),
        )

        first = (tmp_path / GUIDE / "content.md").read_bytes()
        for _ in range(2):
            written = store.load(_guide(tmp_path))
            assert written is not None
            _save(written, _guide(tmp_path))
            assert (tmp_path / GUIDE / "content.md").read_bytes() == first

    def test_a_stored_guide_needs_nothing_but_itself(self, tmp_path):
        """Self-contained, stated as the two things it means: every picture the
        document names is on disk where the document says, and every anchor it
        links to is printed by a heading in the same file."""
        document = _document(_picture())
        _save(document, _guide(tmp_path))
        markdown = (tmp_path / GUIDE / "content.md").read_text(encoding="utf-8")

        for image in document.images:
            assert (tmp_path / GUIDE / "assets" / image.name).is_file()

        loaded = store.load(_guide(tmp_path))
        assert loaded is not None
        printed = {
            f"#{anchor}" for anchor in anchors.of_document(loaded.sections).values()
        }
        assert all(target in printed for target in _LINK.findall(markdown))
