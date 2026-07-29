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


def yesterday(as_of: date | None = None) -> date:
    return (as_of or today()) - timedelta(days=1)


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
def add_submission(conn, pid, url, channel="threads", post_date=None, note=None,
                   drop_id=None, room_members=None) -> int:
    pd = iso(parse_date(post_date))
    cur = conn.execute(
        "INSERT INTO submissions(partner_id, post_url, channel, post_date, submitted_at, "
        "note, drop_id, room_members) VALUES (?,?,?,?,?,?,?,?)",
        (pid, url, channel, pd, now_iso(), note, drop_id, _to_int(room_members)),
    )
    conn.commit()
    return cur.lastrowid


# --------------------------------------------------------------------------- #
# 성과 추적 — 오픈톡방 인원 증감으로 글의 실효를 잰다.
#
# 파트너는 하루 1건만 올린다(출석 규칙). 그래서 "어제 대비 오늘 방 인원 증가분"이
# 곧 '그 하루 동안 살아 있던 글 1건'의 성과가 된다. 파트너에게 "이 글로 몇 명
# 들어왔나요"를 물으면 알 수 없어서 지어내지만, "지금 방 인원 몇 명인가요"는
# 카톡 화면에 그대로 떠 있는 숫자라 정확하다.
#
# 중요 — 하루 밀린다:
#   제출 시점에 넣는 인원은 방금 올린 글(0분짜리)이 아니라 '어제 올린 글'의 결과다.
#   그래서 증가분은 직전 제출(=어제 글)에 크레딧을 붙인다.
#
# 저장은 반드시 '증감분'이 아니라 '절대값(room_members)'으로 한다.
#   증감분을 받으면 파트너가 뺄셈을 해야 하고, 오입력 복구도, 빠진 날 감지도 안 된다.
# --------------------------------------------------------------------------- #

