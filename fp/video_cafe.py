"""Hash-bound Cafe article metadata for partner videos."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

CAFE_URL = re.compile(r"^https://cafe\.naver\.com/[^/?#]+/\d+$")
SOURCE_KEY = re.compile(r"^[A-Za-z0-9_-]{11}$")


def record(conn, video_id):
    return conn.execute(
        "SELECT m.* FROM video_cafe_matches m JOIN exclusive_videos v ON v.id=m.video_id "
        "AND v.sha256=m.video_sha256 WHERE m.video_id=?", (video_id,)).fetchone()


def public_record(conn, video_id):
    row = record(conn, video_id)
    if not row or row["status"] != "published_verified" or not row["topic_match_verified"]:
        return None
    if not CAFE_URL.fullmatch(row["cafe_url"] or ""):
        return None
    return row


def upsert(conn, video, data):
    if data.get("video_sha256") != video["sha256"]:
        raise ValueError("video mismatch")
    source_key = data.get("source_key", "")
    if not SOURCE_KEY.fullmatch(source_key):
        raise ValueError("invalid source key")
    if "source_url" in video.keys() and video["source_url"]:
        parsed = urlparse(video["source_url"])
        current = (parse_qs(parsed.query).get("v") or [parsed.path.rsplit("/", 1)[-1]])[0]
        if current != source_key:
            raise ValueError("source mismatch")
    status = data.get("status")
    if status not in ("missing", "drafting", "queued", "published_verified", "match_review_required"):
        raise ValueError("invalid cafe status")
    matched = data.get("topic_match_verified") is True
    url, title = data.get("cafe_url"), data.get("cafe_title")
    if status == "published_verified":
        if not matched or not CAFE_URL.fullmatch(url or "") or not title:
            raise ValueError("unverified cafe match")
    else:
        url = title = None
    required_hashes = ("clip_review_sha256", "article_sha256")
    if matched and not all(re.fullmatch(r"[a-f0-9]{64}", data.get(k, "")) for k in required_hashes):
        raise ValueError("missing match hashes")
    conn.execute(
        "INSERT INTO video_cafe_matches(video_id,video_sha256,source_key,status,cafe_title,cafe_url,"
        "topic_match_verified,clip_review_sha256,article_sha256,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(video_id) DO UPDATE SET video_sha256=excluded.video_sha256,source_key=excluded.source_key,"
        "status=excluded.status,cafe_title=excluded.cafe_title,cafe_url=excluded.cafe_url,"
        "topic_match_verified=excluded.topic_match_verified,clip_review_sha256=excluded.clip_review_sha256,"
        "article_sha256=excluded.article_sha256,updated_at=excluded.updated_at",
        (video["id"], video["sha256"], source_key, status, title, url, int(matched),
         data.get("clip_review_sha256"), data.get("article_sha256"), data["updated_at"]),
    )
    conn.commit()


def public_payload(conn, video_id):
    row = public_record(conn, video_id)
    if not row:
        return None
    return {"title": row["cafe_title"], "url": row["cafe_url"], "source_key": row["source_key"]}
