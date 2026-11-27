"""TLS-only bounded pool; never log connection strings or driver exception text."""
import os
from functools import lru_cache
from urllib.parse import urlsplit
import psycopg
from psycopg_pool import ConnectionPool
from app.config import missing_config

TABLES = set("schema_versions series episodes assets video_versions rights subtitle_revisions subtitle_tracks subtitle_cues segments segment_cues clips clip_segments clip_assets shares jobs admin_users sessions audit_events".split())


def database_url(migration=False):
    missing_config()
    url = os.getenv("MIGRATION_DATABASE_URL", "") if migration else ""
    url = url or os.getenv("DATABASE_URL", "")
    parsed = urlsplit(url)
    if parsed.scheme not in ("postgresql", "postgres") or not parsed.hostname or not parsed.password:
        raise ValueError("invalid_database_configuration")
    if parsed.password.startswith("[") and parsed.password.endswith("]"):
        raise ValueError("password_placeholder_brackets")
    return url


def connect(migration=False):
    return psycopg.connect(database_url(migration), sslmode="require", connect_timeout=8,
                           options="-c statement_timeout=10000")

@lru_cache(maxsize=1)
def pool():
    return ConnectionPool(database_url(), min_size=0, max_size=3, timeout=10,
        kwargs={"sslmode": "require", "connect_timeout": 8,
                "options": "-c statement_timeout=10000"}, open=True)


def inspect_schema(conn):
    tables = {row[0] for row in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='dialogue'")}
    missing = sorted(TABLES - tables)
    if missing:
        return {"ok": False, "reason": "missing_tables", "missing_tables": missing}
    versions = {row[0] for row in conn.execute("SELECT version FROM dialogue.schema_versions")}
    rls = conn.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='dialogue' AND c.relkind='r' AND NOT c.relrowsecurity AND c.relname <> 'alembic_version'").fetchone()[0]
    external = conn.execute("SELECT has_schema_privilege('anon','dialogue','USAGE') OR has_schema_privilege('authenticated','dialogue','USAGE') OR has_schema_privilege('service_role','dialogue','USAGE')").fetchone()[0]
    indexes = {row[0] for row in conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname='dialogue'")}
    required_indexes = {'segments_en_trgm', 'segments_zh_trgm', 'video_versions_status', 'jobs_claim', 'jobs_lease'}
    tls = conn.execute("SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone()
    user = conn.execute("SELECT current_user").fetchone()[0]
    restricted = user == 'dialogue_backend'
    return {"ok": '001' in versions and rls == 0 and not external and required_indexes <= indexes,
            "table_count": len(TABLES & tables), "version_001": '001' in versions,
            "rls_enabled": rls == 0, "external_access_blocked": not external,
            "required_indexes_present": required_indexes <= indexes,
            "database_server_tls": bool(tls and tls[0]),
            "restricted_runtime_role": restricted}


def database_health():
    try:
        if 'DATABASE_URL' in missing_config():
            return {"status": "missing_configuration"}
        with pool().connection() as conn:
            result = inspect_schema(conn)
        return {"status": "ok" if result.pop('ok') else "schema_incomplete", **result}
    except ValueError:
        return {"status": "invalid_configuration"}
    except (psycopg.Error, TimeoutError):
        return {"status": "connection_failed"}
