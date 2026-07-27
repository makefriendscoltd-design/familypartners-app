"""글감 예약 큐(config/drops_queue.json) → DB 등록. 배포 때 자동 실행된다.

이 서버는 git push 로만 코드가 갱신된다(autodeploy.sh). 그래서 글감도 파일로
예약해두면 배포와 함께 등록되게 한다 — 대시보드에 직접 입력하지 않아도 되고,
글감을 미리 만들어 두었다가 날짜만 정해 밀어 넣을 수 있다.

안전장치
  - 한 번 등록한 항목은 settings 에 표식을 남기고 다시는 넣지 않는다.
    DB 존재 여부가 아니라 표식으로 판단하므로, 운영자가 대시보드에서 지운 글감이
    다음 배포 때 되살아나지 않는다. (2분마다 도는 타이머에서 특히 중요)
  - 큐 파일이 없거나 깨져도 조용히 0 을 반환한다(배포를 막지 않는다)
  - 등록 후 큐 파일을 지우지 않는다. 수정·삭제는 대시보드에서 한다.

실행:
    FP_DB=/home/ubuntu/familypartners/data/challenge.db python3 deploy/seed_drops.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fp import core, db  # noqa: E402

QUEUE = ROOT / "config" / "drops_queue.json"


def load_queue() -> list[dict]:
    if not QUEUE.exists():
        return []
    try:
        data = json.loads(QUEUE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[seed_drops] 큐 파일을 읽지 못했습니다: {e}")
        return []
    items = data.get("drops") if isinstance(data, dict) else data
    return [d for d in (items or []) if isinstance(d, dict)]


def main() -> int:
    items = load_queue()
    if not items:
        return 0

    db.init_db()
    conn = db.connect()
    added = skipped = bad = 0
    try:
        for d in items:
            title = (d.get("title") or "").strip()
            date = (d.get("date") or "").strip()
            if not title or not date:
                bad += 1
                continue
            # 표식으로 판단한다 — DB에 있느냐가 아니라 '전에 넣은 적 있느냐'.
            # 그래야 운영자가 지운 글감이 되살아나지 않는다.
            mark = f"seeded_drop:{date}:{title}"
            if core.get_setting(conn, mark):
                skipped += 1
                continue
            # 표식 도입 전에 이미 들어간 건도 중복으로 넣지 않는다(1회성 보정).
            if conn.execute("SELECT 1 FROM drops WHERE drop_date=? AND title=? LIMIT 1",
                            (date, title)).fetchone():
                core.set_setting(conn, mark, "pre-existing")
                skipped += 1
                continue
            dtype = (d.get("type") or "ai").strip()
            if dtype not in ("ai", "marketing", "evergreen"):
                dtype = "ai"
            assets = d.get("assets") or None
            did = core.add_drop(conn, title, (d.get("body") or "").strip() or None,
                                assets, dtype, date,
                                target=d.get("target"), fmt=d.get("fmt"))
            core.set_setting(conn, mark, core.now_iso())
            print(f"[seed_drops] 등록 #{did} {date} "
                  f"[{d.get('target') or '-'}][{d.get('fmt') or '-'}] {title[:44]}")
            added += 1
    finally:
        conn.close()

    if added or bad:
        print(f"[seed_drops] 등록 {added} · 건너뜀 {skipped} · 형식오류 {bad}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:                    # 배포를 절대 막지 않는다
        print(f"[seed_drops] 실패(무시하고 계속): {type(e).__name__}: {e}")
        raise SystemExit(0)
