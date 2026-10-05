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
