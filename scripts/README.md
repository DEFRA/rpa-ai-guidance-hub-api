# Scripts

> Note: may want to migrate the existing scripts here to validate driving the
rpa-ai-guidance hub backend.

- `parse_docx.py` - parse a `.docx` into a directory laid out exactly the
  way `app.guidance.service.convert` lays out its S3 bucket (nothing is
  uploaded - copy the directory to S3 yourself, e.g. with `aws s3 sync`). See
  [`docs/guidance-document-storage.md`](../docs/guidance-document-storage.md)
  for that layout.
- `audit_docx.py` - report what the guidance parser loses when it renders a
  `.docx` as Markdown.
- `export_openapi.py` - write the app's OpenAPI document to `docs/openapi.json`
  (`uv run task openapi`), or with `--check` fail if the committed file is stale
  (`uv run task openapi-check`, run by the pull-request workflow).
- `check_raises.py` - fail if a `pytest.raises` block makes more than one call,
  so that only the call under test can raise (run by `uv run task lint`).
- `check_imports.py` - fail if Python code imports a name rather than a module,
  or imports relatively (run by `uv run task lint`).
