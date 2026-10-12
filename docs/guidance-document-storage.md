# How a parsed guide is stored in S3

This documents the layout `app.guidance.service.convert` writes a parsed guide
under, so anything that reads or writes one directly - a prototype, a support
script, a one-off inspection - agrees with the API on where things are. The
authoritative source is `app/guidance/documents/store.py` and
`app/guidance/service.py`; this is a map of it, not a replacement for reading
those.

## Buckets

Two buckets, and they are different kinds of thing:

| Bucket (config field)                          | Owner        | Access       |
| ----------------------------------------------- | ------------ | ------------ |
| `SOURCE_DOCS_S3_BUCKET` (`source_docs_s3_bucket`) | this service | read-only    |
| `MANAGED_DOCS_S3_BUCKET` (`managed_docs_s3_bucket`) | this service | read + write |

The source bucket holds the .docx a designer uploaded, in cdp-uploader's own
key layout (`<upload id>/<file id>`, both uuids) - this service only ever reads
from it, and refuses to read anywhere else (`service._refuse_anything_but_an_upload`).
The managed-docs bucket is this service's own, and is what the rest of this
document describes. Locally, both are created by
`compose/floci/start.d/10-setup-resources.sh` as `rpa-ai-guidance-hub-source-docs`
and `rpa-ai-guidance-hub-managed-docs`.

## Key layout

A parsed guide is a Markdown file and the pictures it draws, stored as:

```
s3://<managed-docs bucket>/<document id>/assets/<sha256 of the picture>.<ext>
s3://<managed-docs bucket>/<document id>/<version id>/content.md
```

`document id` and `version id` are both version 7 (time-ordered) uuids, minted
once when a document is first converted and then kept - as the Mongo `_id` of a
`documents` record and a `document_versions` record respectively, stored there as
native uuids (see `app/guidance/ids.py` and `app/guidance/records.py`). The S3
prefixes are their text. Re-converting an existing upload reuses the same
`document id` and mints a fresh `version id`.

### Why pictures sit above versions

A picture is named after the digest of its own bytes (`images.py`), not after
where it sits in the document or which version drew it. Combined with keeping
the `assets/` prefix one level *above* every version's own folder, this means:

- every version of a document shares one pool of pictures, so re-converting a
  document that keeps a picture never duplicates it;
- a stored document names its pictures with a *relative* reference
  (`../assets/<name>`, written into the Markdown as `app.guidance.service._ASSETS`),
  which resolves to the same place from any version's `content.md`;
- nothing has to renumber or move a picture when a new version is stored -
  only the Markdown that references it changes.

### Content types

`content.md` is stored as `text/markdown; charset=utf-8`. Each picture is
stored under the content type its part in the .docx declared (`Image.content_type`),
so that whatever serves it back can set the right header - a filesystem has no
place to keep this and drops it, which is one reason the managed store is S3
and not a bind-mounted directory.

## Reading it back

`store.load(document_url_prefix)` reads a version's `content.md` back into the
same `MarkdownDocument` model that wrote it (via `reader.from_markdown`), and
`store.load_asset(document_url_prefix, assets_url_prefix, name)` fetches one
picture by the name the Markdown references. Neither needs to be told where
the pictures live beyond the same `assets_url_prefix` (`../assets`) the write
used - `store.resolved` is what turns a document's relative reference back
into a full address.

## Producing this layout without the full stack

`scripts/parse_docx.py` parses a local `.docx` with the same
`app.guidance.parsing.parser.parse_docx` the API uses, and writes it to a
directory under your home directory laid out exactly the way the bucket is -
so the result can be read as-is, or copied to S3 by hand (`aws s3 sync`, the
console, whatever you'd rather use) for prototype testing:

```bash
uv run scripts/parse_docx.py path/to/guide.docx
```

That writes `~/rpa-ai-guidance-hub/parsed-guides/<document id>/<version id>/content.md`
and its pictures under `~/rpa-ai-guidance-hub/parsed-guides/<document id>/assets/`.
Sync the output root to the managed-docs bucket, or sync that `<document id>`
directory to `s3://<managed-docs bucket>/<document id>/`, so the keys retain the document prefix.

Each run makes a new document with one version, under freshly minted time-ordered uuids,
as converting an upload does. `--document-id <id>` adds a version to an existing
document instead, under a fresh version id and sharing its `assets/`. Nothing
else is written: no manifest or index of what was converted, so which version
is latest is told by when each was written. The script prints the content location on stdout, and the
document and version ids on stderr.

See the script's own docstring (`uv run scripts/parse_docx.py --help`)
for its options (where the directory goes).
