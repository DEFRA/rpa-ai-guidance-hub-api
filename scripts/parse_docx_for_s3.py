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

A uuid is not a name, so alongside the folders this also keeps a manifest -
`manifest.json`, at the root of the output directory - mapping the guide's own
name (its file name, normalised, unless `--name` says otherwise) to the
document id it was minted under and the ordered list of its versions, each
entry naming its version number (1, 2, 3, ...) and the version id that number
was stored as. Running this again for the same name appends the next version
under the same document id rather than starting a new guide, which is what
lets a prototype refer to "claims-guide version 2" and resolve it to the
uuids the bucket actually uses.

Nothing else in the service is involved: `app.guidance.parsing` imports only
the standard library and python-docx, and the store speaks `file://`, so no
configuration, database, object store or Bedrock access is needed to run this.

Usage:
  uv run scripts/parse_docx_for_s3.py <document.docx>
  uv run scripts/parse_docx_for_s3.py <document.docx> \\
      --name claims-guide --output-dir ~/guides
"""

import argparse
import datetime as dt
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from app.guidance.documents import store
from app.guidance.parsing import models, parser
from app.guidance.parsing.errors import DocumentParseError

# Matches app.guidance.service._ASSETS: pictures sit one level above versions, so
# every version of a document shares one asset pool rather than duplicating them.
_ASSETS = "../assets"

_DEFAULT_OUTPUT_DIR = Path.home() / "rpa-ai-guidance-hub" / "parsed-guides"

_MANIFEST_NAME = "manifest.json"


def normalised_name(stem: str) -> str:
    """A file's stem as a stable manifest key: lowercased, spaces to hyphens.

    So "Claims Guide.docx" and a second copy someone names "claims guide.docx"
    are recognised as the same guide rather than filed as two.
    """
    return stem.strip().lower().replace(" ", "-")


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


def load_manifest(path: Path) -> dict[str, Any]:
    """The manifest as it stands, or an empty one where none has been written yet."""
    if not path.is_file():
        return {}
    manifest: dict[str, Any] = json.loads(path.read_text())
    return manifest


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n")


def next_version_number(guide: dict[str, Any]) -> int:
    """The next version number for a guide, one more than the highest so far."""
    numbers = [version["version"] for version in guide.get("versions", [])]
    return max(numbers, default=0) + 1


def record_version(
    manifest: dict[str, Any],
    name: str,
    document_id: str,
    version_number: int,
    version_id: str,
    document: models.MarkdownDocument,
) -> None:
    """Add one version to the guide named `name`, minting the guide if it is new.

    A guide's `createdAt` is when it was first parsed and its `updatedAt` when
    its latest version was; a version is never changed once parsed, so its two
    are the same.
    """
    now = dt.datetime.now(tz=dt.UTC).isoformat()
    guide = manifest.setdefault(
        name,
        {
            "documentId": document_id,
            "createdAt": now,
            "updatedAt": now,
            "title": document.title,
            "versions": [],
        },
    )
    guide["documentId"] = document_id
    # Guides minted before createdAt was recorded keep their first version's time.
    guide.setdefault(
        "createdAt",
        guide["versions"][0].get("updatedAt", now) if guide["versions"] else now,
    )
    guide["updatedAt"] = now
    guide["versions"].append(
        {
            "version": version_number,
            "versionId": version_id,
            "createdAt": now,
            "updatedAt": now,
            "sections": len(document.sections),
            "images": len(document.images),
            # The key relative to the bucket, matching what app.guidance.service
            # writes - not the file:// URL this ran under, which S3 knows nothing of.
            "contentUrl": f"{document_id}/{version_id}/content.md",
        }
    )
    guide["latestVersion"] = version_number


def parse_args() -> argparse.Namespace:
    argument_parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    argument_parser.add_argument(
        "document", help="Path to the guidance document (.docx)."
    )
    argument_parser.add_argument(
        "--name",
        help="Key this guide is filed under in the manifest, normalised the same "
        "way the default is (default: the file name).",
    )
    argument_parser.add_argument(
        "--document-id",
        help="Id the guide is stored under (default: the manifest's existing id "
        "for this name, or a fresh uuid4 for a name seen for the first time).",
    )
    argument_parser.add_argument(
        "--version-id",
        help="Id this version of the guide is stored under (default: a fresh "
        "uuid4, as app.guidance.service mints one per conversion).",
    )
    argument_parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help=f"Directory the bucket layout - and the manifest - are written "
        f"under (default: {_DEFAULT_OUTPUT_DIR}).",
    )
    return argument_parser.parse_args()


def main() -> int:
    args = parse_args()
    source = Path(args.document)
    name = normalised_name(args.name or source.stem)

    manifest_path = args.output_dir / _MANIFEST_NAME
    manifest = load_manifest(manifest_path)
    guide = manifest.get(name, {})

    document_id = args.document_id or guide.get("documentId") or str(uuid.uuid4())
    version_id = args.version_id or str(uuid.uuid4())
    version_number = next_version_number(guide)

    try:
        document = parser.parse_docx(source.read_bytes())
    except (OSError, DocumentParseError) as error:
        print(f"{source.name}: {error}", file=sys.stderr)
        return 1

    root = store.document_url(args.output_dir.resolve().as_uri(), document_id)
    into = store.document_url(root, version_id)
    content_url = store.save(document, into, _ASSETS)

    record_version(manifest, name, document_id, version_number, version_id, document)
    save_manifest(manifest_path, manifest)

    print(content_url)

    # To stderr so it stays out of anything reading the location from stdout.
    print(summarise(document), file=sys.stderr)
    print(
        f"{name} v{version_number} -> documentId={document_id} versionId={version_id}",
        file=sys.stderr,
    )
    print(f"manifest: {manifest_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
