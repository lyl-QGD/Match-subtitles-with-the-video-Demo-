import unittest
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient

from app.main import app


class FakeCursor:
    def __init__(self, rows=None, row=None):
        self.rows = rows or []
        self.row = row

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, rows=None, row=None):
        self.rows = rows or []
        self.row = row
        self.sql = ''
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.sql = sql
        self.calls.append((sql, params))
        if 'ORDER BY random() LIMIT 9' in sql or 'ORDER BY s.seq' in sql:
            return FakeCursor(rows=self.rows)
        return FakeCursor(row=self.row, rows=self.rows)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_featured_returns_nine_random_authorized_segments(self):
        row = (
            'segment-id', 'tbbt', '生活大爆炸', 'The Big Bang Theory', 1, 1, 'Pilot', 1,
            2380, 5000, 'English dialogue', '中文台词', '', '', 'Next English', '下一句',
        )
        db = FakeConnection(rows=[row] * 9)
        with patch('app.api.catalog.connect', return_value=db):
            response = self.client.get('/api/v1/featured')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['results']), 9)
        self.assertIn("ORDER BY random() LIMIT 9", db.sql)
        self.assertIn("v.status = 'published'", db.sql)
        self.assertIn('rights.allow_preview IS TRUE', db.sql)
        self.assertIn("s.review_status = 'approved'", db.sql)

    def test_playback_signs_only_an_authorized_segment(self):
        version_id = '00000000-0000-0000-0000-000000000010'
        revision_id = '00000000-0000-0000-0000-000000000011'
        segment_id = '00000000-0000-0000-0000-000000000001'
        row = (segment_id, version_id, revision_id, 12, 2380, 5000,
               'dialogue-mvp-originals', 'series/s01/e01.mp4', 'video/mp4', 1_350_000, False)
        cue_rows = [
            (segment_id, version_id, revision_id, 12, 2380, 5000, 'English', '中文'),
            ('00000000-0000-0000-0000-000000000002', version_id, revision_id, 13, 5200, 6200, 'Next', '下一句'),
        ]
        db = FakeConnection(rows=cue_rows, row=row)
        storage = unittest.mock.MagicMock()
        storage.sign_read.return_value = 'https://signed.example/video'
        with patch('app.api.catalog.connect', return_value=db), patch('app.api.catalog.S3StorageGateway', return_value=storage):
            response = self.client.get(f'/api/v1/segments/{segment_id}/playback')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['start_ms'], 2380)
        self.assertEqual(body['media_url'], 'https://signed.example/video')
        self.assertEqual(body['duration_ms'], 1_350_000)
        self.assertEqual(body['version_id'], version_id)
        self.assertEqual(body['revision_id'], revision_id)
        self.assertFalse(body['allow_share'])
        self.assertEqual(len(body['segments']), 2)
        storage.sign_read.assert_called_once_with('dialogue-mvp-originals', 'series/s01/e01.mp4', ttl=900)
        self.assertIn('rights.allow_preview IS TRUE', db.sql)
        self.assertIn('s.version_id = %s', db.sql)
        self.assertIn('s.revision_id = %s', db.sql)

    def test_playback_does_not_sign_unavailable_segment(self):
        db = FakeConnection(row=None)
        storage = unittest.mock.MagicMock()
        with patch('app.api.catalog.connect', return_value=db), patch('app.api.catalog.S3StorageGateway', return_value=storage):
            response = self.client.get('/api/v1/segments/00000000-0000-0000-0000-000000000001/playback')
        self.assertEqual(response.status_code, 404)
        storage.sign_read.assert_not_called()

    def test_featured_database_failure_is_not_an_empty_catalog(self):
        with patch('app.api.catalog.connect', side_effect=psycopg.OperationalError('private details')):
            response = self.client.get('/api/v1/featured')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['detail']['code'], 'database_unavailable')
        self.assertNotIn('private details', response.text)


if __name__ == '__main__':
    unittest.main()
