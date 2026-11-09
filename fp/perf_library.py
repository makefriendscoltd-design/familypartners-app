"""파트너 스레드 글의 공개 반응 수 수집·집계.

- GET  /op/perf/targets  (수집기 베어러) 최근 14일 유효 제출 글 목록
- POST /op/perf/report   (수집기 베어러) 반응 수 저장 — JSON {"results":[{id, likes, replies, reposts, quotes, shares, error}]}
- GET  /op/perf          (관리자) 글감별·형태별 반응 순위

스레드 조회수는 작성자 본인에게만 보여서 공개 반응(좋아요·답글·리포스트·인용·공유)을 지표로 쓴다.
수집기는 deploy/collect_post_perf.mjs (이 맥 launchd).
"""
from __future__ import annotations

import json
from datetime import timedelta
from html import escape as esc
from urllib.parse import urlparse

from . import core, db, video_library as vl

WINDOW_DAYS = 14
FIELDS = ("likes", "replies", "reposts", "quotes", "shares")


def _int(v):
    if v is None or v == "":
        return None
    n = int(v)
    if n < 0 or n > 10**9:
        raise ValueError("out of range")
    return n


def targets(conn, today=None):
    since = core.iso((today or core.today()) - timedelta(days=WINDOW_DAYS))
    rows = conn.execute(
        "SELECT id, post_url, post_date, drop_id, perf_at FROM submissions "
        "WHERE valid=1 AND post_date>=? ORDER BY post_date DESC, id DESC", (since,)).fetchall()
    out = []
    for r in rows:
        host = (urlparse(r["post_url"] or "").hostname or "").lower()
        if host.endswith("threads.net") or host.endswith("threads.com"):
            out.append({"id": r["id"], "url": r["post_url"], "post_date": r["post_date"],
                        "drop_id": r["drop_id"], "perf_at": r["perf_at"]})
    return out


def save(conn, results):
    saved = failed = 0
    now = core.now_iso()
    for item in results[:2000]:
        sid = int(item["id"])
        err = (item.get("error") or "").strip()[:200] or None
        if err:
            # 실패해도 이전에 받은 숫자는 지우지 않는다.
            conn.execute("UPDATE submissions SET perf_error=? WHERE id=?", (err, sid))
            failed += 1
            continue
        vals = [_int(item.get(k)) for k in FIELDS]
        conn.execute(
            "UPDATE submissions SET likes=?, comments=?, reposts=?, quotes=?, shares=?, "
            "perf_at=?, perf_error=NULL WHERE id=?", (*vals, now, sid))
        saved += 1
    conn.commit()
    return {"saved": saved, "failed": failed}


def _total(r):
    return sum((r[k] or 0) for k in ("likes", "comments", "reposts", "quotes", "shares"))


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return 0
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def report(conn, days=60):
    since = core.iso(core.today() - timedelta(days=days))
    rows = conn.execute(
        "SELECT s.id, s.post_url, s.post_date, s.drop_id, s.likes, s.comments, s.reposts, "
        "s.quotes, s.shares, s.perf_at, s.perf_error, d.title, d.fmt, d.target, d.drop_date "
        "FROM submissions s LEFT JOIN drops d ON d.id=s.drop_id "
        "WHERE s.valid=1 AND s.post_date>=? ORDER BY s.post_date DESC", (since,)).fetchall()
    measured = [r for r in rows if r["perf_at"]]
    by_drop, by_fmt = {}, {}
    for r in measured:
        key = r["drop_id"] or 0
        g = by_drop.setdefault(key, {"title": r["title"] or "(글감 선택 안 함)", "fmt": r["fmt"] or "",
                                     "date": r["drop_date"] or "", "totals": [], "best": None})
        t = _total(r)
        g["totals"].append(t)
        if g["best"] is None or t > g["best"][0]:
            g["best"] = (t, r["post_url"])
        by_fmt.setdefault(r["fmt"] or "(미분류)", []).append(t)
    drops = sorted(by_drop.values(), key=lambda g: (_median(g["totals"]), len(g["totals"])), reverse=True)
    fmts = sorted(((f, len(v), _median(v), max(v)) for f, v in by_fmt.items()),
                  key=lambda x: x[2], reverse=True)
    return {"rows": len(rows), "measured": len(measured),
            "errors": sum(1 for r in rows if r["perf_error"] and not r["perf_at"]),
            "last": max((r["perf_at"] for r in measured), default=None),
            "drops": drops, "fmts": fmts}


