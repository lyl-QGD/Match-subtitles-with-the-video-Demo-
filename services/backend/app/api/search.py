"""Public-safe bilingual search over reviewed, explicitly authorized material."""
import math

import psycopg
from fastapi import APIRouter, HTTPException, Query

from app.database import connect
from app.media.subtitles import normalize

router = APIRouter(prefix="/api/v1/search", tags=["search"])

PUBLISHED_FROM = """
FROM dialogue.segments s
JOIN dialogue.video_versions v
  ON v.id = s.version_id
 AND v.active_revision_id = s.revision_id
JOIN dialogue.subtitle_revisions sr
  ON sr.id = s.revision_id
 AND sr.version_id = v.id
JOIN dialogue.episodes e ON e.id = v.episode_id
JOIN dialogue.series series ON series.id = e.series_id
JOIN dialogue.assets playable_asset
  ON playable_asset.id = coalesce(v.proxy_asset_id, v.source_asset_id)
 AND playable_asset.status = 'ready'
WHERE v.status = 'published'
  AND sr.status = 'published'
  AND s.review_status = 'approved'
  AND EXISTS (
    SELECT 1
    FROM dialogue.rights rights
    WHERE rights.version_id = v.id
      AND rights.allow_preview IS TRUE
      AND rights.reviewed_at IS NOT NULL
      AND (rights.valid_from IS NULL OR rights.valid_from <= now())
      AND (rights.valid_to IS NULL OR rights.valid_to > now())
  )
"""


def _like_pattern(value: str) -> str:
    """Escape SQL LIKE metacharacters so user input is always literal text."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@router.get("")
def search_dialogue(
    q: str = Query(min_length=1, max_length=120),
    season: int | None = Query(default=None, ge=1, le=100),
    episode: int | None = Query(default=None, ge=1, le=100),
    page: int = Query(default=1, ge=1, le=10000),
    page_size: int = Query(default=10, ge=1, le=50),
):
    needle = normalize(q)
    if not needle:
        raise HTTPException(status_code=422, detail={"code": "query_required"})

    pattern = _like_pattern(needle)
    where = PUBLISHED_FROM + r" AND (s.search_en LIKE %s ESCAPE E'\\' OR s.search_zh LIKE %s ESCAPE E'\\')"
    params: list[object] = [pattern, pattern]
    if season is not None:
        where += " AND e.season = %s"
        params.append(season)
    if episode is not None:
        where += " AND e.episode_number = %s"
        params.append(episode)

    count_sql = "SELECT count(*) " + where
    offset = (page - 1) * page_size
    result_sql = """
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
                         ORDER BY n.seq LIMIT 1), ''),
               (s.search_en LIKE %s ESCAPE E'\\\\') AS matched_en,
               (s.search_zh LIKE %s ESCAPE E'\\\\') AS matched_zh
    """ + where + """
        ORDER BY CASE
                   WHEN s.search_en = %s OR s.search_zh = %s THEN 0
                   ELSE 1
                 END,
                 series.slug, e.season, e.episode_number, s.seq
        LIMIT %s OFFSET %s
    """
    result_params = [pattern, pattern, *params, needle, needle, page_size, offset]

    try:
        with connect() as conn:
            total = conn.execute(count_sql, params).fetchone()[0]
            rows = conn.execute(result_sql, result_params).fetchall() if total else []
    except (psycopg.Error, TimeoutError, ValueError):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "database_unavailable",
                "message": "搜索服务暂时无法连接云端数据库，请稍后重试。",
            },
        ) from None

    results = [
        {
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
            "matched_languages": [lang for lang, matched in (("en", row[16]), ("zh", row[17])) if matched],
        }
        for row in rows
    ]
    return {
        "query": q,
        "season": season,
        "episode": episode,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": math.ceil(total / page_size) if total else 0,
        "results": results,
    }
