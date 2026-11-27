import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.config import REQUIRED

class HealthTests(unittest.TestCase):
    def test_ready_only_when_both_cloud_services_pass(self):
        with patch('app.main.missing_config', return_value=[]), patch('app.main.database_health', return_value={'status':'ok'}), patch('app.main.storage_health', return_value={'status':'ok'}):
            response = TestClient(app).get('/health/ready')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['status'], 'ready')

    def test_liveness_without_cloud(self):
        self.assertEqual(TestClient(app).get("/health/live").status_code, 200)

    def test_readiness_reports_missing_names_without_values(self):
        with patch.dict(os.environ, {key: "" for key in REQUIRED}), patch("app.config.load_dotenv"):
            response = TestClient(app).get("/health/ready")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(set(response.json()["missing"]), set(REQUIRED))

    def test_configuration_does_not_imply_connected(self):
        with patch.dict(os.environ, {key: "secret-test-value" for key in REQUIRED}), patch("app.main.database_health", return_value={"status": "invalid_configuration"}):
            response = TestClient(app).get("/health/ready")
            self.assertEqual(response.status_code, 503)
            self.assertFalse(response.json()["connectivity_checked"])
            self.assertNotIn("secret-test-value", response.text)