def _to_int(v) -> int | None:
    """폼 입력값 → 0 이상 정수. 빈칸/이상값은 None."""
    try:
        n = int(str(v).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def last_room_members(conn, pid) -> int | None:
    """이 파트너가 마지막으로 넣은 방 인원(절대값). 없으면 None."""
    r = conn.execute(
        "SELECT room_members FROM submissions WHERE partner_id=? AND valid=1 "
        "AND room_members IS NOT NULL ORDER BY post_date DESC, id DESC LIMIT 1", (pid,)
    ).fetchone()
    return r["room_members"] if r else None


def submission_gains(conn) -> dict[int, dict]:
    """제출별 '그 글이 만든 방 인원 증가분'을 계산.

    반환: {submission_id: {"gain": int, "span": int}}
      gain — 다음 입력 시점의 방 인원 − 이 시점의 방 인원 (음수 가능: 나간 사람이 더 많음)
      span — 두 입력 사이의 일수. 1이면 그 글 하루치로 깨끗하게 귀속된다.
             2 이상이면 여러 날이 뭉친 값이라 글감별 평균에서는 제외한다.
    마지막 제출은 다음 입력이 아직 없으므로 결과에 포함되지 않는다.
    """
    rows = conn.execute(
        "SELECT id, partner_id, post_date, room_members FROM submissions "
        "WHERE valid=1 AND room_members IS NOT NULL "
        "ORDER BY partner_id, post_date, id"
    ).fetchall()
    # 같은 날 두 번 제출했으면 그날의 마지막 값만 쓴다(하루 = 한 측정점).
    by_partner: dict[int, dict[str, sqlite3.Row]] = {}
    for r in rows:
        by_partner.setdefault(r["partner_id"], {})[r["post_date"]] = r

    gains: dict[int, dict] = {}
    for per_day in by_partner.values():
        seq = [per_day[d] for d in sorted(per_day)]
        for prev, cur in zip(seq, seq[1:]):
            span = (parse_date(cur["post_date"]) - parse_date(prev["post_date"])).days
            gains[prev["id"]] = {
                "gain": (cur["room_members"] or 0) - (prev["room_members"] or 0),
                "span": span,
            }
    return gains


def drop_performance(conn, limit: int = 40) -> list[dict]:
    """글감별 성과 — 글 1건당 평균 방 인원 순증이 높은 순. 다음 글감의 근거."""
    subs = conn.execute(
        "SELECT s.id, s.drop_id, d.id did, d.drop_date, d.title, d.dtype, d.target, d.fmt "
        "FROM drops d LEFT JOIN submissions s ON s.drop_id=d.id AND s.valid=1"
    ).fetchall()
    gains = submission_gains(conn)

    agg: dict[int, dict] = {}
    for r in subs:
        a = agg.setdefault(r["did"], {
            "id": r["did"], "drop_date": r["drop_date"], "title": r["title"],
            "dtype": r["dtype"], "target": r["target"], "fmt": r["fmt"],
            "used": 0, "measured": 0, "gain": 0, "multi": 0,
        })
        if r["id"] is None:          # 아직 아무도 안 쓴 글감
            continue
        a["used"] += 1
        g = gains.get(r["id"])
        if not g:
            continue
        if g["span"] == 1:           # 하루치로 깨끗하게 귀속되는 것만 평균에 넣는다
            a["measured"] += 1
            a["gain"] += g["gain"]
        else:
            a["multi"] += 1

    out = []
    for a in agg.values():
        m = a["measured"]
        a["avg_gain"] = round(a["gain"] / m, 2) if m else 0
        out.append(a)
    # 측정된 글감 먼저, 그 안에서 평균 순증 높은 순
    out.sort(key=lambda x: (x["measured"] == 0, -x["avg_gain"], x["drop_date"]))
    return out[:limit]


def top_posts(conn, limit: int = 20) -> list[dict]:
    """순증 상위 게시물 — 다음 글감 후보(파트너가 쓴 글이 소스가 된다)."""
    rows = conn.execute(
        "SELECT s.id, s.post_url, s.post_date, s.room_members, "
        "p.name pname, p.handle phandle, d.title dtitle "
        "FROM submissions s JOIN partners p ON p.id=s.partner_id "
        "LEFT JOIN drops d ON d.id=s.drop_id WHERE s.valid=1"
    ).fetchall()
    gains = submission_gains(conn)
    out = []
    for r in rows:
        g = gains.get(r["id"])
        if not g or g["span"] != 1:
            continue
        d = dict(r)
        d["gain"] = g["gain"]
        out.append(d)
    out.sort(key=lambda x: -x["gain"])
    return out[:limit]


def lead_board(conn) -> list[dict]:
    """파트너별 유입 랭킹 — 연속일(성실도)이 아니라 방을 실제로 얼마나 키웠는지.

    여기서는 span 이 2 이상인 구간도 합산한다(총량은 뭉쳐 있어도 맞기 때문).
    """
    gains = submission_gains(conn)
    rows = conn.execute(
        "SELECT s.id, s.partner_id, p.name, p.handle, s.room_members "
        "FROM submissions s JOIN partners p ON p.id=s.partner_id "
        "WHERE s.valid=1 AND p.status='active' ORDER BY s.partner_id, s.post_date, s.id"
    ).fetchall()
    acc: dict[int, dict] = {}
    for r in rows:
        a = acc.setdefault(r["partner_id"], {
            "id": r["partner_id"], "name": r["name"], "handle": r["handle"],
            "gain": 0, "measured": 0, "members": None,
        })
        if r["room_members"] is not None:
            a["members"] = r["room_members"]      # 정렬상 마지막 = 최신 방 인원
        g = gains.get(r["id"])
        if g:
            a["gain"] += g["gain"]
            a["measured"] += 1
    out = list(acc.values())
    out.sort(key=lambda x: (-x["gain"], -(x["members"] or 0), x["name"]))
    return out


def perf_summary(conn, top_n: int = 6) -> dict:
    """글감 생성기가 읽을 성과 요약 — 개인정보 없이 '무엇이 먹혔나'만.

    파트너 이름·방 인원 같은 개인 단위 값은 넣지 않는다. 타겟/형태 축의 평균과
    글감 제목(이미 공개된 값)만 담아, 다음 글감을 뭘로 쓸지 판단할 근거로 쓴다.
    """
    def axis(field):
        return [{"tag": t["tag"], "avg_gain": t["avg_gain"], "measured": t["measured"]}
                for t in tag_performance(conn, field) if t["measured"]]

    drops = [d for d in drop_performance(conn, 200) if d["measured"]]
    drops.sort(key=lambda d: -d["avg_gain"])
    return {
        "by_target": axis("target"),
        "by_fmt": axis("fmt"),
        "top_drops": [{"title": d["title"], "target": d["target"], "fmt": d["fmt"],
                       "avg_gain": d["avg_gain"], "measured": d["measured"]}
                      for d in drops[:top_n]],
        "worst_drops": [{"title": d["title"], "target": d["target"], "fmt": d["fmt"],
                         "avg_gain": d["avg_gain"], "measured": d["measured"]}
                        for d in drops[-3:]] if len(drops) > top_n else [],
    }


def room_stats(conn) -> dict:
    """전체 요약 — 총 순증 / 글 1건당 평균 순증 / 인원 입력률."""
    tot = conn.execute(
        "SELECT COUNT(*) n, SUM(CASE WHEN room_members IS NOT NULL THEN 1 ELSE 0 END) filled "
        "FROM submissions WHERE valid=1").fetchone()
    gains = submission_gains(conn)
    clean = [g["gain"] for g in gains.values() if g["span"] == 1]
    n, filled = tot["n"] or 0, tot["filled"] or 0
    return {
        "submissions": n,
        "filled": filled,
        "fill_rate": round(filled * 100 / n) if n else 0,
        "total_gain": sum(g["gain"] for g in gains.values()),
        "measured": len(clean),
        "avg_gain": round(sum(clean) / len(clean), 2) if clean else 0,
    }


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
# 글감 태그 — 성과를 글감 하나가 아니라 '축'으로 누적해서 보기 위한 것.
# 파트너가 10명뿐이라 글감 하나당 표본이 2~3명밖에 안 된다. 그 숫자로는 아무것도
# 판단할 수 없다. 같은 타겟/포맷을 2주 모으면 표본이 10~15가 되어 비교가 가능해진다.
DROP_TARGETS = ["전체", "자영업자", "직장인", "부모", "입문자", "N잡러", "시니어"]
DROP_FORMATS = ["리스트형", "실패담", "자가진단", "한줄질문", "숫자대비",
                "역발상", "대화인용", "남의사례", "과정공개", "툴비교"]


def add_drop(conn, title, body=None, assets=None, dtype="ai", drop_date=None,
             target=None, fmt=None) -> int:
    d = iso(parse_date(drop_date))
    asset_text = "\n".join(assets) if assets else None
    cur = conn.execute(
        "INSERT INTO drops(drop_date, dtype, title, body, assets, target, fmt, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (d, dtype, title, body, asset_text,
         (target or "").strip() or None, (fmt or "").strip() or None, now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def latest_drop_day(conn, as_of: date) -> list[sqlite3.Row]:
    """'오늘 올릴 글감' 묶음 — 공개된 글감 중 가장 최근 날짜의 것 전부.

    파트너는 이 중에서 자기 계정 색깔에 맞는 하나를 골라 쓴다.
    오늘자가 아직 없으면(운영자가 늦게 올리면) 직전 날짜 묶음을 그대로 보여준다.
    """
    rows = published_drops(conn, as_of, 60)
    if not rows:
        return []
    newest = rows[0]["drop_date"]
    same_day = [r for r in rows if r["drop_date"] == newest]
    # published_drops 는 최신순이라 51, 51-1, 51-2 가 뒤집혀 나온다.
    # 파트너에게는 운영자가 올린 순서 그대로 보여준다.
    return sorted(same_day, key=lambda r: r["id"])


def tag_performance(conn, field: str) -> list[dict]:
    """타겟/포맷 축으로 성과를 누적. field 는 'target' 또는 'fmt'."""
    if field not in ("target", "fmt"):
        raise ValueError("field 는 target 또는 fmt")
    rows = conn.execute(
        f"SELECT s.id, d.{field} tag FROM submissions s "
        f"JOIN drops d ON d.id=s.drop_id WHERE s.valid=1"
    ).fetchall()
    gains = submission_gains(conn)
    acc: dict[str, dict] = {}
    for r in rows:
        tag = r["tag"] or "(태그 없음)"
        a = acc.setdefault(tag, {"tag": tag, "used": 0, "measured": 0, "gain": 0})
        a["used"] += 1
        g = gains.get(r["id"])
        if g and g["span"] == 1:
            a["measured"] += 1
            a["gain"] += g["gain"]
    out = []
    for a in acc.values():
        a["avg_gain"] = round(a["gain"] / a["measured"], 2) if a["measured"] else 0
        out.append(a)
    out.sort(key=lambda x: (x["measured"] == 0, -x["avg_gain"]))
    return out


def get_drop(conn, drop_id) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM drops WHERE id=?", (drop_id,)).fetchone()


def update_drop(conn, drop_id, title=None, body=None, dtype=None,
                target=None, fmt=None, drop_date=None) -> bool:
    """글감 수정 — 넘어온 값만 바꾼다. 예약 글감을 파트너 노출 전에 손보기 위한 것."""
    row = get_drop(conn, drop_id)
    if not row:
        return False
    sets, vals = [], []
    for col, val in (("title", title), ("body", body), ("dtype", dtype),
                     ("target", target), ("fmt", fmt), ("drop_date", drop_date)):
        if val is None:
            continue
        v = val.strip()
        if col in ("title", "drop_date") and not v:
            continue                      # 제목·날짜는 빈 값으로 못 지운다
        if col == "drop_date":
            v = iso(parse_date(v))
        sets.append(f"{col}=?")
        vals.append(v or None)
    if not sets:
        return False
    vals.append(drop_id)
    conn.execute(f"UPDATE drops SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit()
    return True


def drops_on(conn, d: date) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM drops WHERE drop_date=? ORDER BY id", (iso(d),)
    ).fetchall()


def all_drops(conn, limit: int = 365) -> list[sqlite3.Row]:
    """전체 글감 — 최신 날짜부터(관리자/아카이브용, 예약분 포함)."""
    return conn.execute(
        "SELECT * FROM drops ORDER BY drop_date DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()


def published_drops(conn, as_of: date, limit: int = 365) -> list[sqlite3.Row]:
    """공개된 글감만 — drop_date <= 오늘(최신순). 미래(예약) 글감 제외.

    drop_date 는 'YYYY-MM-DD' 텍스트라 사전식 비교 = 날짜 비교(SQLite/PG 공통).
    """
    return conn.execute(
        "SELECT * FROM drops WHERE drop_date <= ? ORDER BY drop_date DESC, id DESC LIMIT ?",
        (iso(as_of), limit),
    ).fetchall()


def scheduled_drops(conn, as_of: date, limit: int = 365) -> list[sqlite3.Row]:
    """예약 글감 — drop_date > 오늘(가까운 날짜부터). 관리자 큐용."""
    return conn.execute(
        "SELECT * FROM drops WHERE drop_date > ? ORDER BY drop_date ASC, id ASC LIMIT ?",
        (iso(as_of), limit),
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
