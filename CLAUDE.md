# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Python/FastAPI backend service for the RPA AI Guidance Hub, built from DEFRA's CDP python backend template and deployed to the Core Delivery Platform (CDP). It runs on port 8085.

## Commands

```bash
uv sync                      # install deps (dev group included by default)
uv run task format           # ruff format + ruff check --fix
uv run task lint             # ruff format --check + ruff check (no fixes)
uv run task typecheck        # mypy over app/ and tests/
uv run task test             # lint + typecheck + pytest with coverage reports
uv run pytest                # tests only
uv run pytest tests/health/test_health_router.py::TestHealthProbe::test_health  # one test
pre-commit install           # git hooks (ruff + hygiene checks)
```

Running locally:

```bash
docker compose up --build                                # everything, including this service
docker compose up -d floci mongodb redis                 # dependencies only
uv run --env-file .env rpa-ai-guidance-hub-api           # service on host, deps in docker
```

Copy `.env.example` to `.env` first — `AppConfig` has required fields and refuses to start without them. `compose.debug.yaml` is an overlay that runs the app under debugpy (port 5678, waits for client).

## Gotchas

- **`app/` uses namespace packages — there are no `__init__.py` files.** pytest is configured with `--import-mode=importlib` and mypy with `explicit_package_bases`. Don't add `__init__.py` when creating a new subpackage.
- `tests/conftest.py` seeds required environment variables with `os.environ.setdefault` *before* any app import. `AppConfig` is a validate-on-construct singleton (`app.config.get_config()`), so tests that need different config must mock `get_config`, not mutate env vars after import.
- The ruff rev in `.pre-commit-config.yaml` must match the ruff version in `uv.lock`, or the hook's formatter fights `uv run task lint`.
- mypy runs with `disallow_untyped_defs` on `app/` (tests are exempt via a `[[tool.mypy.overrides]]` entry).
- `.env` holds *host-side* values (e.g. `FLOCI_ENDPOINT_URL=http://localhost:4566`). `compose.yml` layers `environment:` over `env_file: .env` to re-point them at the container network. `tests/test_compose_config.py` guards that these overrides stay in place — add one there whenever a new host-oriented endpoint enters `.env`.

## Architecture

- **Config** (`app/config.py`): pydantic-settings `AppConfig` read from environment/`.env`, cached in a module-level singleton behind `get_config()`. `CLAUDE_SONNET_MODEL_CONFIG` is a packed string — `model_id,inference_profile[,guardrail_id:guardrail_version]` — parsed by a field validator into `BedrockModelConfig`; a computed `bedrock` field exposes it as structured config. LLM access is AWS Bedrock via inference-profile ARNs with optional guardrails.
- **Entrypoint** (`app/entrypoints/fastapi.py`): builds the `app` instance, mounts routers and middleware, and exposes `main()` — the target of both `[project.scripts]` and the Dockerfile `CMD` (`/home/nonroot/.venv/bin/rpa-ai-guidance-hub-api`).
- **Routers**: one package per feature (`app/health/`, `app/review/`), each exporting an `APIRouter` that the entrypoint mounts. `GET /health` is required by CDP — do not remove it.
- **Shared plumbing** (`app/common/`): lazily-initialised global singletons exposed as FastAPI dependencies — `mongo.py` (async PyMongo client, custom CA from truststore env vars), `http_client.py`, `tracing.py` (propagates the `x-cdp-request-id` header), `metrics.py` (aws-embedded-metrics, gated by `ENABLE_METRICS`), `tls.py` (loads `TRUSTSTORE_*` env vars as CA certs, mirroring CDP's node convention).
- **Local AWS** is `floci` (LocalStack-style emulator) on port 4566 with dummy credentials. Add local buckets/queues to `compose/floci/start.d/10-setup-resources.sh`, which runs at container start (currently only commented examples). `compose/mongo/10-init.js` seeds MongoDB (also a placeholder).
- **Tests** (`tests/`) mirror the `app/` package layout. HTTP-level tests use Starlette's `TestClient` against the app; there is no running-dependency requirement for the unit suite.
- **Docker**: multi-stage `Dockerfile` with `development` and `production` targets on CDP's `defradigital/python*` base images. `LOG_CONFIG` selects the logging setup (`logging-dev.json` in development, `logging.json` — ECS-formatted — in production). In CDP, config and secrets come from CDP conventions, not `.env`.
