"""Guards the container's view of service endpoints.

compose.yml layers `environment:` on top of `env_file: .env`, and .env holds
host-oriented values so the service can also be run directly on the host. Any
such value that `environment:` does not name explicitly survives into the
container, where `localhost` means the container itself rather than the
dependency it is meant to reach.
"""

import pathlib

import yaml

SERVICE = "rpa-ai-guidance-hub-api"
COMPOSE_FILE = pathlib.Path(__file__).parent.parent / "compose.yml"


def service_environment():
    compose = yaml.safe_load(COMPOSE_FILE.read_text())
    return compose["services"][SERVICE]["environment"]


def test_floci_endpoint_is_pinned_to_the_floci_service():
    """app/common/s3.py reads FLOCI_ENDPOINT_URL, so the container needs it set.

    Without an explicit override the value falls through from .env, pointing
    boto3 at localhost:4566 inside the container and failing every S3 call.
    """
    endpoint = service_environment().get("FLOCI_ENDPOINT_URL")

    assert endpoint is not None, (
        "compose.yml does not set FLOCI_ENDPOINT_URL for the service, so the "
        "host-oriented value in .env reaches the container unchanged"
    )
    assert "localhost" not in endpoint, (
        f"FLOCI_ENDPOINT_URL={endpoint!r} points the container at itself; "
        "it must address the floci service"
    )
    assert "floci" in endpoint


def test_mongo_uri_is_pinned_to_the_mongodb_service():
    """The same rule, already honoured for mongo - kept so it stays that way."""
    mongo_uri = service_environment().get("MONGO_URI")

    assert mongo_uri is not None
    assert "localhost" not in mongo_uri
    assert "mongodb" in mongo_uri
