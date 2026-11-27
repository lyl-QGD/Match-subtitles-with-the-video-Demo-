"""Live M8 smoke checks for the local API and the configured cloud resources.

``smoke`` is read-only by default. ``--share-write`` creates one temporary
share, resolves it, checks Range playback, revokes it, and verifies revocation.
The revoked share/clip rows remain in Supabase as an audit trail.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from dotenv import load_dotenv

from app.config import ROOT


def _json(response: httpx.Response, expected: int, label: str) -> dict:
    if response.status_code != expected:
        code = "unexpected_response"
        try:
            detail = response.json().get("detail", {})
            code = detail.get("code", code) if isinstance(detail, dict) else code
        except (ValueError, AttributeError):
            pass
        raise RuntimeError(f"{label}:{response.status_code}:{code}")
    return response.json()


def _reject_signature(url: str) -> str:
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    for index, (name, value) in enumerate(query):
        if name.lower() == "x-amz-signature" and value:
            replacement = "0" if value[0] != "0" else "1"
            query[index] = (name, replacement + value[1:])
            return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    raise RuntimeError("signed_url_missing_signature_field")


def smoke(share_write: bool, search_en: str, search_zh: str) -> dict[str, object]:
    load_dotenv(ROOT / ".env", override=False)
    base = os.getenv("M8_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/") + "/"
    report: dict[str, object] = {"status": "running", "api_base": base.rstrip("/")}
    token: str | None = None
    share_id: str | None = None

    with httpx.Client(timeout=httpx.Timeout(30, connect=8), follow_redirects=False) as client:
        live = _json(client.get(urljoin(base, "api/v1/health/live")), 200, "api_live")
        if live.get("status") != "ok":
            raise RuntimeError("api_live_unhealthy")
        report["api_live"] = "ok"

        ready_response = client.get(urljoin(base, "api/v1/health/ready"))
        ready = _json(ready_response, 200, "cloud_ready")
        if ready.get("status") != "ready":
            report["cloud_ready"] = "not_ready"
            raise RuntimeError("cloud_ready_check_failed")
        report["cloud_ready"] = "ok"
        report["database"] = ready.get("database", {}).get("status")
        report["storage"] = ready.get("storage", {}).get("status")

        for language, query in (("en", search_en), ("zh", search_zh)):
            response = client.get(urljoin(base, "api/v1/search"), params={"q": query, "page_size": 1})
            result = _json(response, 200, f"search_{language}")
            if result.get("total", 0) < 1 or not result.get("results"):
                raise RuntimeError(f"known_search_has_no_results:{language}")
            report[f"search_{language}"] = {"status": "ok", "matches": result["total"]}

        featured = _json(client.get(urljoin(base, "api/v1/featured")), 200, "featured")
        results = featured.get("results", [])
        if len(results) != 9:
            raise RuntimeError("featured_count_not_nine")
        report["featured"] = {"status": "ok", "count": len(results)}

        sample = results[0]
        playback = _json(
            client.get(urljoin(base, f"api/v1/segments/{sample['id']}/playback")),
            200,
            "playback",
        )
        if playback.get("allow_share") is not True:
            raise RuntimeError("current_segment_share_permission_not_enabled")
        media = client.get(playback["media_url"], headers={"Range": "bytes=0-0"})
        media_type = media.headers.get("content-type", "").split(";", 1)[0].lower()
        if media.status_code != 206 or media_type != "video/mp4":
            media.close()
            raise RuntimeError("r2_range_playback_failed")
        if not media.headers.get("content-range", "").startswith("bytes 0-0/"):
            media.close()
            raise RuntimeError("r2_content_range_invalid")
        media.close()
        report["playback_range"] = {"status": "ok", "http": 206, "content_type": media_type}
        report["version_id"] = playback["version_id"]
        report["revision_id"] = playback["revision_id"]

        rejected = client.get(_reject_signature(playback["media_url"]), headers={"Range": "bytes=0-0"})
        if rejected.status_code != 403:
            rejected.close()
            raise RuntimeError("invalid_signed_url_was_not_rejected")
        rejected.close()
        renewed = _json(
            client.get(urljoin(base, f"api/v1/segments/{sample['id']}/playback")),
            200,
            "playback_renewal",
        )
        if renewed.get("version_id") != playback.get("version_id") or renewed.get("revision_id") != playback.get("revision_id"):
            raise RuntimeError("playback_renewal_changed_version")
        renewed_media = client.get(renewed["media_url"], headers={"Range": "bytes=0-0"})
        if renewed_media.status_code != 206:
            renewed_media.close()
            raise RuntimeError("renewed_signed_url_failed")
        renewed_media.close()
        report["signed_url_rejection_and_renewal"] = "ok"

        if share_write:
            selected = next(
                (item for item in playback.get("segments", []) if item.get("id") == sample["id"]),
                None,
            )
            if not selected:
                raise RuntimeError("featured_segment_missing_from_playback_selection")
            create_response = client.post(
                urljoin(base, "api/v1/shares"),
                json={
                    "version_id": playback["version_id"],
                    "revision_id": playback["revision_id"],
                    "segment_ids": [selected["id"]],
                    "start_ms": selected["start_ms"],
                    "end_ms": selected["end_ms"],
                    "subtitle_mode": "bilingual",
                },
            )
            created = _json(create_response, 200, "share_create")
            token = created["token"]
            share_id = created["share_id"]
            try:
                resolved = _json(
                    client.post(urljoin(base, "api/v1/shares/resolve"), json={"token": token}),
                    200,
                    "share_resolve",
                )
                if resolved.get("version_id") != playback.get("version_id"):
                    raise RuntimeError("share_resolved_wrong_video_version")
                shared_media = client.get(resolved["media_url"], headers={"Range": "bytes=0-0"})
                shared_type = shared_media.headers.get("content-type", "").split(";", 1)[0].lower()
                if shared_media.status_code != 206 or shared_type != "video/mp4":
                    shared_media.close()
                    raise RuntimeError("shared_playback_range_failed")
                shared_media.close()
                report["share_create_resolve"] = "ok"
            finally:
                revoked = client.post(urljoin(base, f"api/v1/shares/manage/{share_id}/revoke"))
                _json(revoked, 200, "share_revoke")
            expired = client.post(urljoin(base, "api/v1/shares/resolve"), json={"token": token})
            if expired.status_code != 410:
                raise RuntimeError("revoked_share_still_resolves")
            try:
                revoked_code = expired.json().get("detail", {}).get("code")
            except (ValueError, AttributeError):
                revoked_code = None
            if revoked_code != "share_revoked":
                raise RuntimeError("revoked_share_status_invalid")
            report["share_revocation"] = "ok"
            report["share_test_record"] = "revoked; audit row remains in Supabase"
            token = None

    report["status"] = "passed"
    if not share_write:
        report["share_write_probe"] = "skipped; rerun with --share-write to create and revoke one test share"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only M8 cloud smoke check")
    parser.add_argument("--share-write", action="store_true")
    parser.add_argument("--search-en", default="photon")
    parser.add_argument("--search-zh", default="光子打向")
    args = parser.parse_args()
    result = smoke(args.share_write, args.search_en, args.search_zh)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print(json.dumps({"status": "cancelled"}), file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        # Avoid printing exception text that could contain a signed media URL.
        code = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print(json.dumps({"status": "failed", "code": code}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
