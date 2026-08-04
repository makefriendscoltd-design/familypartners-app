"""글감 예약 큐(config/drops_queue.json) → DB 등록 — 수동 실행용.

평소에는 서버 기동 시 fp/server.py 의 serve() 가 같은 작업을 자동으로 한다
(배포마다 서비스가 재시작되므로). 이 스크립트는 재시작 없이 큐만 즉시
반영하고 싶을 때 쓴다.

    FP_DB=/home/ubuntu/familypartners/data/challenge.db python3 deploy/seed_drops.py

한 번 등록한 항목은 표식이 남아 다시 들어가지 않는다. 운영자가 대시보드에서
지운 글감도 되살아나지 않는다.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fp import db, dropqueue  # noqa: E402


def main() -> int:
    db.init_db()
    s = dropqueue.sync()
    if not (s["added"] or s["updated"] or s["bad"]):
        print(f"[글감큐] 반영할 글감 없음 (건너뜀 {s['skipped']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
