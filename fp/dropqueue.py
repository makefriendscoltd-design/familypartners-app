"""글감 예약 큐 — config/drops_queue.json 을 DB에 반영한다.

왜 앱 안에 있나
---------------
이 서버는 SSH 없이 `git push → 서버 폴링` 으로만 갱신된다. 그래서 글감도 파일로
예약해 코드와 함께 밀어 넣는다. 처음엔 autodeploy.sh 에서 호출했는데, 그 스크립트는
실행 도중 `git reset --hard` 로 자기 자신을 덮어쓰기 때문에 새로 추가한 뒷부분이
그 사이클에 실행되지 않는다. 서비스 재시작은 배포마다 확실히 일어나므로,
serve() 시작 시점에 처리하는 편이 훨씬 안정적이다.

멱등성
------
등록한 항목은 settings 에 `seeded_drop:<date>:<title>` 표식을 남긴다. DB에 있느냐가
아니라 '전에 넣은 적 있느냐'로 판단하므로, 운영자가 대시보드에서 지운 글감이
다음 재시작 때 되살아나지 않는다.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import core, db

QUEUE_PATH = Path(__file__).resolve().parent.parent / "config" / "drops_queue.json"
VALID_TYPES = ("ai", "marketing", "evergreen")


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[글감큐] 파일을 읽지 못했습니다: {e}")
        return []
    items = data.get("drops") if isinstance(data, dict) else data
    return [d for d in (items or []) if isinstance(d, dict)]


def pending(path: Path | None = None) -> int:
    """큐에 있으나 아직 등록되지 않은(표식 없는) 항목 수."""
    try:
        items = _load(path or QUEUE_PATH)
        if not items:
            return 0
        conn = db.connect()
        try:
            n = 0
            for d in items:
                title = (d.get("title") or "").strip()
                date = (d.get("date") or "").strip()
                if title and date and not core.get_setting(
                        conn, f"seeded_drop:{date}:{title}"):
                    n += 1
            return n
        finally:
            conn.close()
    except Exception:
        return -1        # 알 수 없음


def sync(path: Path | None = None, verbose: bool = True) -> dict:
    """큐 → DB. {'added': n, 'skipped': n, 'bad': n} 반환. 예외를 밖으로 던지지 않는다."""
    stats = {"added": 0, "skipped": 0, "bad": 0}
    try:
        items = _load(path or QUEUE_PATH)
        if not items:
            return stats
        conn = db.connect()
        try:
            for d in items:
                title = (d.get("title") or "").strip()
                date = (d.get("date") or "").strip()
                if not title or not date:
                    stats["bad"] += 1
                    continue
                mark = f"seeded_drop:{date}:{title}"
                if core.get_setting(conn, mark):
                    stats["skipped"] += 1
                    continue
                # 표식 도입 전에 이미 등록된 건 — 표식만 남기고 넘어간다.
                if conn.execute("SELECT 1 FROM drops WHERE drop_date=? AND title=? LIMIT 1",
                                (date, title)).fetchone():
                    core.set_setting(conn, mark, "pre-existing")
                    stats["skipped"] += 1
                    continue
                dtype = (d.get("type") or "ai").strip()
                if dtype not in VALID_TYPES:
                    dtype = "ai"
                did = core.add_drop(conn, title, (d.get("body") or "").strip() or None,
                                    d.get("assets") or None, dtype, date,
                                    target=d.get("target"), fmt=d.get("fmt"))
                core.set_setting(conn, mark, core.now_iso())
                stats["added"] += 1
                if verbose:
                    print(f"[글감큐] 등록 #{did} {date} "
                          f"[{d.get('target') or '-'}][{d.get('fmt') or '-'}] {title[:44]}")
        finally:
            conn.close()
        if verbose and (stats["added"] or stats["bad"]):
            print(f"[글감큐] 등록 {stats['added']} · 건너뜀 {stats['skipped']} "
                  f"· 형식오류 {stats['bad']}")
    except Exception as e:                     # 서버 기동을 절대 막지 않는다
        print(f"[글감큐] 처리 실패(무시하고 계속): {type(e).__name__}: {e}")
    return stats
