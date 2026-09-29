#!/usr/bin/env python3
"""Build a read-only partner-video -> Cafe projection from canonical evidence.

This never writes the Cafe queue or the partner provider.  A Cafe URL is exposed
only when the queue says published and a hash-bound clip/article review passes.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path

YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
CAFE_URL = re.compile(r"^https://cafe\.naver\.com/[^/?#]+/\d+$")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def valid_clip_review(path: Path, video_sha: str) -> dict | None:
    if not path.is_file():
        return None
    review = load(path)
    if review.get("schema_version") != 1 or review.get("video_sha256") != video_sha:
        return None
    if review.get("semantic_review") != "pass":
        return None
    evidence = review.get("evidence") or []
    if not evidence:
        return None
    for item in evidence:
        source = Path(item.get("path", ""))
        excerpt = item.get("excerpt", "")
        if not source.is_file() or digest(source) != item.get("sha256"):
            return None
        raw = source.read_text(encoding="utf-8")
        if source.suffix == ".json":
            document = load(source)
            captions = document.get("captions", []) if isinstance(document, dict) else []
            raw = " ".join(x.get("text", "") for x in captions if isinstance(x, dict))
        normalize = lambda value: re.sub(r"[^0-9A-Za-z가-힣]", "", value)
        if normalize(excerpt) not in normalize(raw):
            return None
    return review


def valid_match_review(review: dict | None, *, video_id: int, video_sha: str,
                       source_key: str, clip_review_path: Path,
                       article_path: Path | None) -> bool:
    if not review or review.get("verdict") != "pass" or not article_path:
        return False
    if not article_path.is_file() or not clip_review_path.is_file():
        return False
    clip_document = json.dumps(load(clip_review_path), ensure_ascii=False)
    return all((
        review.get("video_id") == video_id,
        review.get("video_sha256") == video_sha,
        review.get("source_key") == source_key,
        review.get("clip_review_sha256") == digest(clip_review_path),
        review.get("article_path") == str(article_path),
        review.get("article_sha256") == digest(article_path),
        bool(review.get("clip_excerpt")),
        bool(review.get("article_excerpt")),
        review.get("clip_excerpt") in clip_document,
        review.get("article_excerpt") in article_path.read_text(encoding="utf-8"),
    ))


def valid_coverage_item(item: dict | None, *, video_id: int, video_sha: str,
                        source_key: str, clip_review_path: Path,
                        article_path: Path | None) -> dict | None:
    """Accept only literal clip/article excerpts bound to the current bytes."""
    if not item or item.get("coverage") != "pass" or not article_path or not article_path.is_file():
        return None
    if item.get("source_key") != source_key or item.get("partner_video_id") != video_id:
        return None
    if item.get("partner_video_sha256") != video_sha:
        return None
    if Path(item.get("caption_evidence", "")).resolve() != clip_review_path.resolve():
        return None
    if item.get("caption_evidence_sha256") != digest(clip_review_path):
        return None
    if Path(item.get("body", "")).resolve() != article_path.resolve():
        return None
    if item.get("body_sha256") != digest(article_path):
        return None
    clip_excerpt, article_excerpt = item.get("clip_excerpt", ""), item.get("article_excerpt", "")
    if not clip_excerpt or not article_excerpt:
        return None
    normalize = lambda value: re.sub(r"[^0-9A-Za-z가-힣]", "", value)
    if normalize(clip_excerpt) not in normalize(json.dumps(load(clip_review_path), ensure_ascii=False)):
        return None
    if article_excerpt not in article_path.read_text(encoding="utf-8"):
        return None
    return {"clip_review_sha256": digest(clip_review_path),
            "article_sha256": digest(article_path)}


def cafe_record(entry: dict | None, project: Path, source_key: str,
                fresh_urls: set[str], recovery: dict | None = None,
                topic_item: dict | None = None) -> dict:
    if not entry:
        if recovery and recovery.get("classification") == "existing_draft_missing_queue":
            cafe = recovery.get("cafe") or {}
            return {"status": "작성중", "title": cafe.get("title"),
                    "article_path": cafe.get("body_path")}
        if topic_item:
            cafe = topic_item.get("cafe") or {}
            return {"status": "작성중", "title": cafe.get("title"),
                    "article_path": cafe.get("body")}
        return {"status": "원고없음"}
    manifest_path = project / entry.get("manifest", "")
    manifest = load(manifest_path) if manifest_path.is_file() else {}
    body_path = manifest_path.parent / manifest.get("body_file", "") if manifest else None
    url = entry.get("published_url")
    if entry.get("status") == "published" and isinstance(url, str) and CAFE_URL.fullmatch(url):
        evidence_path = project / entry.get("provider_evidence", "")
        evidence = load(evidence_path) if evidence_path.is_file() else {}
        public = evidence.get("publicVerification") or {}
        verified = all((evidence.get("sourceKey") == source_key,
                        evidence.get("status") in ("published", "published_verified"),
                        evidence.get("providerUrl") == url,
                        public.get("status") == "verified",
                        public.get("titleExact") is True,
                        int(public.get("oglinks", 0)) >= 1,
                        int(public.get("embeds", 0)) >= 1,
                        url in fresh_urls))
        return {"status": "발행확인" if verified else "내용매칭검토필요",
                "url": url if verified else None, "title": manifest.get("title"),
                "article_path": str(body_path) if body_path and body_path.is_file() else None}
    if entry.get("status") in ("pending", "failed"):
        state = "발행대기"
    elif entry.get("status") in ("blocked", "reconcile_required"):
        state = "내용매칭검토필요"
    else:
        state = "작성중"
    return {"status": state, "title": manifest.get("title"),
            "article_path": str(body_path) if body_path and body_path.is_file() else None}


def build(partner_inventory: Path, queue_path: Path, caption_reviews: Path,
          match_reviews_path: Path, cafe_project: Path, recovery_report: Path | None = None,
          public_verification: Path | None = None, topic_report: Path | None = None,
          coverage_report: Path | None = None) -> dict:
    partner = load(partner_inventory)["items"]
    current = [x for x in partner if x.get("distribution") in ("available", "queued")]
    queue = load(queue_path)
    by_source = {x.get("source_key"): x for x in queue.get("entries", [])}
    match_reviews = load(match_reviews_path).get("reviews", []) if match_reviews_path.is_file() else []
    match_by_video = {x.get("video_id"): x for x in match_reviews}
    recoveries = load(recovery_report).get("items", []) if recovery_report and recovery_report.is_file() else []
    recovery_by_source = {x.get("source_key"): x for x in recoveries}
    topic_items = load(topic_report).get("items", []) if topic_report and topic_report.is_file() else []
    topic_by_source = {x.get("source_key"): x for x in topic_items}
    coverage_items = load(coverage_report).get("items", []) if coverage_report and coverage_report.is_file() else []
    coverage_by_video = {x.get("partner_video_id"): x for x in coverage_items}
    public_pages = load(public_verification).get("pages", []) if public_verification and public_verification.is_file() else []
    fresh_urls = {x.get("finalUrl") for x in public_pages if x.get("status") == "read" and CAFE_URL.fullmatch(x.get("finalUrl", ""))}
    alignment_module = cafe_project / "cafe_shorts_alignment.py"
    alignment = None
    if alignment_module.is_file():
        spec = importlib.util.spec_from_file_location("fp_cafe_shorts_alignment", alignment_module)
        alignment = importlib.util.module_from_spec(spec);spec.loader.exec_module(alignment)
    rows = []
    for item in sorted(current, key=lambda x: x["video_id"]):
        source_key = item.get("source_key")
        video_id = item["video_id"]
        video_sha = item["video_sha256"]
        if not isinstance(source_key, str) or not YT_ID.fullmatch(source_key):
            rows.append({**item, "status": "출처확인필요", "cafe_url": None})
            continue
        clip_path = caption_reviews / f"{video_id}.json"
        clip_review = valid_clip_review(clip_path, video_sha)
        cafe = cafe_record(by_source.get(source_key), cafe_project, source_key,
                           fresh_urls, recovery_by_source.get(source_key), topic_by_source.get(source_key))
        article_path = Path(cafe["article_path"]) if cafe.get("article_path") else None
        explicit = match_by_video.get(video_id)
        match_hashes = None
        if clip_review and valid_match_review(explicit, video_id=video_id, video_sha=video_sha,
                                              source_key=source_key, clip_review_path=clip_path,
                                              article_path=article_path):
            match_hashes = {"clip_review_sha256": explicit["clip_review_sha256"],
                            "article_sha256": explicit["article_sha256"]}
        if clip_review and not match_hashes:
            match_hashes = valid_coverage_item(coverage_by_video.get(video_id), video_id=video_id,
                                               video_sha=video_sha, source_key=source_key,
                                               clip_review_path=clip_path, article_path=article_path)
        matched = bool(match_hashes)
        status = cafe["status"]
        coverage = coverage_by_video.get(video_id)
        if coverage and coverage.get("coverage") != "pass" and status in ("작성중", "발행대기", "발행확인"):
            status = "내용매칭검토필요"
        if status == "발행확인" and not matched:
            status = "내용매칭검토필요"
        if status == "발행대기":
            entry = by_source.get(source_key)
            shorts_proof = alignment.verified_shorts(cafe_project, entry) if alignment and entry else None
            detail_reason = "verified_shorts_alignment_ready" if shorts_proof else "shorts_alignment_evidence_waiting"
        elif status == "작성중":
            detail_reason = "article_or_provider_gates_incomplete"
        elif status == "원고없음":
            detail_reason = "cafe_article_missing"
        elif status == "내용매칭검토필요":
            detail_reason = "provider_or_clip_topic_verification_incomplete"
        else:
            detail_reason = "published_provider_and_clip_topic_verified"
        rows.append({
            "video_id": video_id, "video_sha256": video_sha,
            "source_key": source_key, "distribution": item["distribution"],
            "status": status, "clip_evidence_verified": bool(clip_review),
            "detail_reason": detail_reason,
            "clip_topic_match_verified": matched,
            "clip_review_sha256": match_hashes.get("clip_review_sha256") if match_hashes else None,
            "article_sha256": match_hashes.get("article_sha256") if match_hashes else None,
            "cafe_title": cafe.get("title") if matched else None,
            "cafe_url": cafe.get("url") if matched else None,
        })
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
            "scope": "current_available_and_queued_partner_videos",
            "source_count": len({x.get("source_key") for x in rows}),
            "video_count": len(rows), "counts": counts, "rows": rows}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--partner-inventory", type=Path, required=True)
    p.add_argument("--queue", type=Path, required=True)
    p.add_argument("--caption-reviews", type=Path, required=True)
    p.add_argument("--match-reviews", type=Path, required=True)
    p.add_argument("--cafe-project", type=Path, required=True)
    p.add_argument("--recovery-report", type=Path)
    p.add_argument("--public-verification", type=Path)
    p.add_argument("--topic-report", type=Path)
    p.add_argument("--coverage-report", type=Path)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    result = build(a.partner_inventory, a.queue, a.caption_reviews,
                   a.match_reviews, a.cafe_project, a.recovery_report,
                   a.public_verification, a.topic_report, a.coverage_report)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.output.with_suffix(a.output.suffix + ".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(a.output)
    print(json.dumps({k: result[k] for k in ("video_count", "source_count", "counts")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
