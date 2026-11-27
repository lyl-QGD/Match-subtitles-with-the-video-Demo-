import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock
from app.storage.r2 import S3StorageGateway, digest

class StorageTests(unittest.TestCase):
    def gateway(self):
        g = object.__new__(S3StorageGateway)
        g.buckets = {'originals', 'derived'}
        g.client = Mock()
        return g

    def test_reject_unapproved_bucket_and_traversal(self):
        g = self.gateway()
        for bucket, key in [('other','a'), ('originals','../a'), ('originals','/a')]:
            with self.assertRaises(ValueError):
                g.validate(bucket,key)

    def test_existing_mismatched_object_is_never_overwritten(self):
        g = self.gateway()
        g.head = Mock(return_value={'ContentLength':2,'Metadata':{'sha256':'wrong'}})
        with TemporaryDirectory() as folder:
            p = Path(folder)/'file'; p.write_bytes(b'ab')
            with self.assertRaises(ValueError):
                g.upload(p,'originals','key','text/plain',folder)
        g.client.create_multipart_upload.assert_not_called()

    def test_existing_verified_object_skips_upload(self):
        g = self.gateway()
        with TemporaryDirectory() as folder:
            p = Path(folder)/'file'; p.write_bytes(b'ab')
            sha = digest(p)
            g.head = Mock(return_value={'ContentLength':2,'Metadata':{'sha256':sha}})
            self.assertEqual(g.upload(p,'originals','key','text/plain',folder),(sha,2))
        g.client.create_multipart_upload.assert_not_called()
