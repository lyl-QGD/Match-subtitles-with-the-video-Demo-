import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from cryptography.exceptions import InvalidTag

from app.backup_cli import (
    _connection_args,
    _project_ref,
    _verify_remote_sha256,
    _verify_remote_sha256,
    decrypt_file,
    encrypt_stream,
    generate_key,
    restore,
    _validate_restore_target,
)


class BackupCliTests(unittest.TestCase):
    def setUp(self):
        import base64

        self.key = base64.urlsafe_b64decode(generate_key() + "==")

    def test_stream_encryption_round_trip_and_hash(self):
        original = (b"dialogue-backup-test" * 100_000) + b"end"
        with tempfile.TemporaryDirectory() as temp_dir:
            encrypted = Path(temp_dir) / "backup.aesgcm"
            decrypted = Path(temp_dir) / "backup.dump"
            with encrypted.open("wb") as target:
                count, plain_hash = encrypt_stream(io.BytesIO(original), target, self.key, len(original))
            restored_hash = decrypt_file(encrypted, decrypted, self.key, encrypted.stat().st_size)
            self.assertEqual(count, len(original))
            self.assertEqual(plain_hash, hashlib.sha256(original).hexdigest())
            self.assertEqual(restored_hash, plain_hash)
            self.assertEqual(decrypted.read_bytes(), original)

    def test_tampering_or_wrong_key_fails_and_removes_partial_plaintext(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            encrypted = Path(temp_dir) / "backup.aesgcm"
            decrypted = Path(temp_dir) / "backup.dump"
            with encrypted.open("wb") as target:
                encrypt_stream(io.BytesIO(b"sensitive dump"), target, self.key, 1024)
            raw = bytearray(encrypted.read_bytes())
            raw[20] ^= 0x01
            encrypted.write_bytes(raw)
            with self.assertRaises(InvalidTag):
                decrypt_file(encrypted, decrypted, bytes(reversed(self.key)), 1024)
            self.assertFalse(decrypted.exists())

    def test_stream_encryption_enforces_temporary_quota(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "backup.aesgcm"
            with output.open("wb") as target, self.assertRaisesRegex(RuntimeError, "quota"):
                encrypt_stream(io.BytesIO(b"12345"), target, self.key, 4)

    def test_pg_dump_arguments_never_contain_database_password(self):
        url = "postgresql://postgres.projectref1234567890:private-password@db.example.test:5432/postgres"
        args, env = _connection_args(url)
        self.assertNotIn("private-password", " ".join(args))
        self.assertEqual(env["PGPASSWORD"], "private-password")
        self.assertNotIn("DATABASE_URL", env)

    def test_project_reference_is_parsed_from_supabase_pooler_or_direct_host(self):
        ref = "projectref1234567890"
        self.assertEqual(_project_ref(f"postgresql://postgres.{ref}:secret@aws-1-us-east-1.pooler.supabase.com:5432/postgres"), ref)
        self.assertEqual(_project_ref(f"postgresql://postgres:secret@db.{ref}.supabase.co:5432/postgres"), ref)
        self.assertIsNone(_project_ref("postgresql://postgres:secret@localhost:5432/postgres"))

    def test_restore_requires_explicit_new_project_confirmation(self):
        with self.assertRaisesRegex(RuntimeError, "explicit_new_project_confirmation"):
            restore("backups/postgres/dialogue/test.dump.aesgcm", "projectref1234567890", False)

    def test_restore_refuses_source_or_mismatched_project_reference_before_connecting(self):
        source_ref = "projectref1234567890"
        other_ref = "otherprojectref12345"
        source = f"postgresql://postgres.{source_ref}:secret@aws.pooler.supabase.com:5432/postgres"
        same_target = f"postgresql://postgres.{source_ref}:secret@aws.pooler.supabase.com:5432/postgres"
        wrong_target = f"postgresql://postgres.{other_ref}:secret@aws.pooler.supabase.com:5432/postgres"
        with self.assertRaisesRegex(RuntimeError, "source_project_refused"):
            _validate_restore_target(same_target, source_ref, source)
        with self.assertRaisesRegex(RuntimeError, "project_ref_mismatch"):
            _validate_restore_target(wrong_target, source_ref, source)

    def test_remote_object_is_stream_hashed_and_mismatch_is_reported(self):
        payload = b"encrypted-object"

        class Body:
            def iter_chunks(self, chunk_size):
                self.chunk_size = chunk_size
                yield payload

            def close(self):
                pass

        class Client:
            def get_object(self, **_kwargs):
                return {"Body": Body()}

        digest = hashlib.sha256(payload).hexdigest()
        _verify_remote_sha256(Client(), "backup", "key", digest)
        with self.assertRaisesRegex(RuntimeError, "checksum_mismatch"):
            _verify_remote_sha256(Client(), "backup", "key", "0" * 64)

    def test_remote_object_is_stream_hashed_and_mismatch_is_reported(self):
        payload = b"encrypted-object"

        class Body:
            def iter_chunks(self, chunk_size):
                self.chunk_size = chunk_size
                yield payload

            def close(self):
                pass

        class Client:
            def get_object(self, **_kwargs):
                return {"Body": Body()}

        digest = hashlib.sha256(payload).hexdigest()
        _verify_remote_sha256(Client(), "backup", "key", digest)
        with self.assertRaisesRegex(RuntimeError, "checksum_mismatch"):
            _verify_remote_sha256(Client(), "backup", "key", "0" * 64)


if __name__ == "__main__":
    unittest.main()
