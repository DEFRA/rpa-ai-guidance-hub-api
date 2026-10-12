#!/usr/bin/env python3
"""Parse a .docx into a folder laid out the way the API's S3 bucket is.

For trying a document out against a real bucket by hand - `aws s3 sync`, or
dragging it into the console - without going through cdp-uploader, the API, or
Mongo: `parser.parse_docx` builds the document exactly as
`app.guidance.service.convert` does, and it is written under the same
`<document id>/assets/` and `<document id>/<version id>/content.md` layout that
bucket uses, `<document id>` and `<version id>` both being uuids the same way
they are in the real store. See `docs/guidance-document-storage.md` for why
that shape is what it is. Nothing is uploaded - the output is a directory,
meant to be copied to S3 by whatever means you choose.

Each run makes a new document with one version, as converting an upload does,
unless `--document-id` names an existing one: then it adds a version to that
document, under a fresh version id and sharing its pictures, as
`app.guidance.service.convert` does when given a document id. Which version is
latest is left to whoever reads the output to tell from when each was written.
Nothing is written beside it to say what it is: the folders and the Markdown are
the whole of the output, and the ids are printed for whoever needs them.

Nothing else in the service is involved: `app.guidance.parsing` imports only
the standard library and python-docx, and the store speaks `file://`, so no
configuration, database, object store or Bedrock access is needed to run this.

Usage:
  uv run scripts/parse_docx.py <document.docx>
  uv run scripts/parse_docx.py <document.docx> --output-dir ~/guides
  uv run scripts/parse_docx.py <newer.docx> --output-dir ~/guides \\
      --document-id <the documentId the first run printed>

Called directly, or by ``scripts/convert_doc.py`` in the local-dev orchestrator
repository, which resolves paths and converts several documents at once.
"""

import argparse
import sys
import uuid
from pathlib import Path

from app.guidance import ids
from app.guidance.documents import store
from app.guidance.parsing import models, parser
from app.guidance.parsing.errors import DocumentParseError

# Matches app.guidance.service._ASSETS: pictures sit one level above versions, so
# every version of a document shares one asset pool rather than duplicating them.
_ASSETS = "../assets"

_DEFAULT_OUTPUT_DIR = Path.home() / "rpa-ai-guidance-hub" / "parsed-guides"


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


def document_id_argument(value: str) -> ids.DocumentId:
    """`value` as a document id: a uuid, as the bucket names documents."""
    try:
        return ids.DocumentId(uuid.UUID(value))
    except ValueError:
        message = f"not a uuid: {value!r}"
        raise argparse.ArgumentTypeError(message) from None


def parse_args() -> argparse.Namespace:
    argument_parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    argument_parser.add_argument(
        "document", help="Path to the guidance document (.docx)."
    )
    argument_parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help=f"Directory the bucket layout is written under "
        f"(default: {_DEFAULT_OUTPUT_DIR}).",
    )
    argument_parser.add_argument(
        "--document-id",
        type=document_id_argument,
        help="Add a version to this existing document rather than making a new "
        "one (default: a fresh time-ordered uuid, as app.guidance.ids mints one).",
    )
    return argument_parser.parse_args()


def main() -> int:
    args = parse_args()
    source = Path(args.document)

    try:
        document = parser.parse_docx(source.read_bytes())
    except (OSError, DocumentParseError) as error:
        print(f"{source.name}: {error}", file=sys.stderr)
        return 1

    # Minted as app.guidance.service.convert mints them: once, before anything is
    # written, and used as the prefixes the files go under. A version id is never
    # taken from the caller, so no run can write over another's version.
    document_id = args.document_id or ids.new_document_id()
    version_id = ids.new_version_id()

    if args.document_id and not (args.output_dir / str(document_id)).is_dir():
        # Allowed, since the document may live elsewhere, but more often a
        # mistyped id or the wrong --output-dir.
        print(
            f"warning: no document {document_id} under {args.output_dir}; "
            f"starting it there",
            file=sys.stderr,
        )

    into = store.version_url(
        args.output_dir.resolve().as_uri(), document_id, version_id
    )
    content_url = store.save(document, into, _ASSETS)

    print(content_url)

    # To stderr so it stays out of anything reading the location from stdout, and
    # interleaves with the orchestrator's own per-document progress.
    print(summarise(document), file=sys.stderr)
    print(
        f"{source.name} -> documentId={document_id} versionId={version_id}",
        file=sys.stderr,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
