# rpa-ai-guidance-hub-api

This is work-in-progress. See [To Do List](./TODO.md)

- [rpa-ai-guidance-hub-api](#rpa-ai-guidance-hub-api)
  - [Requirements](#requirements)
    - [Python](#python)
    - [Environment Variable Configuration](#environment-variable-configuration)
    - [Linting and Formatting](#linting-and-formatting)
    - [Docker](#docker)
  - [Local development](#local-development)
    - [Setup & Configuration](#setup--configuration)
    - [Development](#development)
    - [Testing](#testing)
    - [Production Mode](#production-mode)
  - [Where code goes](#where-code-goes)
    - [Schemas and models](#schemas-and-models)
    - [Ids](#ids)
    - [Guarding routes](#guarding-routes)
  - [Authentication](#authentication)
  - [API endpoints](#api-endpoints)
  - [Further reading](#further-reading)
  - [Custom Cloudwatch Metrics](#custom-cloudwatch-metrics)
  - [Pipelines](#pipelines)
    - [Dependabot](#dependabot)
    - [SonarCloud](#sonarcloud)
  - [Licence](#licence)
    - [About the licence](#about-the-licence)

## Requirements

### Python

Please install python `>= 3.14` (see `.python-version`) and `pipx` in your environment. This project uses [uv](https://github.com/astral-sh/uv) to manage the environment and dependencies.

```python
# install uv via pipx
pipx install uv

# sync dependencies
uv sync

# source python venv
source .venv/bin/activate

# install the pre-commit hooks
pre-commit install
```

This service uses the [`Fast API`](https://fastapi.tiangolo.com/) Python API framework.

### Environment Variable Configuration

The application uses Pydantic's `BaseSettings` for configuration management in `app/config.py`, automatically mapping environment variables to configuration fields. Configuration is loaded using `python-dotenv` which supports both `.env` files and environment variables.

In CDP, environment variables and secrets need to be set using CDP conventions. See links below:
- [CDP App Config](https://github.com/DEFRA/cdp-documentation/blob/main/how-to/config.md)
- [CDP Secrets](https://github.com/DEFRA/cdp-documentation/blob/main/how-to/secrets.md)

For local development - see [instructions below](#local-development).

#### Core Configuration

`AppConfig` validates everything at startup and refuses to start if a required value is missing
or malformed, naming the offending variable. Copy `.env.example` to `.env` for local development.

| Variable | Required | Description | Default |
| --- | --- | --- | --- |
| `AWS_ACCOUNT` | yes (set by CDP) | 12-digit AWS account ID. Also forms the caller role ARNs, see [Authentication](#authentication) | |
| `SOURCE_DOCS_S3_BUCKET` | yes | Bucket cdp-uploader delivers scanned source documents into | |
| `MANAGED_DOCS_S3_BUCKET` | yes | Bucket converted guides and their pictures are stored in | |
| `MONGO_URI` | for Mongo | MongoDB connection string. The API pings Mongo on startup and exits if it is unreachable | |
| `MONGO_DATABASE` | no | Database name | `rpa-ai-guidance-hub-api` |
| `MONGO_TRUSTSTORE` | no | Name of the `TRUSTSTORE_*` env var holding the CA cert for Mongo TLS | `TRUSTSTORE_CDP_ROOT_CA` |
| `FLOCI_ENDPOINT_URL` | local only | Endpoint of the local AWS emulator ([floci](https://github.com/floci-io/floci)) | |
| `AWS_REGION` | no | AWS region | `eu-west-2` |
| `HOST` / `PORT` | no | Bind address and port | `127.0.0.1` / `8085` |
| `HTTP_PROXY` | no | Outbound proxy | |
| `ENABLE_METRICS` | no | Emit CloudWatch EMF metrics, see [Custom Cloudwatch Metrics](#custom-cloudwatch-metrics) | `false` |
| `STAGING_RETENTION_SECONDS` | no | How long a staged document's minimal parse is kept awaiting user action | `14400` (4 hours) |
| `USE_AUTH` | no | Require a CDP web-identity token on API routes, see [Authentication](#authentication) | `false` |
| `CDP_JWT_ISSUER` | with `USE_AUTH` (set by CDP) | Expected token issuer (`iss`) | |
| `CDP_JWT_JWKS_URI` | with `USE_AUTH` (set by CDP) | URL of the issuer's JWKS, used to verify token signatures | |

`AWS_ACCOUNT`, `CDP_JWT_ISSUER` and `CDP_JWT_JWKS_URI` are injected automatically by the CDP
platform, so they are never set in a deployed environment's config. They only need to be in `.env`
for local runs.

The `.env` file holds *host-side* values (for example `FLOCI_ENDPOINT_URL=http://localhost:4566`).
`compose.yml` overrides the container-oriented ones, and `tests/test_compose_config.py` guards that.

#### Bedrock Configuration

The application uses AWS Bedrock for LLM access. The following environment variables are required:

| Variable | Description | Example |
| --- | --- | --- |
| `AWS_REGION` | AWS region for Bedrock service | `eu-west-2` |
| `AWS_ACCESS_KEY_ID` | AWS access key ID | Set via CDP secrets |
| `AWS_SECRET_ACCESS_KEY` | AWS secret access key | Set via CDP secrets |
| `CLAUDE_SONNET_MODEL_CONFIG` | Bedrock model configuration | `anthropic.claude-sonnet-4-6,arn:aws:bedrock:eu-west-2:123456789012:application-inference-profile/fake-profile-test` |

The `CLAUDE_SONNET_MODEL_CONFIG` format is: `model_id,inference_profile[,guardrail_id:guardrail_version]`

#### Reference Data Configuration

The `/reference/*` endpoints (see [API endpoints](#api-endpoints)) serve the option lists for the
metadata capture journey's radio/checkbox questions. Each list is config/env-var driven only - no
database, no admin UI. The following environment variables are **required**; like
`CLAUDE_SONNET_MODEL_CONFIG`, the application refuses to start if one is missing or malformed:

| Variable | Description | Example |
| --- | --- | --- |
| `REFERENCE_SCHEMES` | Options for "Which scheme does this guidance relate to?" | `Basic Payment Scheme|BPS,Countryside Stewardship` |
| `REFERENCE_AUDIENCES` | Options for "Who is this for?" | `Caseworker,Customer` |
| `REFERENCE_SYSTEMS` | Options for "What systems does this guidance relate to?" | `Siti Agri,CRM` |
| `REFERENCE_GUIDANCE_TYPES` | Options for the guidance type question | `Process guide` |

Each variable's format is a comma-separated list of `label` or `label|short label` entries, in display
order. Each endpoint returns that list as `[{"label": ..., "shortLabel": ...}, ...]`, omitting
`shortLabel` when none is set - see `/docs` for the generated OpenAPI schema.

- The **label** is the option's identity: documents store it and filters match on it. Labels must be
  unique ignoring case (startup fails otherwise). Include any abbreviation in the label itself, e.g.
  `Sustainable Farming Incentive (SFI)`, and don't rename a label in place - add the new one and
  retire the old, as stored guides and saved filters hold the old text.
- The **short label** is display-only (chips, table columns). It is never stored or filtered by, and
  need not be unique.
- Any other consumer-specific encoding (e.g. URL-friendly values for checkbox inputs) is derived by
  the consumer from the label.

### Linting and Formatting

This project uses [Ruff](https://github.com/astral-sh/ruff) for linting and formatting Python code.

#### Running Ruff

To run Ruff from the command line:

```bash
# Run linting with auto-fix
uv run ruff check . --fix

# Run formatting
uv run ruff format .
```

#### Pre-commit Hooks

This project uses [pre-commit](https://pre-commit.com/) to run linting and formatting checks automatically before each commit.

The pre-commit configuration is defined in `.pre-commit-config.yaml`

To set up pre-commit hooks:

```bash
# Set up the git hooks
pre-commit install
```

To run the hooks manually on all files:

```bash
pre-commit run --all-files
```

#### VS Code Configuration

For the best development experience, configure VS Code to use Ruff:

1. Install the [Ruff extension](https://marketplace.visualstudio.com/items?itemName=charliermarsh.ruff) for VS Code
2. Configure your VS Code settings (`.vscode/settings.json`):

```json
{
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
        "source.fixAll.ruff": "explicit",
        "source.organizeImports.ruff": "explicit"
    },
    "ruff.lint.run": "onSave",
    "[python]": {
        "editor.defaultFormatter": "charliermarsh.ruff",
        "editor.formatOnSave": true,
        "editor.codeActionsOnSave": {
            "source.fixAll.ruff": "explicit",
            "source.organizeImports.ruff": "explicit"
        }
    }
}
```

This configuration will:

- Format your code with Ruff when you save a file
- Fix linting issues automatically when possible
- Organize imports according to isort rules

#### Ruff Configuration

Ruff is configured in the `.ruff.toml` file

### Docker

This repository uses Docker throughput its lifecycle i.e. both for local development and the environments. A benefit of this is that environment variables & secrets are managed consistently throughout the lifecycle

See the `Dockerfile` and `compose.yml` for details

## Local development

### Setup & Configuration

Follow the convention below for environment variables and secrets in local development.

**Environment variables:** `.env` file in the project root.

**Compose directory:** The `compose/` directory contains helper scripts and environment configurations:
- `compose/floci/start.d/10-setup-resources.sh` - This script is used to set up the necessary AWS resources in the local Floci environment. It runs when the Floci container starts, ensuring that your local AWS environment is ready for development.

**Libraries:** Ensure the python virtual environment is configured and libraries are installed using `uv sync`, [as above](#python)

**Pre-Commit Hooks:** Ensure you install the pre-commit hooks, as above

### Development

This app can be run locally by either using the Docker Compose project or project scripts.

#### Using Docker Compose

To run the application using Docker Compose, you can use the following command:

```bash
docker compose up --build
```

If you want to enable hot-reloading, you can press the `w` key once the compose project is running to enable `watch` mode.

#### Running on the host

To run the application directly on the host with its dependencies in Docker:

```bash
# start the dependencies only
docker compose up -d floci mongodb redis

# start the application
uv run --env-file .env rpa-ai-guidance-hub-api
```

This loads configuration from `.env`, and enables hot-reload when `PYTHON_ENV=development`.

The service will then run on `http://localhost:8085`

### Testing

Ensure the python virtual environment is configured and libraries are installed using `uv sync`, [as above](#python)

Testing follows the [FastApi documented approach](https://fastapi.tiangolo.com/tutorial/testing/); using pytest & starlette.

```bash
uv run task test      # the full gate: lint, mypy, then pytest with coverage
uv run pytest         # tests only
uv run task format    # ruff format + ruff check --fix
uv run task lint      # ruff format --check + ruff check (no fixes)
uv run task typecheck # mypy over app/ and tests/
uv run task openapi   # regenerate docs/openapi.json (openapi-check verifies it is current)
```

**Docker is required.** Tests that touch MongoDB or S3 start real containers with
[testcontainers](https://testcontainers.com/) (`tests/support/`), so without a running Docker daemon
those tests fail rather than skip. Everything else runs offline.

**`tests/conftest.py` seeds the required environment variables** before any app import, and
`AppConfig` is a singleton, so a test that needs different config should mock
`app.config.get_config` rather than change env vars after import.

#### Auth tests

`tests/auth/` exercises the real authentication flow offline: a request goes through
`PyJWKClient`, signature and claim checks, and the caller check, and the test asserts the response
and the log lines. Nothing touches the network.

- The issuer's JWKS endpoint is played back by [vcrpy](https://vcrpy.readthedocs.io/) from the
  cassettes in `tests/auth/cassettes/` (`record_mode="none"`, so any unexpected request fails the test).
- The keys in `tests/auth/keys/` are **fake**, throwaway keys: they are labelled as such, protect
  nothing, and must never be used anywhere real. They sign the test tokens (`jwks.token("expired")`,
  `jwks.token("forged_signature")` ...; see `tests/auth/jwks.py`), and the cassettes serve their public
  halves. If you regenerate a key, regenerate its public JWK in every cassette that serves it.
- Guards are tested at every level they can be applied: app, router, `include_router`, route and
  handler parameter, plus stacked guards and the real app's wiring.

## Where code goes

### Schemas and models

Each layer knows only the one directly below it:

```text
HTTP JSON  <-  schemas.py   Pydantic: from_model() and conversions to models
                   |
               models.py    dataclasses and enums: to_document() / from_document()
                   |
               Mongo record (a dict)
```

- **`schemas.py`** holds the shapes that cross the HTTP boundary: request bodies,
  response bodies, and payloads other services send us (such as cdp-uploader's
  callback). They are Pydantic, which validates them, writes them into the OpenAPI
  document and spells them in camelCase on the wire. A schema converts to and from
  models, and knows nothing of how anything is stored.
- **`models.py`** holds what the service works with: plain dataclasses and enums,
  with no HTTP in them. A model converts itself to and from the record Mongo stores
  (`to_document()` and `from_document()`), so the stored field names are written
  down once. A model never imports a schema.
- **Routers** only call the conversions; they don't build shapes themselves.
- The role decides the file, not the library: `AppConfig` is Pydantic and lives in
  `config.py`.

### Ids

`app/guidance/ids.py` gives each kind of id a type of its own (`typing.NewType`), so
mypy refuses one where another belongs:

- **Ours**, `DocumentId` and `VersionId`, are uuids, version 7 (time-ordered), minted
  only by `new_document_id()` and `new_version_id()`. Mongo stores them as native
  uuids; they become text only as an S3 prefix or on the wire, where the OpenAPI
  document says `format: uuid`. A path id that isn't a uuid is refused with 422.
- **Other services'**, cdp-uploader's `UploadId` and `FileId`, are `str`: taken as
  given, not claimed to be uuids. So is the signed-in user's id.

### Guarding routes

Routes are guarded by a FastAPI dependency from `app/auth/service.py`, and the guard can go at any level:

```python
from app.auth import callers, service

app = fastapi.FastAPI(
    dependencies=[fastapi.Depends(service.allow_all_known())]
)  # whole app
router = fastapi.APIRouter(
    dependencies=[fastapi.Depends(service.allow_all_known())]
)  # one router


@router.get(
    "/thing",
    dependencies=[fastapi.Depends(service.allow(callers.KnownCallers.GUIDANCE_HUB_UI))],
)
async def thing() -> None: ...  # one route
```

- `allow_all_known()` admits every caller in `KnownCallers`; `allow(*names)` admits only those named.
  Stacking guards is safe: the most restrictive one wins.
- To add a caller, add it to `KnownCallers` in `app/auth/callers.py`. Names are checked when the route
  is declared, so a typo fails at import instead of returning 403s.
- As built, `app/entrypoints/fastapi.py` puts one guard on a router that holds every route except
  `/health`, and only when `USE_AUTH` is true. A new router should be included there, or guarded itself.
  `tests/auth/test_jwks_integration.py` fails if any documented operation answers a tokenless request
  with something other than 401.

## Authentication

The API can require callers to present a CDP web-identity token. It is **off by default**
(`USE_AUTH=false`) so local development needs nothing; turn it on with `USE_AUTH=true` plus
`CDP_JWT_ISSUER` and `CDP_JWT_JWKS_URI` (CDP supplies both in a deployed environment; set them in `.env` locally). When on, every route except `/health` and the generated
docs (`/docs`, `/redoc`, `/openapi.json`) needs one.

**What a caller sends:** `Authorization: Bearer <jwt>`.

**What the API checks, in order:**

1. The header is a bearer token.
2. The token's `kid` matches a key in the issuer's JWKS (fetched from `CDP_JWT_JWKS_URI` and cached;
   PyJWT refreshes it when it sees an unknown `kid`, at most once per cooldown).
3. The signature is valid, the algorithm is `RS256`, `iss` equals `CDP_JWT_ISSUER`, `aud` is
   `rpa_ai_guidance_hub_api`, and `exp` is present and in the future (`nbf`, if present, has passed).
4. The `sub` is a role ARN of the form `arn:aws:iam::<AWS_ACCOUNT>:role/<name>` where `<name>` is a
   known caller (currently `rpa-ai-guidance-hub-ui`), and that caller is allowed on the route.

**Responses.** They are deliberately generic, so a caller cannot probe which check failed; the
specific reason is only in the logs (`Auth rejected: ...`).

| Status | Detail | Meaning |
| --- | --- | --- |
| 401 | `Missing or malformed bearer token` | No header, wrong scheme, or empty token |
| 401 | `Token has expired` | `exp` has passed |
| 401 | `Invalid token` | Not a JWT, or no matching signing key |
| 403 | `Invalid token` | Bad signature, issuer, audience, algorithm, or a missing/immature claim |
| 403 | `Forbidden` | Valid token, but the subject is not a known caller, or the caller is not allowed on this route |
| 503 | `Unable to verify token at this time` | The JWKS endpoint is unreachable or returned something unusable |

Tokens are never logged, and neither are exceptions' tracebacks for auth failures.

## API endpoints

| Endpoint                        | Description                    |
|:--------------------------------| :----------------------------- |
| `GET: /docs`                    | Automatic API Swagger docs (the same document is committed as `docs/openapi.json`; regenerate with `uv run task openapi`) |
| `GET: /health`                  | Health check endpoint          |
| `GET: /reference/schemes`       | Scheme options for the "Which scheme does this guidance relate to?" radios |
| `GET: /reference/audiences`     | Audience options for the "Who is this for?" checkboxes |
| `GET: /reference/systems`       | System options for the "What systems...?" checkboxes |
| `GET: /reference/guidance-types`| Guidance type options |
| `GET: /review/assets`           | Simple example endpoint        |
| `GET: /guides`                  | A page of guides, newest first; `limit`, `cursor`, and repeatable `scheme`, `system`, `audience`, `guidance_type` filters |
| `POST: /guides`                 | Creates a guide (201)          |
| `GET: /guides/{id}`             | A guide and all its versions   |
| `GET: /guides/{id}/content`     | The Markdown of a guide's newest version, as plain text |
| `GET: /guides/{id}/assets/{name}` | Gets asset content by name   |
| `POST: /guides/staging/callback` | cdp-uploader callback for an uploaded file (202; processed in the background) |
| `GET: /guides/staging/{file_id}` | Conversion outcome of a staged file (404 if unknown) |

All routes except `/health` and the generated docs require a token when `USE_AUTH=true`, see
[Authentication](#authentication).

## Further reading

- [`docs/guidance-document-storage.md`](./docs/guidance-document-storage.md) - how a parsed guide is laid out in S3
- [`docs/markdown-dialect.md`](./docs/markdown-dialect.md) - the Markdown dialect a parsed guide is written in
- [`docs/openapi.json`](./docs/openapi.json) - the committed OpenAPI document

## Custom Cloudwatch Metrics

Uses the [aws embedded metrics library](https://github.com/awslabs/aws-embedded-metrics-python). An example can be found in `metrics.py`

In order to make this library work in the environments, the environment variable `AWS_EMF_ENVIRONMENT=local` is set in the app config. This tells the library to use the local cloudwatch agent that has been configured in CDP, and uses the environment variables set up in CDP `AWS_EMF_AGENT_ENDPOINT`, `AWS_EMF_LOG_GROUP_NAME`, `AWS_EMF_LOG_STREAM_NAME`, `AWS_EMF_NAMESPACE`, `AWS_EMF_SERVICE_NAME`

## Pipelines

### Dependabot

We have added an example dependabot configuration file to the repository. You can enable it by renaming
the [.github/example.dependabot.yml](.github/example.dependabot.yml) to `.github/dependabot.yml`

### SonarCloud

Instructions for setting up SonarCloud can be found in [sonar-project.properties](./sonar-project.properties)

## Licence

THIS INFORMATION IS LICENSED UNDER THE CONDITIONS OF THE OPEN GOVERNMENT LICENCE found at:

<http://www.nationalarchives.gov.uk/doc/open-government-licence/version/3>

The following attribution statement MUST be cited in your products and applications when using this information.

> Contains public sector information licensed under the Open Government license v3

### About the licence

The Open Government Licence (OGL) was developed by the Controller of Her Majesty's Stationery Office (HMSO) to enable
information providers in the public sector to license the use and re-use of their information under a common open
licence.

It is designed to encourage use and re-use of information freely and flexibly, with only a few conditions.
