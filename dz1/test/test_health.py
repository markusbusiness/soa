import unittest

from fastapi.testclient import TestClient

from src.catalog_service import app


class HealthEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health_returns_200_and_service_status(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ok", "service": "catalog-service"},
        )


if __name__ == "__main__":
    unittest.main()
