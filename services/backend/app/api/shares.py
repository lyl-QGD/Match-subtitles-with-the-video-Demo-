"""Capability-link sharing for locally managed, explicitly shareable clips."""
import hashlib
import json
import os
import re
import secrets
from typing import Literal
from uuid import UUID

import psycopg
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.catalog import PLAYBACK_URL_TTL_SECONDS
from app.config import missing_config
from app.database import connect
from app.storage.r2 import S3StorageGateway

router = APIRouter(prefix="/api/v1/shares", tags=["shares"])
SHARE_TTL_DAYS = 7
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


class CreateShareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: UUID
    revision_id: UUID
    segment_ids: list[UUID] = Field(min_length=1, max_length=100)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    subtitle_mode: Literal["bilingual", "en", "zh", "off"] = "bilingual"


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(max_length=128)


def local_operator_only(request: Request):
    """Creation and management are local-only until the app has user accounts."""
    missing_config()
    if os.getenv("APP_ENV") != "development" or not request.client \
            or request.client.host not in ("127.0.0.1", "::1", "testclient"):
        raise HTTPException(status_code=403, detail={"code": "local_management_only"})
    if request.headers.get("origin") not in (
        None, "http://localhost:5173", "http://127.0.0.1:5173"
    ):
        raise HTTPException(status_code=403, detail={"code": "origin_not_allowed"})
    if request.headers.get("host", "").split(":")[0] not in (
        "localhost", "127.0.0.1", "testserver"
    ):
        raise HTTPException(status_code=403, detail={"code": "host_not_allowed"})


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _unavailable():
    raise HTTPException(status_code=410, detail={"code": "share_unavailable"})


def _database_unavailable():
    raise HTTPException(
        status_code=503,
        detail={"code": "database_unavailable", "message": "分享暂时无法读取，请稍后重试。"},
    ) from None


