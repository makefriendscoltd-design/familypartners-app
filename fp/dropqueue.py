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

표식 값은 큐 항목의 지문(본문·타겟·형태·종류의 해시)이다. 큐에서 본문을 고쳐
다시 push 하면 지문이 달라지므로 이미 등록된 행을 갱신한다. 이때 **행이 있을 때만**
UPDATE 하므로 지운 글감은 여전히 되살아나지 않고, 큐를 그대로 둔 채 운영자가
사이트에서 손으로 고친 글감도 지문이 그대로라 덮어쓰이지 않는다.
"""
from __future__ import annotations

import hashlib
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


def _fingerprint(d: dict) -> str:
    """큐 항목의 내용 지문. 본문을 고치면 값이 달라진다."""
    raw = "\x1f".join((d.get(k) or "").strip()
                      for k in ("body", "target", "fmt", "type"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _cleanup_dup_cta_titles(conn) -> int:
    """1회성 정리 — 2026-08-04 에 큐의 타이틀에 '/ 댓글키워드: ○○' 를 덧붙여 push 했다가
    표식(`seeded_drop:<date>:<title>`)이 어긋나 8/5~8/9 글감이 두 벌 등록됐다.
    그때 생긴 행만 골라 지운다. 아직 공개 전(미래 날짜) 이라 제출과 얽힌 게 없다.
    한 번 돌면 표식이 남아 다시 돌지 않는다. 다음 정리 때 지워도 되는 코드."""
    if core.get_setting(conn, "cleanup:dup_cta_titles_20260804"):
        return 0
    rows = conn.execute(
        "SELECT id FROM drops WHERE title LIKE ? OR title LIKE ?",
        ("%/ 댓글키워드:%", "%/ 댓글유도:%")).fetchall()
    for r in rows:
        conn.execute("DELETE FROM drops WHERE id=?", (r["id"],))
    conn.commit()
    core.set_setting(conn, "cleanup:dup_cta_titles_20260804", f"{len(rows)}건 삭제")
    if rows:
        print(f"[글감큐] 중복 등록분 {len(rows)}건 정리")
    return len(rows)


def pending(path: Path | None = None) -> int:
    """큐에 있으나 아직 반영되지 않은(표식이 없거나 지문이 다른) 항목 수."""
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
                if not title or not date:
                    continue
                if core.get_setting(
                        conn, f"seeded_drop:{date}:{title}") != _fingerprint(d):
                    n += 1
            return n
        finally:
            conn.close()
    except Exception:
        return -1        # 알 수 없음


def sync(path: Path | None = None, verbose: bool = True) -> dict:
    """큐 → DB. {'added': n, 'skipped': n, 'bad': n} 반환. 예외를 밖으로 던지지 않는다."""
    stats = {"added": 0, "updated": 0, "skipped": 0, "bad": 0}
    try:
        items = _load(path or QUEUE_PATH)
        if not items:
            return stats
        conn = db.connect()
        try:
            _cleanup_dup_cta_titles(conn)
            for d in items:
                title = (d.get("title") or "").strip()
                date = (d.get("date") or "").strip()
                if not title or not date:
                    stats["bad"] += 1
                    continue
                mark = f"seeded_drop:{date}:{title}"
                fp = _fingerprint(d)
                seen = core.get_setting(conn, mark)
                if seen == fp:
                    stats["skipped"] += 1
                    continue
                dtype = (d.get("type") or "ai").strip()
                if dtype not in VALID_TYPES:
                    dtype = "ai"
                body = (d.get("body") or "").strip() or None
                target = (d.get("target") or "").strip() or None
                fmt = (d.get("fmt") or "").strip() or None
                row = conn.execute(
                    "SELECT id FROM drops WHERE drop_date=? AND title=? LIMIT 1",
                    (date, title)).fetchone()
                if row is not None:
                    # 이미 있는 글감 — 큐가 바뀌었으니 내용만 갈아끼운다.
                    # (표식이 없던 옛 항목도 여기로 와서 지문만 새로 남긴다)
                    if seen:
                        conn.execute(
                            "UPDATE drops SET body=?, target=?, fmt=?, dtype=? "
                            "WHERE drop_date=? AND title=?",
                            (body, target, fmt, dtype, date, title))
                        conn.commit()
                        stats["updated"] += 1
                        if verbose:
                            print(f"[글감큐] 갱신 #{row['id']} {date} {title[:44]}")
                    else:
                        stats["skipped"] += 1
                    core.set_setting(conn, mark, fp)
                    continue
                if seen:
                    # 전에 넣었는데 행이 없다 = 운영자가 지운 것. 되살리지 않는다.
                    core.set_setting(conn, mark, fp)
                    stats["skipped"] += 1
                    continue
                did = core.add_drop(conn, title, body,
                                    d.get("assets") or None, dtype, date,
                                    target=target, fmt=fmt)
                core.set_setting(conn, mark, fp)
                stats["added"] += 1
                if verbose:
                    print(f"[글감큐] 등록 #{did} {date} "
                          f"[{target or '-'}][{fmt or '-'}] {title[:44]}")
        finally:
            conn.close()
        if verbose and (stats["added"] or stats["updated"] or stats["bad"]):
            print(f"[글감큐] 등록 {stats['added']} · 갱신 {stats['updated']} "
                  f"· 건너뜀 {stats['skipped']} · 형식오류 {stats['bad']}")
    except Exception as e:                     # 서버 기동을 절대 막지 않는다
        print(f"[글감큐] 처리 실패(무시하고 계속): {type(e).__name__}: {e}")
    return stats
