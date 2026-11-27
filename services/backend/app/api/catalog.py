"""Browse approved dialogue and issue short-lived playback URLs."""
import psycopg
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, HTTPException
from uuid import UUID

from app.api.search import PUBLISHED_FROM
from app.database import connect
from app.storage.r2 import S3StorageGateway

router = APIRouter(tags=["catalog"])
PLAYBACK_URL_TTL_SECONDS = 15 * 60
SELECTION_WINDOW_MS = 60_000

SEGMENT_FIELDS = """
SELECT s.id, series.slug, series.title_zh, series.title_en,
       e.season, e.episode_number, e.title, s.seq,
       s.start_ms, s.end_ms, s.text_en, s.text_zh,
       coalesce((SELECT p.text_en FROM dialogue.segments p
                 WHERE p.revision_id = s.revision_id AND p.seq < s.seq
                   AND p.review_status = 'approved'
                 ORDER BY p.seq DESC LIMIT 1), ''),
       coalesce((SELECT p.text_zh FROM dialogue.segments p
                 WHERE p.revision_id = s.revision_id AND p.seq < s.seq
                   AND p.review_status = 'approved'
                 ORDER BY p.seq DESC LIMIT 1), ''),
       coalesce((SELECT n.text_en FROM dialogue.segments n
                 WHERE n.revision_id = s.revision_id AND n.seq > s.seq
                   AND n.review_status = 'approved'
                 ORDER BY n.seq LIMIT 1), ''),
       coalesce((SELECT n.text_zh FROM dialogue.segments n
                 WHERE n.revision_id = s.revision_id AND n.seq > s.seq
                   AND n.review_status = 'approved'
                 ORDER BY n.seq LIMIT 1), '')
"""


def _result(row):
    return {
        "id": str(row[0]),
        "series_slug": row[1],
        "series_title_zh": row[2],
        "series_title_en": row[3],
        "season": row[4],
        "episode": row[5],
        "episode_title": row[6],
        "sequence": row[7],
        "start_ms": row[8],
        "end_ms": row[9],
        "text_en": row[10],
        "text_zh": row[11],
        "previous_en": row[12],
        "previous_zh": row[13],
        "next_en": row[14],
        "next_zh": row[15],
    }


@router.get("/api/v1/featured")
def featured_dialogue():
    try:
        with connect() as conn:
            rows = conn.execute(
                SEGMENT_FIELDS + PUBLISHED_FROM + " ORDER BY random() LIMIT 9"
            ).fetchall()
    except (psycopg.Error, TimeoutError, ValueError):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "database_unavailable",
                "message": "精选台词暂时无法加载，请稍后重试。",
            },
        ) from None
    return {"results": [_result(row) for row in rows]}


@router.get("/api/v1/segments/{segment_id}/playback")
def segment_playback(segment_id: UUID):
    """Re-check preview rights and issue a fresh URL plus same-revision captions.

    Calling this endpoint again renews an expired URL. It intentionally returns
    no saved clip/share state; clip selection is transient until M7.
    """
    try:
        with connect() as conn:
            row = conn.execute(
                """
                SELECT s.id, s.version_id, s.revision_id, s.seq,
                       s.start_ms, s.end_ms,
                       playable_asset.bucket, playable_asset.object_key,
                       playable_asset.mime, v.duration_ms,
                       EXISTS (
                         SELECT 1 FROM dialogue.rights share_rights
                         WHERE share_rights.version_id = v.id
                           AND share_rights.allow_preview IS TRUE
                           AND share_rights.allow_share IS TRUE
                           AND share_rights.reviewed_at IS NOT NULL
                           AND (share_rights.valid_from IS NULL OR share_rights.valid_from <= now())
                           AND (share_rights.valid_to IS NULL OR share_rights.valid_to > now())
                       ) AS share_allowed
                """ + PUBLISHED_FROM + " AND s.id = %s",
                (str(segment_id),),
            ).fetchone()
            if row:
                lower_bound = max(0, row[4] - SELECTION_WINDOW_MS)
                upper_bound = row[5] + SELECTION_WINDOW_MS
                if row[9] is not None:
                    upper_bound = min(row[9], upper_bound)
                selectable_rows = conn.execute(
                    """
                    SELECT s.id, s.version_id, s.revision_id, s.seq,
                           s.start_ms, s.end_ms, s.text_en, s.text_zh
                    """ + PUBLISHED_FROM + """
                      AND s.version_id = %s
                      AND s.revision_id = %s
                      AND s.start_ms >= %s
                      AND s.start_ms <= %s
                    ORDER BY s.seq
                    LIMIT 250
                    """,
                    (str(row[1]), str(row[2]), lower_bound, upper_bound),
                ).fetchall()
            else:
                selectable_rows = []
    except (psycopg.Error, TimeoutError, ValueError):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "database_unavailable",
                "message": "播放信息暂时无法加载，请重试。",
            },
        ) from None
    if not row:
        raise HTTPException(status_code=404, detail={"code": "segment_unavailable"})

    try:
        media_url = S3StorageGateway().sign_read(
            row[6], row[7], ttl=PLAYBACK_URL_TTL_SECONDS
        )
    except (ClientError, BotoCoreError, ValueError):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "media_unavailable",
                "message": "视频暂时无法播放，请稍后重试。",
            },
        ) from None

    return {
        "media_url": media_url,
        "expires_in_seconds": PLAYBACK_URL_TTL_SECONDS,
        "media_type": row[8],
        "duration_ms": row[9],
        "allow_share": row[10],
        "version_id": str(row[1]),
        "revision_id": str(row[2]),
        "start_ms": row[4],
        "end_ms": row[5],
        "segments": [
            {
                "id": str(item[0]),
                "version_id": str(item[1]),
                "revision_id": str(item[2]),
                "sequence": item[3],
                "start_ms": item[4],
                "end_ms": item[5],
                "text_en": item[6],
                "text_zh": item[7],
            }
            for item in selectable_rows
        ],
    }
