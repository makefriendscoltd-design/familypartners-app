"""일일 챌린지 판정 엔진.

규칙
----
- 파트너는 joined_date 부터 '매일' 콘텐츠를 1건 이상 제출해야 한다.
- 어떤 날(post_date)에 제출이 1건이라도 있으면 그 날은 '커버됨'.
- 오늘은 아직 마감 전이므로 강퇴 판정에서 제외(리마인더 대상).
- 어제(이전 필수일)가 커버 안 됐으면 = 위반 = 강퇴 후보.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from . import db

# 한국 표준시(UTC+9, DST 없음) — 모든 날짜/시각 판정 기준.
# Vercel 서버리스는 UTC라서 이걸 안 쓰면 한국 자정과 9시간 어긋난다.
KST = timezone(timedelta(hours=9))


# --------------------------------------------------------------------------- #
# 설정 · 관리자 비밀번호
# --------------------------------------------------------------------------- #
def get_setting(conn, key):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else None


def set_setting(conn, key, value) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def admin_is_set(conn) -> bool:
    return get_setting(conn, "admin_hash") is not None


def _hash_pw(pw: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 100_000).hex()


def set_admin_password(conn, pw: str) -> None:
    salt = secrets.token_hex(16)
    set_setting(conn, "admin_salt", salt)
    set_setting(conn, "admin_hash", _hash_pw(pw, salt))


def check_admin_password(conn, pw: str) -> bool:
    salt = get_setting(conn, "admin_salt")
    h = get_setting(conn, "admin_hash")
    if not salt or not h:
        return False
    return secrets.compare_digest(_hash_pw(pw, salt), h)


def now() -> datetime:
    return datetime.now(KST)


def now_iso() -> str:
    """저장용 타임스탬프(KST) — 오프셋 없이 기존 형식 유지."""
    return now().replace(tzinfo=None).isoformat(timespec="seconds")


def today() -> date:
    return now().date()


def parse_date(s: str | None) -> date:
    return today() if not s else datetime.strptime(s, "%Y-%m-%d").date()


def iso(d: date) -> str:
    return d.strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# 파트너 관리
# --------------------------------------------------------------------------- #
def gen_token() -> str:
    return secrets.token_urlsafe(6)


def gen_code() -> str:
    return "FP-" + secrets.token_hex(3).upper()


def gen_handle() -> str:
    # 신규 SNS 계정용 아이디 제안 (aimax_숫자4자리). 파트너가 이 아이디로 계정 생성.
    return "aimax_" + "".join(secrets.choice("0123456789") for _ in range(4))


def add_partner(conn, name, handle=None, contact=None, code=None, joined=None,
                sales_url=None, partner_type="family") -> int:
    handle = (handle or "").strip().lstrip("@") or None  # 자동생성 X — 본인이 만든 ID를 직접 입력
    if partner_type not in ("family", "aimax", "both"):
        partner_type = "family"
    j = iso(parse_date(joined))
    cur = conn.execute(
        "INSERT INTO partners(name, handle, contact, referral_code, joined_date, "
        "portal_token, sales_url, partner_type) VALUES (?,?,?,?,?,?,?,?)",
        (name, handle, contact, code, j, gen_token(), sales_url, partner_type),
    )
    pid = cur.lastrowid
    conn.execute(
        "INSERT INTO events(partner_id, type, date, reason) VALUES (?,?,?,?)",
        (pid, "join", j, None),
    )
    conn.commit()
    return pid


def self_register(conn, name, handle=None, contact=None, partner_type="family") -> sqlite3.Row:
    """파트너 셀프 등록(포털). 추적코드 자동 발급, 가입일=오늘. 등록된 행 반환."""
    add_partner(conn, name, handle, contact, code=gen_code(), joined=None,
                partner_type=partner_type)
    return conn.execute("SELECT * FROM partners WHERE name=?", (name,)).fetchone()


def is_rejoin_blocked(conn, name, contact) -> bool:
    """강퇴자 재참여 차단 — 이름 또는 연락처가 강퇴 명단과 일치하면 True."""
    name = (name or "").strip()
    contact = (contact or "").strip()
    if not name and not contact:
        return False
    for r in conn.execute("SELECT name, contact FROM partners WHERE status='kicked'"):
        if name and r["name"] == name:
            return True
        if contact and (r["contact"] or "") == contact:
            return True
    return False


def find_by_token(conn, token: str) -> sqlite3.Row | None:
    if not token:
        return None
    return conn.execute(
        "SELECT * FROM partners WHERE portal_token=?", (token,)
    ).fetchone()


def find_for_login(conn, name, contact):
    """파트너 재로그인 — 성함+연락처로 본인 찾기(비번 없는 토큰 방식 보완)."""
    name = (name or "").strip()
    contact = (contact or "").strip()
    if not name or not contact:
        return None
    return conn.execute(
        "SELECT * FROM partners WHERE name=? AND contact=?", (name, contact)
    ).fetchone()


def update_self(conn, token, handle=None, openchat=None) -> bool:
    """파트너가 자기 작업실에서 스레드 아이디·오픈톡방 입력(STEP 1·2)."""
    p = find_by_token(conn, token)
    if not p:
        return False
    h = (handle or "").strip().lstrip("@") or p["handle"]
    oc = (openchat or "").strip() or p["openchat_url"]
    conn.execute("UPDATE partners SET handle=?, openchat_url=? WHERE id=?", (h, oc, p["id"]))
    conn.commit()
    return True


def update_links(conn, token, links: dict) -> bool:
    """파트너가 STEP 3에서 유형별 판매링크 입력 → links_json 저장(빈값은 제거)."""
    import json
    p = find_by_token(conn, token)
    if not p:
        return False
    try:
        cur = json.loads(p["links_json"] or "{}") or {}
    except Exception:
        cur = {}
    for k, v in (links or {}).items():
        v = (v or "").strip()
        if v:
            cur[k] = v
        else:
            cur.pop(k, None)
    conn.execute("UPDATE partners SET links_json=? WHERE id=?",
                 (json.dumps(cur, ensure_ascii=False), p["id"]))
    conn.commit()
    return True


def set_type(conn, pid, ptype) -> None:
    if ptype not in ("family", "aimax", "both"):
        ptype = "family"
    conn.execute("UPDATE partners SET partner_type=? WHERE id=?", (ptype, pid))
    conn.commit()


def posted_today(conn, pid, as_of: date) -> bool:
    return iso(as_of) in covered_dates(conn, pid)


def streak_for(conn, row, as_of: date) -> int:
    joined = datetime.strptime(row["joined_date"], "%Y-%m-%d").date()
    return current_streak(conn, row["id"], joined, as_of)


# --------------------------------------------------------------------------- #
# 제출 검수 · 중복 탐지
# --------------------------------------------------------------------------- #
def norm_url(u: str) -> str:
    return (u or "").strip().lower().rstrip("/").split("?")[0]


def duplicate_url_set(conn) -> set[str]:
    """유효 제출 중 2회 이상 등장한 URL(정규화). 재탕/베끼기 탐지용."""
    rows = conn.execute("SELECT post_url FROM submissions WHERE valid=1").fetchall()
    seen, dup = set(), set()
    for r in rows:
        n = norm_url(r["post_url"])
        if n in seen:
            dup.add(n)
        seen.add(n)
    return dup


def submissions_on(conn, d: date) -> list[dict]:
    """특정 날짜 제출 목록(검수용). 파트너명·유효여부·중복플래그 포함."""
    rows = conn.execute(
        "SELECT s.*, p.name, p.handle FROM submissions s "
        "JOIN partners p ON p.id=s.partner_id WHERE s.post_date=? ORDER BY s.id",
        (iso(d),),
    ).fetchall()
    dups = duplicate_url_set(conn)
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "partner_id": r["partner_id"],
            "name": r["name"], "handle": r["handle"],
            "url": r["post_url"], "channel": r["channel"],
            "valid": r["valid"], "void_reason": r["void_reason"],
            "submitted_at": r["submitted_at"],
            "dup": norm_url(r["post_url"]) in dups,
        })
    return out


def reject_submission(conn, sub_id, reason=None) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM submissions WHERE id=?", (sub_id,)).fetchone()
    if not row:
        return None
    conn.execute("UPDATE submissions SET valid=0, void_reason=? WHERE id=?", (reason, sub_id))
    conn.execute("INSERT INTO events(partner_id, type, date, reason) VALUES (?,?,?,?)",
                 (row["partner_id"], "reject", row["post_date"], reason))
    conn.commit()
    return row


def restore_submission(conn, sub_id) -> None:
    conn.execute("UPDATE submissions SET valid=1, void_reason=NULL WHERE id=?", (sub_id,))
    conn.commit()


# --------------------------------------------------------------------------- #
# 파트너 상세 이력 · 경고 · 리더보드
# --------------------------------------------------------------------------- #
def warn(conn, pid, reason=None, when=None) -> None:
    conn.execute("INSERT INTO events(partner_id, type, date, reason) VALUES (?,?,?,?)",
                 (pid, "warn", iso(parse_date(when)), reason))
    conn.commit()


def partner_detail(conn, row, as_of: date) -> dict:
    subs = conn.execute(
        "SELECT * FROM submissions WHERE partner_id=? ORDER BY post_date DESC, id DESC",
        (row["id"],),
    ).fetchall()
    events = conn.execute(
        "SELECT * FROM events WHERE partner_id=? ORDER BY id DESC", (row["id"],),
    ).fetchall()
    valid_subs = [s for s in subs if s["valid"]]
    return {
        "row": row,
        "streak": streak_for(conn, row, as_of),
        "posted_today": posted_today(conn, row["id"], as_of),
        "total_valid": len(valid_subs),
        "total_void": len(subs) - len(valid_subs),
        "submissions": subs,
        "events": events,
    }


# --------------------------------------------------------------------------- #
# 자료실
# --------------------------------------------------------------------------- #
def add_library_file(conn, title, category, stored_name, orig_name, size,
                     data_b64=None) -> int:
    """파일 자료 등록. data_b64 가 있으면 DB에 내용 저장(서버리스/Vercel 영구 보존),
    없으면 stored_name(디스크 저장 파일명)으로 처리(로컬)."""
    cur = conn.execute(
        "INSERT INTO library(kind, title, category, stored_name, orig_name, size, "
        "data_b64, created_at) VALUES ('file',?,?,?,?,?,?,?)",
        (title, category, stored_name, orig_name, size, data_b64,
         now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def add_library_link(conn, title, category, url) -> int:
    cur = conn.execute(
        "INSERT INTO library(kind, title, category, url, created_at) "
        "VALUES ('link',?,?,?,?)",
        (title, category, url, now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def list_library(conn) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM library ORDER BY category, id DESC"
    ).fetchall()


def get_library(conn, lid) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM library WHERE id=?", (lid,)).fetchone()


def delete_library(conn, lid) -> sqlite3.Row | None:
    row = get_library(conn, lid)
    if row:
        conn.execute("DELETE FROM library WHERE id=?", (lid,))
        conn.commit()
    return row


def human_size(n) -> str:
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


# --------------------------------------------------------------------------- #
# 운영 공지
# --------------------------------------------------------------------------- #
def add_notice(conn, title, body=None, when=None) -> int:
    cur = conn.execute(
        "INSERT INTO notices(notice_date, title, body, created_at) VALUES (?,?,?,?)",
        (iso(parse_date(when)), title, body, now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def active_notices(conn) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM notices WHERE active=1 ORDER BY notice_date DESC, id DESC"
    ).fetchall()


def all_notices(conn) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM notices ORDER BY notice_date DESC, id DESC LIMIT 30"
    ).fetchall()


def set_notice_active(conn, nid, active: int) -> None:
    conn.execute("UPDATE notices SET active=? WHERE id=?", (active, nid))
    conn.commit()


def recent_submissions(conn, pid, limit=10) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM submissions WHERE partner_id=? ORDER BY post_date DESC, id DESC LIMIT ?",
        (pid, limit),
    ).fetchall()


def wall_summary(conn, as_of: date) -> dict:
    """공개 인증 보드 요약 — 오늘 발행/전체, 평균 연속일."""
    lb = leaderboard(conn, as_of)
    posted = sum(1 for r in lb if r["posted_today"])
    avg = round(sum(r["streak"] for r in lb) / len(lb), 1) if lb else 0
    return {"total": len(lb), "posted_today": posted, "avg_streak": avg, "board": lb}


def leaderboard(conn, as_of: date) -> list[dict]:
    rows = conn.execute("SELECT * FROM partners WHERE status='active' ORDER BY id").fetchall()
    out = []
    for r in rows:
        total = conn.execute(
            "SELECT COUNT(*) c FROM submissions WHERE partner_id=? AND valid=1", (r["id"],)
        ).fetchone()["c"]
        out.append({
            "name": r["name"], "handle": r["handle"], "id": r["id"],
            "streak": streak_for(conn, r, as_of),
            "posted_today": posted_today(conn, r["id"], as_of),
            "total": total,
        })
    out.sort(key=lambda x: (-x["streak"], -x["total"], x["name"]))
    return out


def find_partner(conn, ident: str) -> sqlite3.Row | None:
    """이름 / @핸들 / 숫자 id 로 조회."""
    if ident.isdigit():
        row = conn.execute("SELECT * FROM partners WHERE id=?", (int(ident),)).fetchone()
        if row:
            return row
    h = ident.lstrip("@")
    return conn.execute(
        "SELECT * FROM partners WHERE name=? OR handle=? OR handle=?",
        (ident, h, "@" + h),
    ).fetchone()


def all_partners(conn) -> list:
    return conn.execute(
        "SELECT * FROM partners ORDER BY status, joined_date DESC, name"
    ).fetchall()


def delete_partner(conn, pid) -> None:
    # 자식(제출·이벤트·매출)은 ON DELETE CASCADE 로 함께 삭제
    conn.execute("DELETE FROM partners WHERE id=?", (pid,))
    conn.commit()


def reset_all(conn) -> None:
    """전체 초기화 — 모든 데이터+관리자 비번 삭제(첫 로그인 때 비번 재설정)."""
    if db.is_postgres():
        conn.execute("TRUNCATE partners, submissions, events, drops, sales, "
                     "library, notices, settings RESTART IDENTITY CASCADE")
    else:
        for t in ("submissions", "events", "drops", "sales", "library",
                  "notices", "settings", "partners"):
            conn.execute(f"DELETE FROM {t}")
    conn.commit()


def set_status(conn, pid, status, when=None, reason=None) -> None:
    d = iso(parse_date(when))
    if status == "kicked":
        conn.execute("UPDATE partners SET status=?, kicked_date=? WHERE id=?", (status, d, pid))
    else:
        conn.execute("UPDATE partners SET status=?, kicked_date=NULL WHERE id=?", (status, pid))
    etype = {"kicked": "kick", "paused": "pause", "active": "reactivate"}.get(status, status)
    conn.execute(
        "INSERT INTO events(partner_id, type, date, reason) VALUES (?,?,?,?)",
        (pid, etype, d, reason),
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# 제출
# --------------------------------------------------------------------------- #
def add_submission(conn, pid, url, channel="threads", post_date=None, note=None) -> int:
    pd = iso(parse_date(post_date))
    cur = conn.execute(
        "INSERT INTO submissions(partner_id, post_url, channel, post_date, submitted_at, note) "
        "VALUES (?,?,?,?,?,?)",
        (pid, url, channel, pd, now_iso(), note),
    )
    conn.commit()
    return cur.lastrowid


def covered_dates(conn, pid) -> set[str]:
    """유효(valid=1) 제출이 있는 날짜만 출석 인정."""
    rows = conn.execute(
        "SELECT DISTINCT post_date FROM submissions WHERE partner_id=? AND valid=1", (pid,)
    ).fetchall()
    return {r["post_date"] for r in rows}


def current_streak(conn, pid, joined: date, as_of: date) -> int:
    """as_of(오늘) 기준 연속 커버 일수. 오늘 미제출이면 어제부터 카운트(아직 살아있음)."""
    cov = covered_dates(conn, pid)
    d = as_of if iso(as_of) in cov else as_of - timedelta(days=1)
    streak = 0
    while d >= joined and iso(d) in cov:
        streak += 1
        d -= timedelta(days=1)
    return streak


# --------------------------------------------------------------------------- #
# 일일 운영 판정
# --------------------------------------------------------------------------- #
@dataclass
class PartnerStatus:
    row: sqlite3.Row
    posted_today: bool
    streak: int
    missed_yesterday: bool      # 어제(이전 필수일) 빵꾸 → 강퇴 후보
    total_posts: int

    @property
    def name(self):
        return self.row["name"]

    @property
    def handle(self):
        return self.row["handle"]


def daily_board(conn, as_of: date) -> dict:
    """오늘 운영 보드: 완료 / 위험군(오늘 미제출) / 강퇴후보(어제 빵꾸)."""
    rows = conn.execute("SELECT * FROM partners WHERE status='active' ORDER BY id").fetchall()
    done, at_risk, kick = [], [], []
    for r in rows:
        joined = datetime.strptime(r["joined_date"], "%Y-%m-%d").date()
        cov = covered_dates(conn, r["id"])
        posted_today = iso(as_of) in cov
        yday = as_of - timedelta(days=1)
        # 어제가 필수일(>= 가입일)인데 커버 안 됐으면 위반
        missed_yday = yday >= joined and iso(yday) not in cov
        total = conn.execute(
            "SELECT COUNT(*) c FROM submissions WHERE partner_id=?", (r["id"],)
        ).fetchone()["c"]
        st = PartnerStatus(
            row=r,
            posted_today=posted_today,
            streak=current_streak(conn, r["id"], joined, as_of),
            missed_yesterday=missed_yday,
            total_posts=total,
        )
        if missed_yday:
            kick.append(st)
        elif posted_today:
            done.append(st)
        else:
            at_risk.append(st)
    return {"date": iso(as_of), "done": done, "at_risk": at_risk, "kick": kick,
            "active_count": len(rows)}


# --------------------------------------------------------------------------- #
# 일일 글감/소스 배포
# --------------------------------------------------------------------------- #
def add_drop(conn, title, body=None, assets=None, dtype="ai", drop_date=None) -> int:
    d = iso(parse_date(drop_date))
    asset_text = "\n".join(assets) if assets else None
    cur = conn.execute(
        "INSERT INTO drops(drop_date, dtype, title, body, assets, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (d, dtype, title, body, asset_text, now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def drops_on(conn, d: date) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM drops WHERE drop_date=? ORDER BY id", (iso(d),)
    ).fetchall()


def all_drops(conn, limit: int = 365) -> list[sqlite3.Row]:
    """전체 글감 — 최신 날짜부터(피드/아카이브용)."""
    return conn.execute(
        "SELECT * FROM drops ORDER BY drop_date DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()


def delete_drop(conn, drop_id: int) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM drops WHERE id=?", (drop_id,)).fetchone()
    conn.execute("DELETE FROM drops WHERE id=?", (drop_id,))
    conn.commit()
    return row


# --------------------------------------------------------------------------- #
# 매출 / 몰수 / 정산
# --------------------------------------------------------------------------- #
def add_sale(conn, pid, product_key, amount, sale_date=None, order_ref=None, note=None) -> int:
    sd = iso(parse_date(sale_date))
    cur = conn.execute(
        "INSERT INTO sales(partner_id, product_key, amount, sale_date, order_ref, note) "
        "VALUES (?,?,?,?,?,?)",
        (pid, product_key, int(amount), sd, order_ref, note),
    )
    conn.commit()
    return cur.lastrowid


def forfeit_month(conn, pid, ym: str, reason=None) -> None:
    """그 달(YYYY-MM) 수익 몰수 표시."""
    existing = conn.execute(
        "SELECT 1 FROM events WHERE partner_id=? AND type='forfeit' AND date=?",
        (pid, ym),
    ).fetchone()
    if existing:
        return
    conn.execute(
        "INSERT INTO events(partner_id, type, date, reason) VALUES (?,?,?,?)",
        (pid, "forfeit", ym, reason),
    )
    conn.commit()


def is_forfeited(conn, pid, ym: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM events WHERE partner_id=? AND type='forfeit' AND date=?",
        (pid, ym),
    ).fetchone() is not None


def enforce(conn, as_of: date, dry_run: bool = True) -> list[dict]:
    """규칙 집행: 어제 누락한 활성 파트너를 즉시 강퇴 + 그 달 수익 몰수.
    dry_run=True 면 대상만 반환하고 실제 변경하지 않음."""
    board = daily_board(conn, as_of)
    yday = as_of - timedelta(days=1)
    ym = yday.strftime("%Y-%m")
    out = []
    for s in board["kick"]:
        out.append({"id": s.row["id"], "name": s.name,
                    "handle": s.handle, "contact": s.row["contact"], "month": ym})
        if not dry_run:
            reason = f"{iso(yday)} 미발행(1회 누락 즉시 강퇴)"
            set_status(conn, s.row["id"], "kicked", iso(as_of), reason)
            forfeit_month(conn, s.row["id"], ym, "누락 강퇴로 당월 수익 몰수")
    return out


def settle(conn, year: int, month: int) -> list[dict]:
    """월 정산: 파트너별 매출·쉐어액. 그 달 몰수 대상이면 정산액 0."""
    from . import products as _products
    ym = f"{year}-{month:02d}"
    first = date(year, month, 1)
    nxt = date(year + (month == 12), (month % 12) + 1, 1)
    last = iso(nxt - timedelta(days=1))
    pmap = {p["key"]: p for p in _products.products()}
    rows = conn.execute("SELECT * FROM partners ORDER BY id").fetchall()
    out = []
    for r in rows:
        sales = conn.execute(
            "SELECT product_key, amount FROM sales WHERE partner_id=? "
            "AND sale_date>=? AND sale_date<=?",
            (r["id"], iso(first), last),
        ).fetchall()
        if not sales:
            continue
        gross = sum(s["amount"] for s in sales)
        share = 0
        for s in sales:
            prod = pmap.get(s["product_key"])
            rate = prod["share"] if prod else 0
            share += round(s["amount"] * rate)
        forfeited = is_forfeited(conn, r["id"], ym)
        out.append({
            "name": r["name"], "status": r["status"],
            "count": len(sales), "gross": gross,
            "share": 0 if forfeited else share,
            "forfeited": forfeited, "share_before": share,
        })
    return out


def month_report(conn, year: int, month: int, as_of: date) -> list[dict]:
    """월간 인증용 리포트: 파트너별 달성률 / 최장 연속 / 게시물 수."""
    first = date(year, month, 1)
    nxt = date(year + (month == 12), (month % 12) + 1, 1)
    last_required = min(nxt - timedelta(days=1), as_of)
    rows = conn.execute("SELECT * FROM partners ORDER BY id").fetchall()
    out = []
    for r in rows:
        joined = datetime.strptime(r["joined_date"], "%Y-%m-%d").date()
        start = max(first, joined)
        if start > last_required:
            continue
        cov = covered_dates(conn, r["id"])
        required = (last_required - start).days + 1
        covered = sum(
            1 for i in range(required) if iso(start + timedelta(days=i)) in cov
        )
        # 최장 연속
        longest = run = 0
        for i in range(required):
            if iso(start + timedelta(days=i)) in cov:
                run += 1
                longest = max(longest, run)
            else:
                run = 0
        posts = conn.execute(
            "SELECT COUNT(*) c FROM submissions WHERE partner_id=? "
            "AND post_date>=? AND post_date<=?",
            (r["id"], iso(first), iso(last_required)),
        ).fetchone()["c"]
        out.append({
            "name": r["name"], "handle": r["handle"], "status": r["status"],
            "required": required, "covered": covered,
            "rate": round(covered / required * 100) if required else 0,
            "longest_streak": longest, "posts": posts,
        })
    return out
