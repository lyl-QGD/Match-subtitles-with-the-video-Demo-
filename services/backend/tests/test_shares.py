import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient

from app.main import app


class FakeCursor:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self.responses.pop(0) if self.responses else FakeCursor()


class ShareTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.version_id = '00000000-0000-0000-0000-000000000010'
        self.revision_id = '00000000-0000-0000-0000-000000000011'
        self.segment_ids = [
            '00000000-0000-0000-0000-000000000001',
            '00000000-0000-0000-0000-000000000002',
        ]
        self.share_id = '00000000-0000-0000-0000-000000000020'
        self.clip_id = '00000000-0000-0000-0000-000000000021'
        self.now = datetime.now(timezone.utc)

    def _create_body(self):
        return {
            'version_id': self.version_id,
            'revision_id': self.revision_id,
            'segment_ids': self.segment_ids,
            'start_ms': 1000,
            'end_ms': 3000,
            'subtitle_mode': 'bilingual',
        }

    def _authorized_rights(self, allow_share=True):
        return FakeCursor(row=(1_350_000, self.revision_id, 'published', 'published',
                               True, allow_share, self.now, True))

    def test_create_share_persists_fixed_clip_and_only_token_hash(self):
        segment_rows = [
            (self.segment_ids[0], 10, 1000, 2000),
            (self.segment_ids[1], 11, 2000, 3000),
        ]
        db = FakeConnection([
            self._authorized_rights(),
            FakeCursor(rows=segment_rows),
            FakeCursor(row=(self.clip_id,)),
            FakeCursor(), FakeCursor(),
            FakeCursor(row=(self.share_id, self.now + timedelta(days=7))),
        ])
        with patch.dict('os.environ', {'APP_ENV': 'development'}), \
                patch('app.api.shares.missing_config', return_value=[]), \
                patch('app.api.shares.connect', return_value=db):
            response = self.client.post('/api/v1/shares', json=self._create_body())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['share_id'], self.share_id)
        self.assertEqual(body['expires_in_days'], 7)
        self.assertGreaterEqual(len(body['token']), 40)
        token_hash = hashlib.sha256(body['token'].encode()).hexdigest()
        share_insert = next((sql, params) for sql, params in db.calls if 'INSERT INTO dialogue.shares' in sql)
        self.assertIn('token_hash', share_insert[0])
        self.assertIn(token_hash, share_insert[1])
        self.assertNotIn(body['token'], repr(db.calls))
        self.assertIn('INSERT INTO dialogue.clips', '\n'.join(sql for sql, _ in db.calls))
        self.assertEqual(sum('INSERT INTO dialogue.clip_segments' in sql for sql, _ in db.calls), 2)

    def test_share_creation_is_denied_when_rights_do_not_allow_share(self):
        db = FakeConnection([self._authorized_rights(allow_share=False)])
        with patch.dict('os.environ', {'APP_ENV': 'development'}), \
                patch('app.api.shares.missing_config', return_value=[]), \
                patch('app.api.shares.connect', return_value=db):
            response = self.client.post('/api/v1/shares', json=self._create_body())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['detail']['code'], 'sharing_not_authorized')
        self.assertEqual(len(db.calls), 1)

    def test_share_creation_rejects_non_contiguous_segments(self):
        db = FakeConnection([
            self._authorized_rights(),
            FakeCursor(rows=[
                (self.segment_ids[0], 10, 1000, 2000),
                (self.segment_ids[1], 12, 2000, 3000),
            ]),
        ])
        with patch.dict('os.environ', {'APP_ENV': 'development'}), \
                patch('app.api.shares.missing_config', return_value=[]), \
                patch('app.api.shares.connect', return_value=db):
            response = self.client.post('/api/v1/shares', json=self._create_body())
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['detail']['code'], 'non_contiguous_segments')

    def test_resolve_invalid_and_revoked_links(self):
        invalid = self.client.post('/api/v1/shares/resolve', json={'token': 'not-a-valid-token'})
        self.assertEqual(invalid.status_code, 404)
        self.assertEqual(invalid.json()['detail']['code'], 'share_invalid')

        db = FakeConnection([FakeCursor(row=(self.share_id, self.clip_id, 'revoked', False))])
        with patch('app.api.shares.connect', return_value=db):
            revoked = self.client.post('/api/v1/shares/resolve', json={'token': 'A' * 43})
        self.assertEqual(revoked.status_code, 410)
        self.assertEqual(revoked.json()['detail']['code'], 'share_revoked')
        self.assertEqual(len(db.calls), 1)

    def test_resolve_expired_share(self):
        db = FakeConnection([FakeCursor(row=(self.share_id, self.clip_id, 'active', True))])
        with patch('app.api.shares.connect', return_value=db):
            response = self.client.post('/api/v1/shares/resolve', json={'token': 'B' * 43})
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json()['detail']['code'], 'share_expired')

    def test_resolve_signs_fixed_revision_and_rechecks_rights(self):
        share_token = 'C' * 43
        clip_row = (
            self.clip_id, self.version_id, self.revision_id, 1000, 3000, 'bilingual',
            'tbbt', '生活大爆炸', 'The Big Bang Theory', 1, 1, 'Pilot', 1_350_000,
            'dialogue-mvp-originals', 'series/s01/e01.mp4', 'video/mp4',
        )
        subtitle_rows = [
            (self.segment_ids[0], 10, 1000, 2000, 'Hello.', '你好。', self.version_id, self.revision_id, 'approved'),
            (self.segment_ids[1], 11, 2000, 3000, 'How are you?', '你好吗？', self.version_id, self.revision_id, 'approved'),
        ]
        db = FakeConnection([
            FakeCursor(row=(self.share_id, self.clip_id, 'active', False)),
            FakeCursor(row=clip_row),
            FakeCursor(rows=subtitle_rows),
        ])
        storage = unittest.mock.MagicMock()
        storage.sign_read.return_value = 'https://signed.example/media'
        with patch('app.api.shares.connect', return_value=db), \
                patch('app.api.shares.S3StorageGateway', return_value=storage):
            response = self.client.post('/api/v1/shares/resolve', json={'token': share_token})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['start_ms'], 1000)
        self.assertEqual(body['end_ms'], 3000)
        self.assertEqual(body['revision_id'], self.revision_id)
        self.assertEqual(len(body['segments']), 2)
        self.assertEqual(body['media_url'], 'https://signed.example/media')
        storage.sign_read.assert_called_once_with('dialogue-mvp-originals', 'series/s01/e01.mp4', ttl=900)
        self.assertIn('rights.allow_share IS TRUE', db.calls[1][0])
        self.assertIn("sr.status IN ('published', 'archived')", db.calls[1][0])
        self.assertNotIn('v.active_revision_id = c.revision_id', db.calls[1][0])
        self.assertEqual(db.calls[0][1], (hashlib.sha256(share_token.encode()).hexdigest(),))

    def test_revoke_and_list_shares_are_local_management_only(self):
        db = FakeConnection([
            FakeCursor(rows=[(self.share_id, 'active', self.now, self.now + timedelta(days=7),
                              1000, 3000, 'tbbt', 1, 1, 'active')]),
            FakeCursor(row=('revoked',)),
        ])
        with patch.dict('os.environ', {'APP_ENV': 'development'}), \
                patch('app.api.shares.missing_config', return_value=[]), \
                patch('app.api.shares.connect', return_value=db):
            listing = self.client.get('/api/v1/shares/manage')
            revoked = self.client.post(f'/api/v1/shares/manage/{self.share_id}/revoke')
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()['shares'][0]['status'], 'active')
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(revoked.json()['status'], 'revoked')


if __name__ == '__main__':
    unittest.main()