@router.post("", dependencies=[Depends(local_operator_only)])
def create_share(request: CreateShareRequest):
    if len(set(request.segment_ids)) != len(request.segment_ids):
        raise HTTPException(status_code=422, detail={"code": "duplicate_segments"})
    if request.end_ms <= request.start_ms or request.end_ms - request.start_ms > 60_000:
        raise HTTPException(status_code=422, detail={"code": "invalid_clip_range"})

    try:
        with connect() as conn:
            rights = conn.execute(
                """
                SELECT v.duration_ms, v.active_revision_id, v.status, sr.status,
                       rights.allow_preview, rights.allow_share, rights.reviewed_at,
                       (rights.valid_from IS NULL OR rights.valid_from <= now())
                       AND (rights.valid_to IS NULL OR rights.valid_to > now())
                FROM dialogue.video_versions v
                JOIN dialogue.subtitle_revisions sr
                  ON sr.id = %s AND sr.version_id = v.id
                LEFT JOIN dialogue.rights rights ON rights.version_id = v.id
                WHERE v.id = %s
                """,
                (request.revision_id, request.version_id),
            ).fetchone()
            if not rights or str(rights[1]) != str(request.revision_id) \
                    or rights[2] != "published" or rights[3] != "published" \
                    or rights[4] is not True or rights[6] is None or rights[7] is not True:
                raise HTTPException(status_code=404, detail={"code": "source_unavailable"})
            if rights[5] is not True:
                raise HTTPException(
                    status_code=403,
                    detail={"code": "sharing_not_authorized", "message": "该素材当前未开启分享授权。"},
                )
            if rights[0] is not None and request.end_ms > rights[0]:
                raise HTTPException(status_code=422, detail={"code": "invalid_clip_range"})

            segments = conn.execute(
                """
                SELECT id, seq, start_ms, end_ms
                FROM dialogue.segments
                WHERE id = ANY(%s::uuid[])
                  AND version_id = %s
                  AND revision_id = %s
                  AND review_status = 'approved'
                ORDER BY seq
                """,
                (request.segment_ids, request.version_id, request.revision_id),
            ).fetchall()
            if len(segments) != len(request.segment_ids):
                raise HTTPException(status_code=404, detail={"code": "source_unavailable"})
            if any(segments[i][1] != segments[i - 1][1] + 1 for i in range(1, len(segments))):
                raise HTTPException(status_code=422, detail={"code": "non_contiguous_segments"})
            if request.start_ms >= segments[0][3] or request.end_ms <= segments[-1][2]:
                raise HTTPException(status_code=422, detail={"code": "range_misses_selected_dialogue"})

            render_material = {
                "version_id": str(request.version_id),
                "revision_id": str(request.revision_id),
                "segment_ids": [str(row[0]) for row in segments],
                "start_ms": request.start_ms,
                "end_ms": request.end_ms,
                "subtitle_mode": request.subtitle_mode,
            }
            render_key = hashlib.sha256(
                json.dumps(render_material, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            clip_id = conn.execute(
                """
                INSERT INTO dialogue.clips
                    (version_id, revision_id, start_ms, end_ms, subtitle_mode, render_key)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (render_key) DO UPDATE SET render_key = EXCLUDED.render_key
                RETURNING id
                """,
                (request.version_id, request.revision_id, request.start_ms,
                 request.end_ms, request.subtitle_mode, render_key),
            ).fetchone()[0]
            for position, segment in enumerate(segments):
                conn.execute(
                    """
                    INSERT INTO dialogue.clip_segments
                        (clip_id, segment_id, revision_id, version_id, position)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (clip_id, segment_id) DO NOTHING
                    """,
                    (clip_id, segment[0], request.revision_id, request.version_id, position),
                )

            token = secrets.token_urlsafe(32)
            share_row = conn.execute(
                """
                INSERT INTO dialogue.shares (token_hash, clip_id, expires_at)
                VALUES (%s, %s, now() + (%s * interval '1 day'))
                RETURNING id, expires_at
                """,
                (_token_hash(token), clip_id, SHARE_TTL_DAYS),
            ).fetchone()
    except HTTPException:
        raise
    except (psycopg.Error, TimeoutError, ValueError):
        _database_unavailable()

    return {
        "share_id": str(share_row[0]),
        "token": token,
        "expires_at": share_row[1],
        "expires_in_days": SHARE_TTL_DAYS,
    }


@router.post("/resolve")
def resolve_share(request: TokenRequest):
    if not TOKEN_PATTERN.fullmatch(request.token):
        raise HTTPException(status_code=404, detail={"code": "share_invalid"})
    try:
        with connect() as conn:
            share = conn.execute(
                """
                SELECT id, clip_id, status,
                       expires_at IS NOT NULL AND expires_at <= now()
                FROM dialogue.shares WHERE token_hash = %s
                """,
                (_token_hash(request.token),),
            ).fetchone()
            if not share:
                raise HTTPException(status_code=404, detail={"code": "share_invalid"})
            if share[2] == "revoked":
                raise HTTPException(status_code=410, detail={"code": "share_revoked"})
            if share[3]:
                raise HTTPException(status_code=410, detail={"code": "share_expired"})

            clip = conn.execute(
                """
                SELECT c.id, c.version_id, c.revision_id, c.start_ms, c.end_ms,
                       c.subtitle_mode, series.slug, series.title_zh, series.title_en,
                       e.season, e.episode_number, e.title, v.duration_ms,
                       media.bucket, media.object_key, media.mime
                FROM dialogue.clips c
                JOIN dialogue.video_versions v
                  ON v.id = c.version_id AND v.status = 'published'
                JOIN dialogue.subtitle_revisions sr
                  ON sr.id = c.revision_id AND sr.version_id = v.id
                 AND sr.status IN ('published', 'archived')
                JOIN dialogue.episodes e ON e.id = v.episode_id
                JOIN dialogue.series series ON series.id = e.series_id
                JOIN dialogue.assets media
                  ON media.id = coalesce(v.proxy_asset_id, v.source_asset_id)
                 AND media.status = 'ready'
                JOIN dialogue.rights rights ON rights.version_id = v.id
                 AND rights.allow_preview IS TRUE
                 AND rights.allow_share IS TRUE
                 AND rights.reviewed_at IS NOT NULL
                 AND (rights.valid_from IS NULL OR rights.valid_from <= now())
                 AND (rights.valid_to IS NULL OR rights.valid_to > now())
                WHERE c.id = %s
                """,
                (share[1],),
            ).fetchone()
            if not clip:
                _unavailable()
            segments = conn.execute(
                """
                SELECT s.id, s.seq, s.start_ms, s.end_ms, s.text_en, s.text_zh,
                       s.version_id, s.revision_id, s.review_status
                FROM dialogue.clip_segments cs
                JOIN dialogue.segments s
                  ON s.id = cs.segment_id AND s.version_id = cs.version_id
                 AND s.revision_id = cs.revision_id
                WHERE cs.clip_id = %s AND cs.version_id = %s AND cs.revision_id = %s
                ORDER BY cs.position
                """,
                (share[1], clip[1], clip[2]),
            ).fetchall()
            if not segments or any(row[8] != "approved" for row in segments):
                _unavailable()
    except HTTPException:
        raise
    except (psycopg.Error, TimeoutError, ValueError):
        _database_unavailable()

    try:
        media_url = S3StorageGateway().sign_read(
            clip[13], clip[14], ttl=PLAYBACK_URL_TTL_SECONDS
        )
    except (ClientError, BotoCoreError, ValueError):
        raise HTTPException(
            status_code=503,
            detail={"code": "media_unavailable", "message": "视频暂时无法播放，请稍后重试。"},
        ) from None

    return {
        "series_slug": clip[6],
        "series_title_zh": clip[7],
        "series_title_en": clip[8],
        "season": clip[9],
        "episode": clip[10],
        "episode_title": clip[11],
        "version_id": str(clip[1]),
        "revision_id": str(clip[2]),
        "start_ms": clip[3],
        "end_ms": clip[4],
        "subtitle_mode": clip[5],
        "duration_ms": clip[12],
        "media_type": clip[15],
        "media_url": media_url,
        "media_url_expires_in_seconds": PLAYBACK_URL_TTL_SECONDS,
        "segments": [
            {
                "id": str(row[0]),
                "sequence": row[1],
                "start_ms": row[2],
                "end_ms": row[3],
                "text_en": row[4],
                "text_zh": row[5],
            }
            for row in segments
        ],
    }


@router.get("/manage", dependencies=[Depends(local_operator_only)])
def list_shares():
    try:
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT sh.id, sh.status, sh.created_at, sh.expires_at,
                       c.start_ms, c.end_ms, series.slug, e.season, e.episode_number,
                       CASE WHEN sh.status = 'revoked' THEN 'revoked'
                            WHEN sh.expires_at IS NOT NULL AND sh.expires_at <= now() THEN 'expired'
                            ELSE 'active' END
                FROM dialogue.shares sh
                JOIN dialogue.clips c ON c.id = sh.clip_id
                JOIN dialogue.video_versions v ON v.id = c.version_id
                JOIN dialogue.episodes e ON e.id = v.episode_id
                JOIN dialogue.series series ON series.id = e.series_id
                ORDER BY sh.created_at DESC
                LIMIT 100
                """
            ).fetchall()
    except (psycopg.Error, TimeoutError, ValueError):
        _database_unavailable()
    return {
        "shares": [
            {
                "id": str(row[0]),
                "status": row[9],
                "created_at": row[2],
                "expires_at": row[3],
                "start_ms": row[4],
                "end_ms": row[5],
                "series_slug": row[6],
                "season": row[7],
                "episode": row[8],
            }
            for row in rows
        ]
    }


@router.post("/manage/{share_id}/revoke", dependencies=[Depends(local_operator_only)])
def revoke_share(share_id: UUID):
    try:
        with connect() as conn:
            row = conn.execute(
                "UPDATE dialogue.shares SET status = 'revoked' WHERE id = %s RETURNING status",
                (share_id,),
            ).fetchone()
    except (psycopg.Error, TimeoutError, ValueError):
        _database_unavailable()
    if not row:
        raise HTTPException(status_code=404, detail={"code": "share_invalid"})
    return {"status": row[0]}
