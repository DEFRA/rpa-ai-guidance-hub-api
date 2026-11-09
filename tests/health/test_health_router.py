import types
from unittest import mock

import fastapi.testclient

import app.entrypoints.fastapi

client = fastapi.testclient.TestClient(app.entrypoints.fastapi.app)


def _a_consumer(*, healthy: bool):
    return mock.patch.object(
        app.entrypoints.fastapi.app.state,
        "queue_consumer",
        types.SimpleNamespace(healthy=healthy),
        create=True,
    )


class TestHealthProbe:
    """Test that API documentation is available."""

    def test_health(self):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_health_does_not_depend_on_the_queue(self):
        """CDP restarts a container that fails this, which cannot fix a queue, and
        would stop everything else the API does."""
        with _a_consumer(healthy=False):
            response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestSqsWorkerStatus:
    def test_is_ok_while_the_queue_can_be_worked(self):
        with _a_consumer(healthy=True):
            response = client.get("/health/sqs-worker/status")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_is_unavailable_while_the_queue_has_a_problem(self):
        with _a_consumer(healthy=False):
            response = client.get("/health/sqs-worker/status")

        assert response.status_code == 503
        assert response.json() == {"status": "queue unavailable"}
