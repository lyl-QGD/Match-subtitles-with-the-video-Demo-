-- 在 Supabase SQL Editor 中，以 postgres 角色完整执行本文件。
-- 首次建库脚本：失败时整个事务回滚；已成功执行后不要重复执行。
-- 不修改 public/auth/storage，也不保存任何视频二进制。
BEGIN;
CREATE SCHEMA dialogue;
REVOKE ALL ON SCHEMA dialogue FROM PUBLIC, anon, authenticated, service_role;

CREATE TABLE dialogue.schema_versions (
  version text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE dialogue.series (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug text NOT NULL UNIQUE,
  title_zh text NOT NULL,
  title_en text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE dialogue.episodes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  series_id uuid NOT NULL REFERENCES dialogue.series(id),
  season integer NOT NULL CHECK (season > 0),
  episode_number integer NOT NULL CHECK (episode_number > 0),
  title text NOT NULL DEFAULT '',
  UNIQUE(series_id, season, episode_number)
);
CREATE TABLE dialogue.assets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  bucket text NOT NULL,
  object_key text NOT NULL,
  sha256 text CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
  mime text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('source_video','source_subtitle','proxy','subtitle','thumbnail','export')),
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','ready','failed','deleted')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(bucket, object_key)
);
CREATE TABLE dialogue.video_versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  episode_id uuid NOT NULL REFERENCES dialogue.episodes(id),
  edition text NOT NULL,
  source_asset_id uuid REFERENCES dialogue.assets(id),
  proxy_asset_id uuid REFERENCES dialogue.assets(id),
  duration_ms bigint CHECK (duration_ms > 0),
  active_revision_id uuid,
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','uploaded','probing','processing','needs_review','ready','published','failed','withdrawn')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(episode_id, edition)
);
CREATE TABLE dialogue.rights (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  version_id uuid NOT NULL UNIQUE REFERENCES dialogue.video_versions(id),
  evidence_ref text,
  source_url text,
  allow_preview boolean NOT NULL DEFAULT false,
  allow_share boolean NOT NULL DEFAULT false,
  allow_download boolean NOT NULL DEFAULT false,
  territories text[] NOT NULL DEFAULT '{}',
  valid_from timestamptz,
  valid_to timestamptz,
  reviewed_at timestamptz,
  CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from)
);
CREATE TABLE dialogue.subtitle_revisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  version_id uuid NOT NULL REFERENCES dialogue.video_versions(id),
  revision integer NOT NULL CHECK (revision > 0),
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','needs_review','published','archived')),
  created_at timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz,
  UNIQUE(version_id, revision),
  UNIQUE(id, version_id)
);
ALTER TABLE dialogue.video_versions ADD CONSTRAINT active_revision_same_version
  FOREIGN KEY (active_revision_id, id) REFERENCES dialogue.subtitle_revisions(id, version_id);

