"""패밀리 파트너스 일일 챌린지 운영 CLI.

사용 예:
    python -m fp init
    python -m fp partner add --name 홍길동 --handle @gildong --contact 010-1234-5678
    python -m fp submit --who @gildong --url https://www.threads.net/@gildong/post/abc
    python -m fp ingest --csv data/submissions.csv
    python -m fp report                 # 오늘 운영 보드
    python -m fp reminders              # 위험군/강퇴후보 메시지 출력
    python -m fp partner kick --who 홍길동 --reason "2일 연속 미제출"
    python -m fp month --month 2026-06  # 월간 인증 리포트
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

from . import core, db, messages, onboard, products

_COLOR = sys.stdout.isatty()
def _c(code):
    return code if _COLOR else ""
C_RESET = _c("\033[0m"); C_DIM = _c("\033[2m"); C_GREEN = _c("\033[32m")
C_YEL = _c("\033[33m"); C_RED = _c("\033[31m"); C_BOLD = _c("\033[1m")


def _p(s=""):
    print(s)


# --------------------------------------------------------------------------- #
def cmd_init(_args):
    db.init_db()
    _p(f"DB 준비 완료 → {db.db_path()}")


def cmd_partner(args):
    conn = db.connect()
    if args.action == "add":
        pid = core.add_partner(conn, args.name, args.handle, args.contact, args.code, args.date)
        _p(f"파트너 등록: #{pid} {args.name} ({args.handle or '-'})")
    elif args.action == "list":
        q = "SELECT * FROM partners"
        params = ()
        if args.status:
            q += " WHERE status=?"; params = (args.status,)
        q += " ORDER BY id"
        rows = conn.execute(q, params).fetchall()
        _p(f"{'ID':>3}  {'이름':<10} {'핸들':<16} {'상태':<8} {'가입일':<11} 연락처")
        for r in rows:
            _p(f"{r['id']:>3}  {r['name']:<10} {str(r['handle'] or '-'):<16} "
               f"{r['status']:<8} {r['joined_date']:<11} {r['contact'] or '-'}")
        _p(f"\n총 {len(rows)}명")
    elif args.action in ("kick", "pause", "reactivate"):
        p = core.find_partner(conn, args.who)
        if not p:
            _p(f"파트너를 찾을 수 없음: {args.who}"); sys.exit(1)
        status = {"kick": "kicked", "pause": "paused", "reactivate": "active"}[args.action]
        core.set_status(conn, p["id"], status, args.date, args.reason)
        _p(f"{p['name']} → {status}" + (f" ({args.reason})" if args.reason else ""))
    elif args.action == "warn":
        p = core.find_partner(conn, args.who)
        if not p:
            _p(f"파트너를 찾을 수 없음: {args.who}"); sys.exit(1)
        core.warn(conn, p["id"], args.reason, args.date)
        _p(f"⚠ 경고 기록: {p['name']}" + (f" ({args.reason})" if args.reason else ""))
    elif args.action == "show":
        p = core.find_partner(conn, args.who)
        if not p:
            _p(f"파트너를 찾을 수 없음: {args.who}"); sys.exit(1)
        d = core.partner_detail(conn, p, core.parse_date(args.date))
        r = d["row"]
        _p(f"{C_BOLD}━━ {r['name']}  ({r['handle'] or '-'}) ━━{C_RESET}")
        _p(f"상태 {r['status']} · 가입 {r['joined_date']} · 연락처 {r['contact'] or '-'}")
        _p(f"현재 {d['streak']}일 연속 · 오늘 {'발행✅' if d['posted_today'] else '미발행⏳'} · "
           f"유효 {d['total_valid']}건 / 무효 {d['total_void']}건")
        _p(f"\n[제출 이력]")
        for s in d["submissions"][:15]:
            mark = "  " if s["valid"] else "✗ "
            extra = f"  ✗무효: {s['void_reason'] or ''}" if not s["valid"] else ""
            _p(f"  {mark}{s['post_date']} [{s['channel']}] {s['post_url']}{extra}")
        _p(f"\n[운영 이력]")
        for e in d["events"][:15]:
            _p(f"  {e['date']} {e['type']}" + (f" — {e['reason']}" if e["reason"] else ""))
    conn.close()


def cmd_submit(args):
    conn = db.connect()
    p = core.find_partner(conn, args.who)
    if not p:
        _p(f"파트너를 찾을 수 없음: {args.who}"); sys.exit(1)
    core.add_submission(conn, p["id"], args.url, args.channel, args.date, args.note)
    _p(f"제출 기록: {p['name']} [{args.channel}] {core.iso(core.parse_date(args.date))}")
    conn.close()


def cmd_ingest(args):
    """CSV 일괄 적재. 헤더: who,url[,channel,post_date,note]
       who = 이름 또는 @핸들 또는 id."""
    conn = db.connect()
    ok = skip = 0
    with open(args.csv, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            who = (row.get("who") or row.get("name") or row.get("핸들") or "").strip()
            url = (row.get("url") or row.get("post_url") or row.get("링크") or "").strip()
            if not who or not url:
                skip += 1; continue
            p = core.find_partner(conn, who)
            if not p:
                _p(f"  미등록 파트너 건너뜀: {who}"); skip += 1; continue
            core.add_submission(
                conn, p["id"], url,
                (row.get("channel") or "threads").strip() or "threads",
                (row.get("post_date") or "").strip() or None,
                (row.get("note") or "").strip() or None,
            )
            ok += 1
    _p(f"적재 완료: {ok}건 / 건너뜀 {skip}건")
    conn.close()


def cmd_report(args):
    conn = db.connect()
    as_of = core.parse_date(args.date)
    b = core.daily_board(conn, as_of)
    _p(f"{C_BOLD}━━━ 일일 챌린지 보드  {b['date']}  (활성 {b['active_count']}명) ━━━{C_RESET}")
    _p(f"\n{C_GREEN}✅ 오늘 완료 ({len(b['done'])}){C_RESET}")
    for s in b["done"]:
        _p(f"   {s.name:<10} {C_DIM}{s.handle or '-'}{C_RESET}  🔥{s.streak}일 연속")
    _p(f"\n{C_YEL}⏳ 위험군 — 오늘 아직 미제출 ({len(b['at_risk'])}){C_RESET}")
    for s in b["at_risk"]:
        _p(f"   {s.name:<10} {C_DIM}{s.handle or '-'}{C_RESET}  현재 {s.streak}일 연속 · 마감 전 리마인더 필요")
    _p(f"\n{C_RED}⛔ 강퇴 후보 — 어제 빵꾸 ({len(b['kick'])}){C_RESET}")
    for s in b["kick"]:
        _p(f"   {s.name:<10} {C_DIM}{s.handle or '-'}{C_RESET}  {s.row['contact'] or '-'}")
    if not b["kick"]:
        _p(f"   {C_DIM}없음{C_RESET}")
    _p(f"\n{C_DIM}리마인더 메시지: python -m fp reminders"
       f"{' --date ' + args.date if args.date else ''}{C_RESET}")
    conn.close()


def cmd_reminders(args):
    conn = db.connect()
    as_of = core.parse_date(args.date)
    b = core.daily_board(conn, as_of)
    _p(f"━━━ 리마인더 ({b['date']}) ━━━")
    if b["at_risk"]:
        _p(f"\n[위험군 — 마감 전 독려]")
        for s in b["at_risk"]:
            tgt = s.row["contact"] or (s.handle or s.name)
            _p(f"\n▶ {s.name}  ({tgt})\n{messages.reminder(s.name, s.streak)}")
    if b["kick"]:
        _p(f"\n[강퇴 후보 — 경고]")
        for s in b["kick"]:
            tgt = s.row["contact"] or (s.handle or s.name)
            _p(f"\n▶ {s.name}  ({tgt})\n{messages.warning(s.name)}")
    if not b["at_risk"] and not b["kick"]:
        _p("\n보낼 메시지 없음 — 전원 오늘 완료 👍")
    conn.close()


def cmd_month(args):
    conn = db.connect()
    as_of = core.parse_date(None)
    if args.month:
        y, m = (int(x) for x in args.month.split("-"))
    else:
        y, m = as_of.year, as_of.month
    rep = core.month_report(conn, y, m, as_of)
    _p(f"{C_BOLD}━━━ {y}-{m:02d} 월간 인증 리포트 ━━━{C_RESET}")
    _p(f"{'이름':<10} {'상태':<8} {'달성':>9}  {'달성률':>6}  {'최장연속':>6}  게시물")
    for r in sorted(rep, key=lambda x: -x["rate"]):
        bar = "%d/%d" % (r["covered"], r["required"])
        _p(f"{r['name']:<10} {r['status']:<8} {bar:>9}  {r['rate']:>5}%  "
           f"{r['longest_streak']:>5}일  {r['posts']:>4}건")
    conn.close()


def cmd_onboard(args):
    conn = db.connect()
    p = core.find_partner(conn, args.who)
    if not p:
        _p(f"파트너를 찾을 수 없음: {args.who}"); sys.exit(1)
    kit = onboard.build_kit(p["name"], p["referral_code"], p["portal_token"],
                            p["handle"], onboard.links_of(p), p["openchat_url"])
    out_dir = Path(core.db.ROOT) / "out"
    out_dir.mkdir(exist_ok=True)
    fp = out_dir / f"onboard_{p['name']}.md"
    fp.write_text(kit, encoding="utf-8")
    if args.print:
        _p(kit)
    _p(f"\n온보딩 키트 저장 → {fp}")
    conn.close()


def cmd_drop(args):
    conn = db.connect()
    if args.action == "add":
        assets = [a.strip() for a in (args.assets or "").split(",") if a.strip()]
        did = core.add_drop(conn, args.title, args.body, assets, args.type, args.date)
        _p(f"글감 등록: #{did} [{args.type}] {args.title}"
           + (f" · 첨부 {len(assets)}개" if assets else ""))
    elif args.action == "list":
        rows = conn.execute("SELECT * FROM drops ORDER BY drop_date DESC, id DESC LIMIT 30").fetchall()
        for r in rows:
            n = len((r["assets"] or "").splitlines()) if r["assets"] else 0
            _p(f"{r['drop_date']}  [{r['dtype']:<9}] {r['title']}  (첨부 {n})")
    elif args.action == "today" or args.action == "show":
        d = core.parse_date(args.date)
        rows = core.drops_on(conn, d)
        if not rows:
            _p(f"{core.iso(d)} 에 등록된 글감이 없습니다."); conn.close(); return
        TYPE = {"ai": "AI 콘텐츠", "marketing": "마케팅 자료", "evergreen": "상시 자료"}
        for r in rows:
            _p(f"📦 오늘의 글감 — {r['drop_date']}  [{TYPE.get(r['dtype'], r['dtype'])}]")
            _p(f"제목: {r['title']}")
            if r["body"]:
                _p("─ 본문/캡션 (복사해서 본인 톤으로) ─")
                _p(r["body"])
            if r["assets"]:
                _p("─ 첨부 (다운로드) ─")
                for a in r["assets"].splitlines():
                    _p(f"  {a}")
            _p("※ 발행 후 게시물 링크를 운영방에 제출하세요.\n")
    conn.close()


def cmd_sale(args):
    conn = db.connect()
    if args.action == "add":
        p = core.find_partner(conn, args.who)
        if not p:
            _p(f"파트너를 찾을 수 없음: {args.who}"); sys.exit(1)
        if not products.find(args.product):
            _p(f"상품키를 찾을 수 없음: {args.product} "
               f"(가능: {', '.join(x['key'] for x in products.products())})"); sys.exit(1)
        core.add_sale(conn, p["id"], args.product, args.amount, args.date, args.ref)
        _p(f"매출 기록: {p['name']} · {args.product} · {products.won(int(args.amount))}")
    elif args.action == "ingest":
        ok = skip = 0
        with open(args.csv, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                who = (row.get("who") or row.get("name") or "").strip()
                pk = (row.get("product") or row.get("product_key") or "").strip()
                amt = (row.get("amount") or "").strip()
                p = core.find_partner(conn, who) if who else None
                if not p or not pk or not amt:
                    skip += 1; continue
                core.add_sale(conn, p["id"], pk, amt,
                              (row.get("sale_date") or "").strip() or None,
                              (row.get("order_ref") or "").strip() or None)
                ok += 1
        _p(f"매출 적재: {ok}건 / 건너뜀 {skip}건")
    conn.close()


def cmd_enforce(args):
    conn = db.connect()
    as_of = core.parse_date(args.date)
    targets = core.enforce(conn, as_of, dry_run=not args.yes)
    if not targets:
        _p("강퇴 대상 없음 — 어제 전원 발행 완료 👍"); conn.close(); return
    head = "강퇴 집행 완료" if args.yes else "강퇴 대상 (미리보기 — 실제 집행하려면 --yes)"
    _p(f"{C_RED}⛔ {head} ({len(targets)}명){C_RESET}")
    for t in targets:
        _p(f"   {t['name']:<10} {t['handle'] or '-'}  {t['contact'] or '-'}  → {t['month']} 수익 몰수")
    if args.yes:
        _p(f"\n{C_DIM}강퇴 + 당월 수익 몰수 처리됨.{C_RESET}")
    conn.close()


def cmd_settle(args):
    conn = db.connect()
    as_of = core.parse_date(None)
    if args.month:
        y, m = (int(x) for x in args.month.split("-"))
    else:
        y, m = as_of.year, as_of.month
    rep = core.settle(conn, y, m)
    _p(f"{C_BOLD}━━━ {y}-{m:02d} 월 정산 ━━━{C_RESET}")
    if not rep:
        _p("해당 월 매출 없음."); conn.close(); return
    _p(f"{'이름':<10} {'상태':<8} {'건수':>4} {'매출':>12} {'쉐어정산':>12}  비고")
    tot_g = tot_s = 0
    for r in sorted(rep, key=lambda x: -x["gross"]):
        note = "몰수" if r["forfeited"] else ""
        _p(f"{r['name']:<10} {r['status']:<8} {r['count']:>4} "
           f"{products.won(r['gross']):>12} {products.won(r['share']):>12}  {note}")
        tot_g += r["gross"]; tot_s += r["share"]
    _p(f"{'─'*52}")
    _p(f"{'합계':<10} {'':<8} {'':>4} {products.won(tot_g):>12} {products.won(tot_s):>12}")
    conn.close()


def cmd_review(args):
    conn = db.connect()
    d = core.parse_date(args.date)
    subs = core.submissions_on(conn, d)
    _p(f"{C_BOLD}━━━ 제출 검수  {core.iso(d)}  (총 {len(subs)}건) ━━━{C_RESET}")
    if not subs:
        _p("제출 없음."); conn.close(); return
    for s in subs:
        flags = []
        if not s["valid"]:
            flags.append(f"{C_RED}✗무효{C_RESET}")
        if s["dup"]:
            flags.append(f"{C_YEL}⚠중복{C_RESET}")
        tag = ("  " + " ".join(flags)) if flags else ""
        _p(f"  #{s['id']:<4} {s['name']:<8} [{s['channel']}] {s['url']}{tag}")
    _p(f"\n{C_DIM}무효 처리: python -m fp reject --id <번호> --reason \"사유\"{C_RESET}")
    conn.close()


def cmd_reject(args):
    conn = db.connect()
    row = core.reject_submission(conn, args.id, args.reason)
    if not row:
        _p(f"제출 #{args.id} 없음"); sys.exit(1)
    p = conn.execute("SELECT name FROM partners WHERE id=?", (row["partner_id"],)).fetchone()
    _p(f"✗ 제출 #{args.id} 무효 처리 ({p['name']}, {row['post_date']})"
       + (f" — {args.reason}" if args.reason else ""))
    _p(f"{C_DIM}해당 날짜 다른 유효 제출이 없으면 출석에서 빠집니다. report 로 확인.{C_RESET}")
    conn.close()


def cmd_board(args):
    conn = db.connect()
    lb = core.leaderboard(conn, core.parse_date(args.date))
    _p(f"{C_BOLD}━━━ 활동 랭킹 (연속일) ━━━{C_RESET}")
    medals = ["🥇", "🥈", "🥉"]
    for i, r in enumerate(lb):
        rank = medals[i] if i < 3 else f"{i+1:>2}"
        today = "✅" if r["posted_today"] else "⏳"
        _p(f" {rank} {r['name']:<8} {C_DIM}{r['handle'] or '-':<14}{C_RESET} "
           f"🔥{r['streak']:>2}일  오늘 {today}  누적 {r['total']}건")
    conn.close()


def cmd_notice(args):
    conn = db.connect()
    if args.action == "add":
        if not args.title:
            _p("--title 필요"); sys.exit(1)
        nid = core.add_notice(conn, args.title, args.body, args.date)
        _p(f"공지 게시: #{nid} {args.title}")
    elif args.action == "list":
        for r in core.all_notices(conn):
            mark = "" if r["active"] else " (내림)"
            _p(f"#{r['id']:<3} {r['notice_date']} {r['title']}{mark}")
    elif args.action == "off":
        core.set_notice_active(conn, args.id, 0)
        _p(f"공지 #{args.id} 내림")
    conn.close()


def cmd_serve(args):
    from . import server
    server.serve(args.port)


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="fp", description="패밀리 파트너스 일일 챌린지 운영")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="DB 초기화").set_defaults(func=cmd_init)

    pp = sub.add_parser("partner", help="파트너 관리")
    pp.set_defaults(func=cmd_partner)
    pp.add_argument("action",
                    choices=["add", "list", "show", "kick", "pause", "reactivate", "warn"])
    pp.add_argument("--name"); pp.add_argument("--handle"); pp.add_argument("--contact")
    pp.add_argument("--code", help="개인 추적코드(수익 어트리뷰션용)")
    pp.add_argument("--who", help="이름/@핸들/id")
    pp.add_argument("--status", choices=["active", "paused", "kicked"])
    pp.add_argument("--reason"); pp.add_argument("--date", help="YYYY-MM-DD")

    sp = sub.add_parser("submit", help="제출 1건 기록"); sp.set_defaults(func=cmd_submit)
    sp.add_argument("--who", required=True); sp.add_argument("--url", required=True)
    sp.add_argument("--channel", default="threads"); sp.add_argument("--date")
    sp.add_argument("--note")

    ip = sub.add_parser("ingest", help="CSV 일괄 적재"); ip.set_defaults(func=cmd_ingest)
    ip.add_argument("--csv", required=True)

    rp = sub.add_parser("report", help="오늘 운영 보드"); rp.set_defaults(func=cmd_report)
    rp.add_argument("--date")

    mp = sub.add_parser("reminders", help="리마인더/경고 메시지 출력")
    mp.set_defaults(func=cmd_reminders); mp.add_argument("--date")

    np = sub.add_parser("month", help="월간 달성 리포트"); np.set_defaults(func=cmd_month)
    np.add_argument("--month", help="YYYY-MM")

    op = sub.add_parser("onboard", help="파트너 온보딩 키트 생성(카톡방+프로필+개인링크)")
    op.set_defaults(func=cmd_onboard)
    op.add_argument("--who", required=True, help="이름/@핸들/id")
    op.add_argument("--print", action="store_true", help="화면에도 출력")

    dp = sub.add_parser("drop", help="일일 글감/소스 배포"); dp.set_defaults(func=cmd_drop)
    dp.add_argument("action", choices=["add", "today", "show", "list"])
    dp.add_argument("--title"); dp.add_argument("--body")
    dp.add_argument("--assets", help="파일경로/URL, 쉼표 구분")
    dp.add_argument("--type", default="ai", choices=["ai", "marketing", "evergreen"])
    dp.add_argument("--date", help="YYYY-MM-DD")

    sl = sub.add_parser("sale", help="매출 기록/적재"); sl.set_defaults(func=cmd_sale)
    sl.add_argument("action", choices=["add", "ingest"])
    sl.add_argument("--who"); sl.add_argument("--product"); sl.add_argument("--amount")
    sl.add_argument("--date"); sl.add_argument("--ref"); sl.add_argument("--csv")

    ep = sub.add_parser("enforce", help="규칙 집행: 누락자 즉시 강퇴+당월 몰수")
    ep.set_defaults(func=cmd_enforce)
    ep.add_argument("--date"); ep.add_argument("--yes", action="store_true", help="실제 집행")

    tp = sub.add_parser("settle", help="월 정산(매출·쉐어·몰수)"); tp.set_defaults(func=cmd_settle)
    tp.add_argument("--month", help="YYYY-MM")

    rv = sub.add_parser("review", help="제출 검수(중복·무효 플래그)"); rv.set_defaults(func=cmd_review)
    rv.add_argument("--date")

    rj = sub.add_parser("reject", help="제출 무효 처리(출석 미인정)"); rj.set_defaults(func=cmd_reject)
    rj.add_argument("--id", type=int, required=True); rj.add_argument("--reason")

    bd = sub.add_parser("board", help="활동 랭킹(연속일 리더보드)"); bd.set_defaults(func=cmd_board)
    bd.add_argument("--date")

    nt = sub.add_parser("notice", help="운영 공지 게시/목록/내림"); nt.set_defaults(func=cmd_notice)
    nt.add_argument("action", choices=["add", "list", "off"])
    nt.add_argument("--title"); nt.add_argument("--body")
    nt.add_argument("--id", type=int); nt.add_argument("--date")

    vp = sub.add_parser("serve", help="운영 대시보드 웹서버 실행"); vp.set_defaults(func=cmd_serve)
    vp.add_argument("--port", type=int, default=8000)
    return ap


def main(argv=None):
    try:  # 윈도우 콘솔(cp949)에서 한글/이모지 출력 깨짐 방지
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
