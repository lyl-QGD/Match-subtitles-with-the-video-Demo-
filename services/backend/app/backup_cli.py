"""Encrypted, local-operator database backups for the dialogue schema.

The archive contains the application-owned ``dialogue`` schema only. It does
not contain Supabase-managed schemas or the media objects stored in R2.
Credentials and encryption keys are never printed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.error import URLError
from urllib.request import Request, urlopen

import boto3
import psycopg
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
from psycopg.conninfo import conninfo_to_dict

from app.config import ROOT

MAGIC = b"DLB1"
NONCE_SIZE = 12
TAG_SIZE = 16
CHUNK_SIZE = 1024 * 1024
BACKUP_PREFIX = "backups/postgres/dialogue/"


def _load_environment() -> None:
    load_dotenv(ROOT / ".env", override=False)


def _required(*names: str) -> dict[str, str]:
    missing = [name for name in names if not os.getenv(name, "").strip()]
    if missing:
        raise RuntimeError("missing_configuration:" + ",".join(missing))
    return {name: os.environ[name].strip() for name in names}


def _encryption_key() -> bytes:
    value = _required("BACKUP_ENCRYPTION_KEY")["BACKUP_ENCRYPTION_KEY"]
    padded = value + ("=" * (-len(value) % 4))
    try:
        key = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeError):
        raise RuntimeError("invalid_backup_encryption_key") from None
    if len(key) != 32:
        raise RuntimeError("backup_encryption_key_must_be_32_bytes")
    return key


def generate_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def _crypto_types():
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        raise RuntimeError(
            "missing_dependency: install services/backend/requirements.lock in .venv"
        ) from None
    return Cipher, algorithms, modes, InvalidTag


def encrypt_stream(source, target, key: bytes, max_bytes: int) -> tuple[int, str]:
    """Stream AES-256-GCM encryption without materializing the dump in RAM."""
    Cipher, algorithms, modes, _ = _crypto_types()
    nonce = secrets.token_bytes(NONCE_SIZE)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(MAGIC)
    target.write(MAGIC + nonce)
    total = 0
    digest = hashlib.sha256()
    while True:
        block = source.read(CHUNK_SIZE)
        if not block:
            break
        total += len(block)
        if total > max_bytes:
            raise RuntimeError("temporary_storage_quota_exceeded")
        digest.update(block)
        target.write(encryptor.update(block))
    target.write(encryptor.finalize())
    target.write(encryptor.tag)
    target.flush()
    os.fsync(target.fileno())
    return total, digest.hexdigest()


def decrypt_file(source_path: Path, target_path: Path, key: bytes, max_bytes: int) -> str:
    """Authenticate and decrypt a backup; delete partial plaintext on failure."""
    Cipher, algorithms, modes, InvalidTag = _crypto_types()
    source_path = Path(source_path)
    target_path = Path(target_path)
    file_size = source_path.stat().st_size
    if file_size < len(MAGIC) + NONCE_SIZE + TAG_SIZE or file_size > max_bytes:
        raise RuntimeError("invalid_or_oversized_backup")
    with source_path.open("rb") as source:
        header = source.read(len(MAGIC) + NONCE_SIZE)
        if header[:len(MAGIC)] != MAGIC:
            raise RuntimeError("unsupported_backup_format")
        nonce = header[len(MAGIC):]
        ciphertext_size = file_size - len(header) - TAG_SIZE
        source.seek(file_size - TAG_SIZE)
        tag = source.read(TAG_SIZE)
        source.seek(len(header))
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(MAGIC)
        digest = hashlib.sha256()
        remaining = ciphertext_size
        try:
            with target_path.open("wb") as target:
                while remaining:
                    block = source.read(min(CHUNK_SIZE, remaining))
                    if not block:
                        raise RuntimeError("truncated_backup")
                    remaining -= len(block)
                    plain = decryptor.update(block)
                    digest.update(plain)
                    target.write(plain)
                final = decryptor.finalize()
                digest.update(final)
                target.write(final)
                target.flush()
                os.fsync(target.fileno())
        except (InvalidTag, OSError, RuntimeError):
            target_path.unlink(missing_ok=True)
            raise
    return digest.hexdigest()


def _max_temp_bytes() -> int:
    try:
        quota_gb = float(os.getenv("TEMP_QUOTA_GB", "2"))
    except ValueError:
        raise RuntimeError("invalid_TEMP_QUOTA_GB") from None
    if quota_gb <= 0:
        raise RuntimeError("invalid_TEMP_QUOTA_GB")
    return int(quota_gb * 1024**3)


def _db_info(database_url: str) -> dict[str, int | str]:
    try:
        with psycopg.connect(database_url, sslmode="require", connect_timeout=8) as conn:
            size, name, server_version = conn.execute(
                "SELECT pg_database_size(current_database()), current_database(), current_setting('server_version')"
            ).fetchone()
            schema_exists = conn.execute(
                "SELECT to_regnamespace('dialogue') IS NOT NULL"
            ).fetchone()[0]
            if not schema_exists:
                raise RuntimeError("dialogue_schema_not_found")
            return {"database_bytes": int(size), "database_name": name, "server_version": server_version}
    except psycopg.Error:
        raise RuntimeError("database_connection_failed") from None


def _connection_args(database_url: str) -> tuple[list[str], dict[str, str]]:
    try:
        params = conninfo_to_dict(database_url)
    except Exception:
        raise RuntimeError("invalid_database_configuration") from None
    host = params.get("host")
    user = params.get("user")
    database = params.get("dbname")
    if not host or not user or not database or not params.get("password"):
        raise RuntimeError("invalid_database_configuration")
    args = [
        "--host", host,
        "--port", str(params.get("port", "5432")),
        "--username", user,
        "--dbname", database,
        "--no-password",
    ]
    child_env = os.environ.copy()
    for name in list(child_env):
        if name in ("DATABASE_URL", "RESTORE_DATABASE_URL", "MIGRATION_DATABASE_URL") \
                or name.startswith(("S3_", "BACKUP_", "CF_", "SUPABASE_")):
            child_env.pop(name, None)
    child_env["PGPASSWORD"] = params["password"]
    child_env["PGSSLMODE"] = "require"
    child_env["PGCONNECT_TIMEOUT"] = "8"
    return args, child_env


def _backup_s3():
    cfg = _required(
        "BACKUP_BUCKET", "BACKUP_S3_ACCESS_KEY_ID", "BACKUP_S3_SECRET_ACCESS_KEY"
    )
    endpoint = (os.getenv("BACKUP_S3_ENDPOINT") or os.getenv("S3_ENDPOINT", "")).strip().rstrip("/")
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".r2.cloudflarestorage.com") or parsed.path or parsed.query:
        raise RuntimeError("invalid_backup_r2_endpoint")
    protected_buckets = {
        os.getenv("S3_ORIGINALS_BUCKET", "").strip(),
        os.getenv("S3_DERIVED_BUCKET", "").strip(),
    }
    if cfg["BACKUP_BUCKET"] in protected_buckets:
        raise RuntimeError("backup_bucket_must_be_separate")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=os.getenv("BACKUP_S3_REGION", "auto"),
        aws_access_key_id=cfg["BACKUP_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=cfg["BACKUP_S3_SECRET_ACCESS_KEY"],
        config=Config(
            signature_version="s3v4",
            connect_timeout=10,
            read_timeout=120,
            retries={"max_attempts": 3},
            s3={"addressing_style": "path"},
        ),
    )
    return client, cfg["BACKUP_BUCKET"]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(CHUNK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_remote_sha256(client, bucket: str, object_key: str, expected_sha256: str) -> None:
    try:
        response = client.get_object(Bucket=bucket, Key=object_key)
        body = response["Body"]
        digest = hashlib.sha256()
        try:
            for block in body.iter_chunks(chunk_size=CHUNK_SIZE):
                digest.update(block)
        finally:
            body.close()
    except (ClientError, BotoCoreError, KeyError):
        raise RuntimeError("backup_remote_checksum_read_failed") from None
    if digest.hexdigest() != expected_sha256:
        raise RuntimeError("backup_remote_checksum_mismatch")


def _dump_command(binary: str, database_url: str) -> tuple[list[str], dict[str, str]]:
    connection_args, child_env = _connection_args(database_url)
    command = [
        binary, "--format=custom", "--no-owner", "--no-acl",
        "--schema=dialogue", *connection_args,
    ]
    return command, child_env


def backup() -> dict[str, object]:
    cfg = _required("DATABASE_URL", "BACKUP_ENCRYPTION_KEY")
    key = _encryption_key()
    max_bytes = _max_temp_bytes()
    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        raise RuntimeError("pg_dump_not_found_install_postgresql_client_tools")
    db = _db_info(cfg["DATABASE_URL"])
    if db["database_bytes"] > max_bytes:
        raise RuntimeError("database_exceeds_TEMP_QUOTA_GB")
    if db["database_bytes"] + 2 * 1024 * 1024 > shutil.disk_usage(tempfile.gettempdir()).free:
        raise RuntimeError("insufficient_temporary_disk_space")

    client, bucket = _backup_s3()
    command, child_env = _dump_command(pg_dump, cfg["DATABASE_URL"])
    now = datetime.now(timezone.utc)
    object_key = f"{BACKUP_PREFIX}{now:%Y/%m/%d}/{now:%H%M%SZ}-{uuid.uuid4().hex}.dump.aesgcm"

    with tempfile.TemporaryDirectory(prefix="dialogue-db-backup-") as temp_dir:
        encrypted_path = Path(temp_dir) / "dialogue.dump.aesgcm"
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child_env,
        )
        assert process.stdout is not None and process.stderr is not None
        stderr_drain = threading.Thread(target=lambda: process.stderr.read(), daemon=True)
        stderr_drain.start()
        try:
            with encrypted_path.open("wb") as target:
                dump_bytes, plain_sha = encrypt_stream(process.stdout, target, key, max_bytes)
            return_code = process.wait(timeout=3600)
        except BaseException:
            process.kill()
            process.wait(timeout=10)
            encrypted_path.unlink(missing_ok=True)
            raise
        finally:
            stderr_drain.join(timeout=2)
        if return_code != 0 or dump_bytes == 0:
            encrypted_path.unlink(missing_ok=True)
            raise RuntimeError("pg_dump_failed_or_returned_empty_archive")
        encrypted_sha = _sha256_file(encrypted_path)
        encrypted_size = encrypted_path.stat().st_size
        if encrypted_size > max_bytes:
            encrypted_path.unlink(missing_ok=True)
            raise RuntimeError("temporary_storage_quota_exceeded")
        metadata = {
            "format": "dialogue-pg-custom-aesgcm-v1",
            "sha256": encrypted_sha,
            "plain-sha256": plain_sha,
            "schema": "dialogue",
            "created-at": now.isoformat(),
        }
        try:
            with encrypted_path.open("rb") as body:
                client.upload_fileobj(
                    body,
                    bucket,
                    object_key,
                    ExtraArgs={"ContentType": "application/octet-stream", "Metadata": metadata},
                )
            head = client.head_object(Bucket=bucket, Key=object_key)
        except (ClientError, BotoCoreError):
            raise RuntimeError("backup_upload_or_verification_failed") from None
        if head.get("ContentLength") != encrypted_size or head.get("Metadata", {}).get("sha256") != encrypted_sha:
            raise RuntimeError("backup_remote_verification_failed")
        _verify_remote_sha256(client, bucket, object_key, encrypted_sha)
        return {
            "status": "uploaded_and_verified",
            "bucket": bucket,
            "object_key": object_key,
            "archive_bytes": dump_bytes,
            "encrypted_bytes": encrypted_size,
            "sha256": encrypted_sha,
            "remote_checksum_verified": True,
            "schema": "dialogue",
            "server_version": db["server_version"],
        }


def list_backups() -> dict[str, object]:
    client, bucket = _backup_s3()
    try:
        paginator = client.get_paginator("list_objects_v2")
        objects = []
        for page in paginator.paginate(Bucket=bucket, Prefix=BACKUP_PREFIX):
            for item in page.get("Contents", []):
                objects.append({
                    "object_key": item["Key"],
                    "bytes": item["Size"],
                    "last_modified": item["LastModified"].astimezone(timezone.utc).isoformat(),
                })
    except (ClientError, BotoCoreError):
        raise RuntimeError("backup_listing_failed") from None
    return {"status": "ok", "bucket": bucket, "count": len(objects), "backups": sorted(objects, key=lambda x: x["last_modified"], reverse=True)}


def _project_ref(database_url: str) -> str | None:
    parsed = urlsplit(database_url)
    username = unquote(parsed.username or "")
    match = re.fullmatch(r"postgres\.([a-z0-9]{16,30})", username)
    if match:
        return match.group(1)
    host_match = re.fullmatch(r"db\.([a-z0-9]{16,30})\.supabase\.co", parsed.hostname or "")
    return host_match.group(1) if host_match else None


def _validate_restore_target(target_url: str, expected_ref: str, source_url: str) -> None:
    target_ref = _project_ref(target_url)
    if not re.fullmatch(r"[a-z0-9]{16,30}", expected_ref) or target_ref != expected_ref:
        raise RuntimeError("restore_target_project_ref_mismatch")
    if _project_ref(source_url) == target_ref:
        raise RuntimeError("restore_to_source_project_refused")
    try:
        with psycopg.connect(target_url, sslmode="require", connect_timeout=8) as conn:
            exists = conn.execute(
                "SELECT to_regnamespace('dialogue') IS NOT NULL"
            ).fetchone()[0]
            if exists:
                raise RuntimeError("restore_target_already_has_dialogue_schema")
            has_trgm = conn.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='pg_trgm')"
            ).fetchone()[0]
            if not has_trgm:
                raise RuntimeError(
                    "restore_target_missing_pg_trgm_enable_extension_before_restore"
                )
    except psycopg.Error:
        raise RuntimeError("restore_target_connection_failed") from None


def restore(object_key: str, target_project_ref: str, confirmed_new_project: bool) -> dict[str, object]:
    if not confirmed_new_project:
        raise RuntimeError("restore_requires_explicit_new_project_confirmation")
    cfg = _required("DATABASE_URL", "RESTORE_DATABASE_URL", "BACKUP_ENCRYPTION_KEY")
    target_url = cfg["RESTORE_DATABASE_URL"]
    _validate_restore_target(target_url, target_project_ref, cfg["DATABASE_URL"])
    client, bucket = _backup_s3()
    if not object_key.startswith(BACKUP_PREFIX) or not object_key.endswith(".dump.aesgcm"):
        raise RuntimeError("invalid_backup_object_key")
    max_bytes = _max_temp_bytes()
    key = _encryption_key()
    pg_restore = shutil.which("pg_restore")
    if not pg_restore:
        raise RuntimeError("pg_restore_not_found_install_postgresql_client_tools")

    with tempfile.TemporaryDirectory(prefix="dialogue-db-restore-") as temp_dir:
        encrypted_path = Path(temp_dir) / "download.dump.aesgcm"
        plain_path = Path(temp_dir) / "dialogue.dump"
        try:
            head = client.head_object(Bucket=bucket, Key=object_key)
        except (ClientError, BotoCoreError):
            raise RuntimeError("backup_metadata_read_failed") from None
        encrypted_size = int(head.get("ContentLength", 0))
        if encrypted_size <= 0 or encrypted_size * 2 > max_bytes:
            raise RuntimeError("temporary_storage_quota_exceeded")
        if encrypted_size * 2 > shutil.disk_usage(temp_dir).free:
            raise RuntimeError("insufficient_temporary_disk_space")
        try:
            client.download_file(bucket, object_key, str(encrypted_path))
        except (ClientError, BotoCoreError):
            raise RuntimeError("backup_download_failed") from None
        if encrypted_path.stat().st_size != encrypted_size:
            raise RuntimeError("backup_download_size_mismatch")
        encrypted_sha = _sha256_file(encrypted_path)
        expected_sha = head.get("Metadata", {}).get("sha256")
        if not expected_sha or encrypted_sha != expected_sha:
            raise RuntimeError("backup_checksum_mismatch")
        plain_sha = decrypt_file(encrypted_path, plain_path, key, max_bytes)
        expected_plain_sha = head.get("Metadata", {}).get("plain-sha256")
        if expected_plain_sha and plain_sha != expected_plain_sha:
            raise RuntimeError("backup_plaintext_checksum_mismatch")

        listing = subprocess.run(
            [pg_restore, "--list", str(plain_path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if listing.returncode != 0 or b"SCHEMA - dialogue" not in listing.stdout:
            raise RuntimeError("backup_archive_validation_failed")

        args, child_env = _connection_args(target_url)
        command = [
            pg_restore,
            "--no-owner", "--no-acl", "--exit-on-error", "--single-transaction",
            *args,
            str(plain_path),
        ]
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=child_env,
            check=False,
            timeout=3600,
        )
        if result.returncode != 0:
            raise RuntimeError("pg_restore_failed_target_may_be_partially_restored")
        with psycopg.connect(target_url, sslmode="require", connect_timeout=8) as conn:
            table_count = conn.execute(
                "SELECT count(*) FROM pg_tables WHERE schemaname='dialogue'"
            ).fetchone()[0]
            if table_count < 10:
                raise RuntimeError("restore_verification_failed")
    return {
        "status": "restored_and_verified",
        "target_project_ref": target_project_ref,
        "object_key": object_key,
        "dialogue_tables": table_count,
        "sha256": plain_sha,
    }


def _cloudflare_bucket_metrics(bucket: str, days: int, account_id: str, token: str) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    end = now.isoformat().replace("+00:00", "Z")
    query = """
    query R2Usage($accountTag: string!, $startDate: Time!, $endDate: Time!, $bucketName: string!) {
      viewer {
        accounts(filter: {accountTag: $accountTag}) {
          r2OperationsAdaptiveGroups(limit: 10000, filter: {
            datetime_geq: $startDate, datetime_leq: $endDate, bucketName: $bucketName
          }) { sum { requests } dimensions { actionType } }
          r2StorageAdaptiveGroups(limit: 1, filter: {
            datetime_geq: $startDate, datetime_leq: $endDate, bucketName: $bucketName
          }, orderBy: [datetime_DESC]) {
            max { objectCount uploadCount payloadSize metadataSize }
            dimensions { datetime }
          }
          r2BandwidthUsageAdaptiveGroups(limit: 1000, filter: {
            datetime_geq: $startDate, datetime_lt: $endDate, bucketName: $bucketName
          }) { sum { bytesUpload bytesDownload } }
        }
      }
    }
    """
    body = json.dumps({
        "query": query,
        "variables": {
            "accountTag": account_id,
            "startDate": start,
            "endDate": end,
            "bucketName": bucket,
        },
    }).encode("utf-8")
    request = Request(
        "https://api.cloudflare.com/client/v4/graphql",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except (URLError, TimeoutError, ValueError):
        return {"status": "unavailable", "bucket": bucket}
    if payload.get("errors"):
        return {"status": "unavailable", "bucket": bucket, "reason": "analytics_query_failed"}
    try:
        account = payload["data"]["viewer"]["accounts"][0]
        ops = account["r2OperationsAdaptiveGroups"]
        storage_groups = account["r2StorageAdaptiveGroups"]
        bandwidth_groups = account["r2BandwidthUsageAdaptiveGroups"]
        operations = {
            group["dimensions"]["actionType"]: int(group["sum"]["requests"] or 0)
            for group in ops
        }
        storage = storage_groups[0]["max"] if storage_groups else {}
        return {
            "status": "ok",
            "bucket": bucket,
            "range_days": days,
            "requests_by_operation": operations,
            "requests_total": sum(operations.values()),
            "objects": int(storage.get("objectCount", 0) or 0),
            "pending_multipart_uploads": int(storage.get("uploadCount", 0) or 0),
            "payload_bytes": int(storage.get("payloadSize", 0) or 0),
            "metadata_bytes": int(storage.get("metadataSize", 0) or 0),
            "bandwidth_upload_bytes": sum(int(x["sum"]["bytesUpload"] or 0) for x in bandwidth_groups),
            "bandwidth_download_bytes": sum(int(x["sum"]["bytesDownload"] or 0) for x in bandwidth_groups),
        }
    except (KeyError, IndexError, TypeError, ValueError):
        return {"status": "unavailable", "bucket": bucket, "reason": "analytics_response_unexpected"}


def usage(days: int, record: bool) -> dict[str, object]:
    if not 1 <= days <= 31:
        raise RuntimeError("usage_range_must_be_1_to_31_days")
    _load_environment()
    cfg = _required("DATABASE_URL")
    try:
        with psycopg.connect(cfg["DATABASE_URL"], sslmode="require", connect_timeout=8) as conn:
            db_usage = conn.execute(
                """
                SELECT current_database(), pg_database_size(current_database()),
                       coalesce(sum(pg_total_relation_size(c.oid)), 0)
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='dialogue' AND c.relkind IN ('r','m')
                """
            ).fetchone()
    except psycopg.Error:
        raise RuntimeError("database_usage_query_failed") from None

    buckets = [
        x for x in (
            os.getenv("S3_ORIGINALS_BUCKET", ""),
            os.getenv("S3_DERIVED_BUCKET", ""),
            os.getenv("BACKUP_BUCKET", ""),
        ) if x.strip()
    ]
    account_id = os.getenv("CF_ACCOUNT_ID", "").strip()
    token = os.getenv("CF_API_TOKEN", "").strip()
    if account_id and token:
        r2_metrics = [_cloudflare_bucket_metrics(name, days, account_id, token) for name in buckets]
    else:
        r2_metrics = {"status": "not_configured", "required_variables": ["CF_ACCOUNT_ID", "CF_API_TOKEN"]}
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "range_days": days,
        "database": {
            "name": db_usage[0],
            "database_bytes": int(db_usage[1]),
            "dialogue_schema_bytes": int(db_usage[2]),
        },
        "r2": r2_metrics,
        "billing_note": "This report is an operational snapshot, not a bill or a budget cap.",
    }
    if record:
        client, bucket = _backup_s3()
        created = datetime.now(timezone.utc)
        object_key = f"ops/usage/{created:%Y/%m}/{created:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex}.json"
        body = json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8")
        try:
            client.put_object(
                Bucket=bucket,
                Key=object_key,
                Body=body,
                ContentType="application/json; charset=utf-8",
                Metadata={"sha256": hashlib.sha256(body).hexdigest()},
            )
        except (ClientError, BotoCoreError):
            raise RuntimeError("usage_report_upload_failed") from None
        report["recorded_to"] = {"bucket": bucket, "object_key": object_key}
    return report


def preflight() -> dict[str, object]:
    _load_environment()
    required = (
        "DATABASE_URL", "BACKUP_BUCKET", "BACKUP_S3_ACCESS_KEY_ID",
        "BACKUP_S3_SECRET_ACCESS_KEY", "BACKUP_ENCRYPTION_KEY",
    )
    missing = [name for name in required if not os.getenv(name, "").strip()]
    pg_dump = bool(shutil.which("pg_dump"))
    pg_restore = bool(shutil.which("pg_restore"))
    try:
        import importlib.util
        crypto_available = importlib.util.find_spec("cryptography") is not None
    except (ImportError, ValueError):
        crypto_available = False
    ready = not missing and pg_dump and pg_restore and crypto_available
    return {
        "status": "ready" if ready else "needs_setup",
        "missing_variables": missing,
        "pg_dump": pg_dump,
        "pg_restore": pg_restore,
        "cryptography": crypto_available,
        "restore_target_configured": bool(os.getenv("RESTORE_DATABASE_URL", "").strip()),
        "cloudflare_analytics_configured": bool(os.getenv("CF_ACCOUNT_ID", "").strip() and os.getenv("CF_API_TOKEN", "").strip()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Encrypted Supabase dialogue-schema backup and usage CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight", help="Check backup prerequisites without connecting to cloud services")
    sub.add_parser("generate-key", help="Print one 32-byte AES key; save it in a password manager")
    sub.add_parser("backup", help="Dump, encrypt and verify the dialogue schema in the backup R2 bucket")
    sub.add_parser("list", help="List encrypted database backup objects")
    restore_parser = sub.add_parser("restore", help="Restore only into a confirmed, empty Supabase project")
    restore_parser.add_argument("--object-key", required=True)
    restore_parser.add_argument("--target-project-ref", required=True)
    restore_parser.add_argument("--confirm-new-project", action="store_true")
    usage_parser = sub.add_parser("usage", help="Report database and Cloudflare R2 use")
    usage_parser.add_argument("--days", type=int, default=7)
    usage_parser.add_argument("--record", action="store_true", help="Save the report in the private backup bucket")
    args = parser.parse_args()
    _load_environment()
    if args.command == "preflight":
        print(json.dumps(preflight(), ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "generate-key":
        print(generate_key())
        return 0
    if args.command == "backup":
        result = backup()
    elif args.command == "list":
        result = list_backups()
    elif args.command == "restore":
        result = restore(args.object_key, args.target_project_ref, args.confirm_new_project)
    else:
        result = usage(args.days, args.record)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print(json.dumps({"status": "cancelled"}), file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        code = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print(json.dumps({"status": "failed", "code": code}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
