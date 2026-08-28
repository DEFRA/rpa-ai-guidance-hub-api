#!/usr/bin/env python3
"""Parse a .docx into Markdown with the guidance parser, without the stack.

Runs exactly what the API runs -- ``parse_docx`` to build the document, then its
``markdown()`` -- but writes the result to a local file instead of S3. Nothing else
in the service is involved: ``app.guidance.parsing`` imports only the standard
library and python-docx, so no configuration, database, S3 or Bedrock access is
needed.

Images are written only when ``--images-dir`` is given, under the same bare names
the Markdown refers to them by, so ``--images-prefix`` is the only thing that has to
agree between the two.

Usage:
  uv run scripts/parse_docx.py <document.docx> <output.md> \
      [--images-dir DIR] [--images-prefix PREFIX]

Called directly, or by ``scripts/convert_doc.py`` in the local-dev orchestrator
repository, which resolves paths and converts several documents at once.
"""

import argparse
import sys
from pathlib import Path

from app.guidance.parsing import parser
from app.guidance.parsing.errors import DocumentParseError


def write_images(images, images_dir: Path) -> None:
    """Write each of the document's images under its own generated name."""
    images_dir.mkdir(parents=True, exist_ok=True)
    for image in images:
        (images_dir / image.name).write_bytes(image.data)


def summarise(document, images) -> str:
    """A short report of what was parsed, for eyeballing a real document."""
    lines = [
        f"title:    {document.title!r}",
        f"sections: {len(document.sections)}",
        f"images:   {len(images)}",
    ]
    lines.extend(
        f"  {'  ' * (section.depth - 1)}{section.number} {section.heading}"
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
        "output", help="Path to write the rendered Markdown to."
    )
    argument_parser.add_argument(
        "--images-dir",
        default=None,
        help="Directory to write embedded images to (default: images are dropped).",
    )
    argument_parser.add_argument(
        "--images-prefix",
        default="",
        help="Prefix for image paths as they appear in the Markdown.",
    )
    return argument_parser.parse_args()


def main() -> int:
    args = parse_args()
    source = Path(args.document)
    output = Path(args.output)

    try:
        document = parser.parse_docx(source.read_bytes())
    except (OSError, DocumentParseError) as error:
        print(f"{source.name}: {error}", file=sys.stderr)
        return 1

    markdown = document.markdown(args.images_prefix)
    images = document.images

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown, encoding="utf-8")

    if images and args.images_dir:
        write_images(images, Path(args.images_dir))

    # To stderr so it stays out of anything piping the Markdown, and interleaves
    # with the orchestrator's own per-document progress.
    print(summarise(document, images), file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
