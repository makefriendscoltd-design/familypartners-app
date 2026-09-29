#!/usr/bin/env python3
"""Apply a verified projection to the partner API without touching video state."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen


def payload(row, reviews):
    review = reviews.get(row["video_id"], {})
    status_map = {"원고없음":"missing", "작성중":"drafting", "발행대기":"queued",
                  "발행확인":"published_verified", "내용매칭검토필요":"match_review_required"}
    result = {"video_sha256":row["video_sha256"], "source_key":row["source_key"],
              "status":status_map[row["status"]], "topic_match_verified":row["clip_topic_match_verified"],
              "updated_at":datetime.now(timezone.utc).isoformat()}
    if row.get("clip_topic_match_verified"):
        result.update(clip_review_sha256=row.get("clip_review_sha256") or review.get("clip_review_sha256"),
                      article_sha256=row.get("article_sha256") or review.get("article_sha256"))
    if row["status"] == "발행확인":
        result.update(cafe_title=row["cafe_title"], cafe_url=row["cafe_url"],
                      clip_review_sha256=result["clip_review_sha256"],
                      article_sha256=result["article_sha256"])
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--projection',type=Path,required=True)
    p.add_argument('--match-reviews',type=Path,required=True);p.add_argument('--base-url',required=True)
    p.add_argument('--token-file',type=Path,required=True);p.add_argument('--apply',action='store_true')
    a=p.parse_args();projection=json.loads(a.projection.read_text());review_data=json.loads(a.match_reviews.read_text())
    reviews={x['video_id']:x for x in review_data['reviews']};planned=[]
    for row in projection['rows']:
        if row['status']=='출처확인필요': continue
        planned.append((row['video_id'],payload(row,reviews)))
    if not a.apply:
        print(json.dumps({'status':'dry_run','count':len(planned),'published_verified':sum(x[1]['status']=='published_verified' for x in planned)},ensure_ascii=False));return
    token=a.token_file.read_text().strip()
    for video_id,body in planned:
        request=Request(a.base_url.rstrip('/')+f'/op/videos/cafe/{video_id}',data=json.dumps(body).encode(),method='POST',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'})
        with urlopen(request,timeout=30) as response:
            if response.status != 200: raise RuntimeError(f'http_{response.status}_video_{video_id}')
    print(json.dumps({'status':'applied','count':len(planned)},ensure_ascii=False))


if __name__=='__main__': main()
