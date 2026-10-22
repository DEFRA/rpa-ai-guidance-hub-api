import fastapi.testclient

import app.entrypoints.fastapi

client = fastapi.testclient.TestClient(app.entrypoints.fastapi.app)


class TestSchemes:
    def test_returns_the_configured_schemes_in_order(self):
        response = client.get("/reference/schemes")

        assert response.status_code == 200
        assert response.json() == [
            {"label": "Basic Payment Scheme", "shortLabel": "BPS"},
            {"label": "Countryside Stewardship"},
        ]


class TestAudiences:
    def test_returns_the_configured_audiences_in_order(self):
        response = client.get("/reference/audiences")

        assert response.status_code == 200
        assert response.json() == [{"label": "Caseworker"}, {"label": "Customer"}]


class TestSystems:
    def test_returns_the_configured_systems_in_order(self):
        response = client.get("/reference/systems")

        assert response.status_code == 200
        assert response.json() == [{"label": "Siti Agri"}, {"label": "CRM"}]


class TestGuidanceTypes:
    def test_returns_the_configured_guidance_types(self):
        response = client.get("/reference/guidance-types")

        assert response.status_code == 200
        assert response.json() == [{"label": "Process guide"}]
