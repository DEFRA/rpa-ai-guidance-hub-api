#!/usr/bin/env python3
"""Write the API's OpenAPI document to ``docs/openapi.json``, or check it is current.

    uv run task openapi          # regenerate docs/openapi.json
    uv run task openapi-check    # exit 1 if the committed file is out of date

The document is built from the FastAPI app without starting it, so no Mongo, S3 or
Bedrock is involved. Importing the app does not construct ``AppConfig``, so none of
its required environment variables need to be set.
"""

import argparse
import json
import sys
from pathlib import Path

from app.entrypoints import fastapi as entrypoint

OPENAPI_PATH = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"


def render() -> str:
    # Auth is documented regardless of the environment: the committed file
    # describes the deployed service, where `USE_AUTH` is on.
    return (
        json.dumps(entrypoint.openapi_schema(auth=True), indent=2, sort_keys=True)
        + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if docs/openapi.json differs from what the app generates",
    )
    args = parser.parse_args()

    rendered = render()
    if not args.check:
        OPENAPI_PATH.write_text(rendered)
        return 0

    if not OPENAPI_PATH.exists() or OPENAPI_PATH.read_text() != rendered:
        sys.stderr.write(
            f"docs/{OPENAPI_PATH.name} is out of date; run `uv run task openapi` "
            "and commit the result.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
