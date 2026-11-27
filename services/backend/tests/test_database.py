import unittest
from unittest.mock import patch
from app.database import database_url, database_health

class DatabaseTests(unittest.TestCase):
    def test_api_url_rejected(self):
        with patch('app.database.missing_config'), patch.dict('os.environ', {'DATABASE_URL': 'https://project.supabase.co'}):
            with self.assertRaises(ValueError):
                database_url()

    def test_password_placeholder_rejected(self):
        with patch('app.database.missing_config'), patch.dict('os.environ', {'DATABASE_URL': 'postgresql://user:[password]@example.com/postgres'}):
            with self.assertRaises(ValueError):
                database_url()

    def test_missing_config_does_not_connect(self):
        with patch('app.database.missing_config', return_value=['DATABASE_URL']), patch('app.database.pool') as pool:
            self.assertEqual(database_health()['status'], 'missing_configuration')
            pool.assert_not_called()

    def test_invalid_config_does_not_leak(self):
        with patch('app.database.missing_config', return_value=[]), patch('app.database.pool', side_effect=ValueError('secret')):
            self.assertEqual(database_health(), {'status': 'invalid_configuration'})
