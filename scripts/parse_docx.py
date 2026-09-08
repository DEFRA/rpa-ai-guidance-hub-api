#!/usr/bin/env python3
"""Parse a .docx into a stored guide, without the stack.

Runs exactly what the API runs -- ``parse_docx`` to build the document, then
``store.save`` to write it -- so what comes out is laid out the way a guide really
is: one Markdown file with its pictures beside it, addressed relatively.

Nothing else in the service is involved. ``app.guidance.parsing`` imports only the
standard library and python-docx, and the store speaks ``file://``, so no
configuration, database, object store or Bedrock access is needed.

The destination is a URL -- ``file:///var/guides`` -- and a plain path is accepted
and turned into one, because naming a directory is the obvious thing to type. The
guide's own id names the directory beneath it, and defaults to the document's name so
that a converted document can be found again by the name it went in under.

Usage:
  uv run scripts/parse_docx.py <document.docx> <directory-or-url> [--guide-id ID]

Called directly, or by ``scripts/convert_doc.py`` in the local-dev orchestrator
repository, which resolves paths and converts several documents at once.
"""

import argparse
import sys
from pathlib import Path

from app.guidance.documents import store
from app.guidance.parsing import models, parser
from app.guidance.parsing.errors import DocumentParseError


def as_url(destination: str) -> str:
    """`destination` as a URL, whether it was given as one or as a path."""
    if "://" in destination:
        return destination

    return Path(destination).resolve().as_uri()


def summarise(document: models.MarkdownDocument) -> str:
    """A short report of what was parsed, for eyeballing a real document."""
    lines = [
        f"title:    {document.title!r}",
        f"sections: {len(document.sections)}",
        f"images:   {len(document.images)}",
    ]
    lines.extend(
        f"  {'  ' * (section.level - 1)}{section.number} {section.heading}"
        for section in document.sections
    )
    return "\n".join(lines)


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
        help="Where to store the guide: a directory, or a file:// URL.",
    )
    argument_parser.add_argument(
        "--guide-id",
        default=None,
        help="Store under this id (default: the document's own name).",
    )
    return argument_parser.parse_args()


def main() -> int:
    args = parse_args()
    source = Path(args.document)
    guide_id = args.guide_id or source.stem

    try:
        document = parser.parse_docx(source.read_bytes())
    except (OSError, DocumentParseError) as error:
        print(f"{source.name}: {error}", file=sys.stderr)
        return 1

    print(store.save(document, store.guide_url(as_url(args.destination), guide_id)))

    # To stderr so it stays out of anything reading the location from stdout, and
    # interleaves with the orchestrator's own per-document progress.
    print(summarise(document), file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
