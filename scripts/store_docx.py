#!/usr/bin/env python3
"""Store a .docx as a guide, read it back, and report whether it survived.

The round trip end to end and on a real document: parse it, write the guide and its
pictures, read the stored Markdown back into a model, and compare. Like
``parse_docx.py`` it needs nothing running -- no configuration, no database, no
object store, no Bedrock -- because a stored guide is a directory holding a Markdown
file and the pictures it draws.

What it checks, in the order it matters:

* the stored Markdown renders again byte for byte, which is the guarantee the storage
  format actually offers;
* every heading comes back with the same text, ordinal, appendix flag and number;
* every picture comes back under the same name, and its bytes are on disk under the
  digest that names them;
* no link in the stored file dangles that did not dangle before it was stored. A
  cross-reference Word wrote to a bookmark that is not a heading has nowhere to point
  either way -- the CS guide has one -- and that is a defect in the document rather
  than a loss in storing it, so it is reported and not counted against the trip.

The one thing it does not check is that ``section.content`` is identical, because it
is not: a cross-reference is stored resolved, so the Word bookmark name it was written
with has been replaced by the anchor it resolves to, and a table keeps the padding
rendering gave it. Both are by design and ``documents/reader.py`` says why.

The destination is a URL - ``file:///var/guides`` - and a plain path is accepted and
turned into one, because naming a directory is the obvious thing to type. Which
scheme a store can reach is the store's business; making a path usable is this
script's.

Usage:
  uv run scripts/store_docx.py <document.docx> <directory-or-url> [--guide-id ID]

Guarded by this docstring and by what it prints rather than by tests of its own: it
is an instrument for looking at a real document, and everything it exercises is under
test in ``tests/guidance/``.
"""

import argparse
import re
import sys
import uuid
from pathlib import Path

from app.guidance.documents import store
from app.guidance.parsing import anchors, models, parser
from app.guidance.parsing.errors import DocumentParseError

_LINK = re.compile(r"\]\(#([^)]*)\)")


def compare(
    before: models.MarkdownDocument, after: models.MarkdownDocument
) -> list[str]:
    """Every way the two documents differ, named. Empty is the answer wanted."""
    failures = []

    if before.markdown(store.ASSET_PREFIX) != after.markdown(store.ASSET_PREFIX):
        failures.append("the stored document does not render the same twice")
    if before.title != after.title:
        failures.append(f"title: {before.title!r} became {after.title!r}")
    if len(before.sections) != len(after.sections):
        failures.append(
            f"sections: {len(before.sections)} became {len(after.sections)}"
        )

    for one, other in zip(before.sections, after.sections, strict=False):
        shape = (one.heading, one.ordinal, one.appendix, one.number, one.level)
        if shape != (
            other.heading,
            other.ordinal,
            other.appendix,
            other.number,
            other.level,
        ):
            failures.append(f"section {one.number}: {shape} became {other!r}")

    if [image.name for image in before.images] != [
        image.name for image in after.images
    ]:
        failures.append("the pictures came back under different names")

    return failures


def dangling_anchors(document: models.MarkdownDocument) -> set[str]:
    """Every link target in the document that no heading in it prints.

    A link to an anchor nothing prints is the one way a "self-contained" document
    can fail to be one, so it is measured on both sides of the trip rather than
    asserted away on either.
    """
    printed = set(anchors.of_document(document.sections).values())
    markdown = document.markdown(store.ASSET_PREFIX)
    return {target for target in _LINK.findall(markdown) if target not in printed}


def missing_assets(document: models.MarkdownDocument, guide: str) -> list[str]:
    """Every picture named by the document whose bytes are not in the store."""
    return [
        image.name
        for image in document.images
        if store.load_asset(guide, image.name) is None
    ]


def as_url(destination: str) -> str:
    """`destination` as a URL, whether it was given as one or as a path."""
    if "://" in destination:
        return destination

    return Path(destination).resolve().as_uri()


def parse_args() -> argparse.Namespace:
    argument_parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    argument_parser.add_argument(
        "document", help="Path to the guidance document (.docx)."
    )
    argument_parser.add_argument(
        "destination",
        help="Where to store guides: a directory, or a URL such as file:///var/guides.",
    )
    argument_parser.add_argument(
        "--guide-id",
        default=None,
        help="Store under this id rather than a fresh one (default: a new uuid4).",
    )
    return argument_parser.parse_args()


def main() -> int:
    args = parse_args()
    source = Path(args.document)
    guide_id = args.guide_id or str(uuid.uuid4())
    guide = store.guide_url(as_url(args.destination), guide_id)

    try:
        parsed = parser.parse_docx(source.read_bytes())
    except (OSError, DocumentParseError) as error:
        print(f"{source.name}: {error}", file=sys.stderr)
        return 1

    saved = store.save(parsed, guide)
    loaded = store.load(guide)

    if loaded is None:
        print(f"stored {guide_id} and could not read it back", file=sys.stderr)
        return 1

    print(f"guide:    {guide_id}")
    print(f"markdown: {saved}")
    print(f"assets:   {store.assets_url(guide)}")
    print(f"sections: {len(loaded.sections)}")
    print(f"pictures: {len(loaded.images)}")

    already_dangling = dangling_anchors(parsed)
    for target in sorted(already_dangling):
        print(
            f"\nnote: the document itself links to #{target}, which no heading in "
            f"it prints.\n      Word wrote a cross-reference to a bookmark that is "
            f"not a heading; storing it\n      neither caused that nor repaired it."
        )

    failures = compare(parsed, loaded)
    failures += [
        f"picture missing from the store: {name}"
        for name in missing_assets(loaded, guide)
    ]
    failures += [
        f"storing the document broke a link to #{target}"
        for target in sorted(dangling_anchors(loaded) - already_dangling)
    ]

    if failures:
        print("\nthe round trip lost something:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("\nthe document survived being stored and read back")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