def admin_page(conn):
    rep = report(conn)
    last = rep["last"] or "아직 없음"
    head = (f"<div class=card><h2>글감별 반응 순위 (최근 60일)</h2>"
            f"<p class=meta>반응 = 좋아요+답글+리포스트+인용+공유. 스레드 조회수는 작성자만 볼 수 있어서 공개 반응으로 비교함. "
            f"순위는 중앙값(가운데 값) 기준 — 팔로워 많은 파트너 한 명 때문에 튀는 걸 줄이려고.</p>"
            f"<p>제출 글 {rep['rows']}개 · 측정됨 {rep['measured']}개 · 측정 실패 {rep['errors']}개 · 마지막 수집 {esc(str(last))}</p></div>")
    trs = "".join(
        f"<tr><td>{esc(g['date'])}</td><td>{esc(g['title'])}</td><td>{esc(g['fmt'])}</td>"
        f"<td style='text-align:right'>{len(g['totals'])}</td>"
        f"<td style='text-align:right'><b>{_median(g['totals']):g}</b></td>"
        f"<td style='text-align:right'>{max(g['totals'])}</td>"
        f"<td><a class=lk href='{esc(g['best'][1])}' target=_blank>최고 글 ↗</a></td></tr>"
        for g in rep["drops"])
    drop_tbl = ("<div class=card><h3>글감별</h3><table><tr><th>날짜</th><th>글감</th><th>형태</th>"
                "<th>글 수</th><th>반응 중앙값</th><th>최고</th><th></th></tr>" + (trs or
                "<tr><td colspan=7>아직 측정된 글이 없어요.</td></tr>") + "</table></div>")
    ftrs = "".join(f"<tr><td>{esc(f)}</td><td style='text-align:right'>{n}</td>"
                   f"<td style='text-align:right'><b>{m:g}</b></td><td style='text-align:right'>{mx}</td></tr>"
                   for f, n, m, mx in rep["fmts"])
    fmt_tbl = ("<div class=card><h3>글 형태별</h3><table><tr><th>형태</th><th>글 수</th>"
               "<th>반응 중앙값</th><th>최고</th></tr>" + ftrs + "</table></div>")
    return head + drop_tbl + fmt_tbl


def handle(h) -> bool:
    u = urlparse(h.path)
    if not (u.path == "/op/perf" or u.path.startswith("/op/perf/")):
        return False
    conn = None
    try:
        conn = db.connect()
        sync = vl.sync_authorized(h)
        if h.command == "GET" and u.path == "/op/perf/targets" and sync:
            vl.json_response(h, {"targets": targets(conn)})
        elif h.command == "POST" and u.path == "/op/perf/report" and sync:
            n = int(h.headers.get("Content-Length", 0) or 0)
            if n <= 0 or n > 1_000_000:
                vl.json_response(h, {"error": "bad length"}, 400)
                return True
            data = json.loads(h.rfile.read(n))
            vl.json_response(h, save(conn, data.get("results") or []))
        elif h.command == "GET" and u.path == "/op/perf" and h._admin_ok():
            vl.page(h, "글감 성과", admin_page(conn), admin=True)
        else:
            vl.response(h, "관리자 로그인이 필요해요.", 403)
    except (ValueError, KeyError, TypeError) as e:
        vl.json_response(h, {"error": str(e)[:200]}, 400)
    finally:
        if conn:
            conn.close()
    return True
