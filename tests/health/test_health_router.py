import fastapi.testclient

import app.entrypoints.fastapi

client = fastapi.testclient.TestClient(app.entrypoints.fastapi.app)


class TestHealthProbe:
    """Test that API documentation is available."""

    def test_health(self):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