CREATE TABLE dialogue.subtitle_tracks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  version_id uuid NOT NULL REFERENCES dialogue.video_versions(id),
  language text NOT NULL CHECK (language IN ('en','zh')),
  source_asset_id uuid NOT NULL REFERENCES dialogue.assets(id),
  revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
  provenance text NOT NULL,
  review_status text NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','approved','rejected')),
  UNIQUE(version_id, language, revision),
  UNIQUE(id, version_id)
);
CREATE TABLE dialogue.subtitle_cues (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  track_id uuid NOT NULL,
  version_id uuid NOT NULL,
  seq integer NOT NULL CHECK (seq >= 0),
  raw_text text NOT NULL,
  clean_text text NOT NULL,
  start_ms bigint NOT NULL CHECK (start_ms >= 0),
  end_ms bigint NOT NULL CHECK (end_ms > start_ms),
  FOREIGN KEY(track_id, version_id) REFERENCES dialogue.subtitle_tracks(id, version_id),
  UNIQUE(track_id, seq),
  UNIQUE(id, version_id)
);
CREATE TABLE dialogue.segments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  revision_id uuid NOT NULL,
  version_id uuid NOT NULL,
  seq integer NOT NULL CHECK (seq >= 0),
  start_ms bigint NOT NULL CHECK (start_ms >= 0),
  end_ms bigint NOT NULL CHECK (end_ms > start_ms),
  text_en text NOT NULL DEFAULT '',
  text_zh text NOT NULL DEFAULT '',
  search_en text NOT NULL DEFAULT '',
  search_zh text NOT NULL DEFAULT '',
  review_status text NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','approved','rejected')),
  FOREIGN KEY(revision_id, version_id) REFERENCES dialogue.subtitle_revisions(id, version_id),
  CHECK (text_en <> '' OR text_zh <> ''),
  UNIQUE(revision_id, seq),
  UNIQUE(id, version_id),
  UNIQUE(id, revision_id, version_id)
);
CREATE TABLE dialogue.segment_cues (
  segment_id uuid NOT NULL,
  cue_id uuid NOT NULL,
  version_id uuid NOT NULL,
  PRIMARY KEY(segment_id, cue_id),
  FOREIGN KEY(segment_id, version_id) REFERENCES dialogue.segments(id, version_id),
  FOREIGN KEY(cue_id, version_id) REFERENCES dialogue.subtitle_cues(id, version_id)
);
CREATE TABLE dialogue.clips (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  version_id uuid NOT NULL,
  revision_id uuid NOT NULL,
  start_ms bigint NOT NULL CHECK (start_ms >= 0),
  end_ms bigint NOT NULL,
  subtitle_mode text NOT NULL DEFAULT 'bilingual' CHECK (subtitle_mode IN ('bilingual','en','zh','off')),
  render_key text NOT NULL UNIQUE CHECK (render_key ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (end_ms > start_ms AND end_ms - start_ms <= 60000),
  FOREIGN KEY(revision_id, version_id) REFERENCES dialogue.subtitle_revisions(id, version_id),
  UNIQUE(id, revision_id, version_id)
);
CREATE TABLE dialogue.clip_segments (
  clip_id uuid NOT NULL,
  segment_id uuid NOT NULL,
  revision_id uuid NOT NULL,
  version_id uuid NOT NULL,
  position integer NOT NULL CHECK (position >= 0),
  PRIMARY KEY(clip_id, segment_id),
  UNIQUE(clip_id, position),
  FOREIGN KEY(clip_id, revision_id, version_id) REFERENCES dialogue.clips(id, revision_id, version_id),
  FOREIGN KEY(segment_id, revision_id, version_id) REFERENCES dialogue.segments(id, revision_id, version_id)
);
CREATE TABLE dialogue.clip_assets (
  clip_id uuid NOT NULL REFERENCES dialogue.clips(id),
  asset_id uuid NOT NULL REFERENCES dialogue.assets(id),
  render_profile text NOT NULL,
  expires_at timestamptz,
  PRIMARY KEY(clip_id, render_profile)
);
CREATE TABLE dialogue.shares (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  token_hash text NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
  clip_id uuid NOT NULL REFERENCES dialogue.clips(id),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
  expires_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE dialogue.jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  type text NOT NULL CHECK (type IN ('import','probe','proxy','export','cleanup')),
  payload jsonb NOT NULL DEFAULT '{}',
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','processing','ready','failed','cancelled')),
  dedupe_key text NOT NULL UNIQUE,
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  run_after timestamptz NOT NULL DEFAULT now(),
  lease_until timestamptz,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE dialogue.admin_users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  username text NOT NULL UNIQUE,
  password_hash text NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE dialogue.sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  admin_user_id uuid REFERENCES dialogue.admin_users(id),
  token_hash text NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE dialogue.audit_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  actor uuid REFERENCES dialogue.admin_users(id),
  action text NOT NULL,
  target text NOT NULL,
  details jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);

-- 独立扩展 schema；不依赖不稳定的默认 search_path。
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA extensions;
-- 从扩展实际安装位置获取 operator class，兼容已安装的项目。
DO $$
DECLARE ext_schema text;
BEGIN
  SELECT n.nspname INTO ext_schema FROM pg_extension e
    JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='pg_trgm';
  EXECUTE format('CREATE INDEX segments_en_trgm ON dialogue.segments USING gin(search_en %I.gin_trgm_ops)', ext_schema);
  EXECUTE format('CREATE INDEX segments_zh_trgm ON dialogue.segments USING gin(search_zh %I.gin_trgm_ops)', ext_schema);
END $$;
CREATE INDEX video_versions_status ON dialogue.video_versions(status, episode_id);
CREATE INDEX subtitle_cues_version ON dialogue.subtitle_cues(version_id);
CREATE INDEX segments_version ON dialogue.segments(version_id);
CREATE INDEX shares_clip ON dialogue.shares(clip_id);
CREATE INDEX jobs_claim ON dialogue.jobs(run_after, created_at) WHERE status='queued';
CREATE INDEX jobs_lease ON dialogue.jobs(lease_until) WHERE status='processing';

-- 默认拒绝所有浏览器 Data API 角色；不创建匿名访问策略。
DO $$
DECLARE t record;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname='dialogue' LOOP
    EXECUTE format('ALTER TABLE dialogue.%I ENABLE ROW LEVEL SECURITY', t.tablename);
  END LOOP;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA dialogue FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA dialogue FROM PUBLIC, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA dialogue REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA dialogue REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC, anon, authenticated, service_role;
INSERT INTO dialogue.schema_versions(version) VALUES ('001');
COMMIT;
