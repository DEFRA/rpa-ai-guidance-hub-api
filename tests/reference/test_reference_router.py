import fastapi.testclient

import app.entrypoints.fastapi

client = fastapi.testclient.TestClient(app.entrypoints.fastapi.app)


class TestSchemes:
    def test_returns_the_configured_schemes_in_order(self):
        response = client.get("/reference/schemes")

        assert response.status_code == 200
        assert response.json() == [
            {"value": "basic-payment-scheme", "label": "Basic Payment Scheme"},
            {"value": "not-specific", "label": "Not scheme-specific"},
        ]


class TestAudiences:
    def test_returns_the_configured_audiences_in_order(self):
        response = client.get("/reference/audiences")

        assert response.status_code == 200
        assert response.json() == [
            {"value": "caseworker", "label": "Caseworker"},
            {"value": "customer", "label": "Customer"},
        ]


class TestSystems:
    def test_returns_the_configured_systems_in_order(self):
        response = client.get("/reference/systems")

        assert response.status_code == 200
        assert response.json() == [
            {"value": "siti-agri", "label": "Siti Agri"},
            {"value": "crm", "label": "CRM"},
        ]


class TestGuidanceTypes:
    def test_returns_the_configured_guidance_types(self):
        response = client.get("/reference/guidance-types")

        assert response.status_code == 200
        assert response.json() == [{"value": "process-guide", "label": "Process guide"}]
