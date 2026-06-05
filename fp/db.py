"""SQLite 저장소 - 파트너 / 제출 / 운영 이벤트.

표준 라이브러리(sqlite3)만 사용. 설치 불필요.
DB 경로는 환경변수 FP_DB 로 바꿀 수 있고, 기본값은 data/challenge.db.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "challenge.db"


def database_url() -> str:
    # Vercel/배포 시 Postgres 연결 문자열. 없으면 로컬 SQLite 사용.
    # 서버리스 + pg8000 안정성을 위해 non-pooling(직접 연결) 우선.
    for key in ("DATABASE_URL_UNPOOLED", "POSTGRES_URL_NON_POOLING",
                "DATABASE_URL", "POSTGRES_URL"):
        v = os.environ.get(key)
        if v:
            return v
    return ""


def is_postgres() -> bool:
    return bool(database_url())


def db_path() -> Path:
    return Path(os.environ.get("FP_DB", str(DEFAULT_DB)))


# --------------------------------------------------------------------------- #
# Postgres 어댑터 — sqlite3 와 동일한 사용법(conn.execute(...).fetchone(), row["col"],
# cur.lastrowid, conn.commit())을 제공해서 core/server 코드를 그대로 쓰게 한다.
# --------------------------------------------------------------------------- #
def _pg_schema() -> str:
    return SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")


def _translate(sql: str) -> str:
    return sql.replace("?", "%s")


class _PgCursor:
    def __init__(self, raw, lastid=None):
        self._c = raw
        self._cols = [d[0] for d in raw.description] if raw.description else []
        self._lastid = lastid

    def _row(self, t):
        return dict(zip(self._cols, t)) if t is not None else None

    def fetchone(self):
        return self._row(self._c.fetchone())

    def fetchall(self):
        return [self._row(t) for t in self._c.fetchall()]

    def __iter__(self):
        for t in self._c.fetchall():
            yield self._row(t)

    @property
    def lastrowid(self):
        return self._lastid


class _PgConn:
    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=()):
        sql2 = _translate(sql)
        upper = sql2.upper()
        cur = self._raw.cursor()
        # 자동증가 id는 INSERT ... RETURNING id 로 즉시 회수(풀러/세션 안전)
        if (upper.lstrip().startswith("INSERT")
                and "RETURNING" not in upper and "ON CONFLICT" not in upper):
            cur.execute(sql2 + " RETURNING id", tuple(params))
            r = cur.fetchone()
            return _PgCursor(cur, r[0] if r else None)
        cur.execute(sql2, tuple(params))
        return _PgCursor(cur)

    def executescript(self, sql):
        for stmt in sql.split(";"):
            if stmt.strip():
                self._raw.cursor().execute(stmt)
        self._raw.commit()

    def commit(self):
        self._raw.commit()

    def close(self):
        try:
            self._raw.close()
        except Exception:
            pass


def _pg_connect():
    import ssl
    import urllib.parse

    import pg8000.dbapi

    u = urllib.parse.urlparse(database_url())
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    raw = pg8000.dbapi.connect(
        user=urllib.parse.unquote(u.username or ""),
        password=urllib.parse.unquote(u.password or ""),
        host=u.hostname or "localhost",
        port=u.port or 5432,
        database=(u.path or "/").lstrip("/").split("?")[0] or "postgres",
        ssl_context=ctx,
    )
    return _PgConn(raw)


def connect():
    if is_postgres():
        return _pg_connect()
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS partners (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    handle       TEXT,                       -- 스레드 @핸들
    contact      TEXT,                       -- 카톡/전화 등 연락 채널
    referral_code TEXT UNIQUE,               -- 개인별 수익 추적용(쿠폰코드/UTM값 등)
    status       TEXT NOT NULL DEFAULT 'active',  -- active / paused / kicked
    joined_date  TEXT NOT NULL,              -- YYYY-MM-DD (챌린지 시작일)
    kicked_date  TEXT,
    portal_token TEXT,                       -- 파트너 포털 비밀 링크 토큰
    sales_url    TEXT,                        -- 운영진이 개별 발급한 판매 페이지 링크
    openchat_url TEXT                         -- 본인 오픈톡방 링크(세팅 시 파트너가 입력)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_partner_token ON partners(portal_token);

CREATE TABLE IF NOT EXISTS submissions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_id   INTEGER NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
    post_url     TEXT NOT NULL,
    channel      TEXT NOT NULL DEFAULT 'threads',
    post_date    TEXT NOT NULL,              -- YYYY-MM-DD (이 제출이 인정되는 날짜)
    submitted_at TEXT NOT NULL,              -- ISO 타임스탬프(접수 시각)
    note         TEXT,
    valid        INTEGER NOT NULL DEFAULT 1, -- 1=유효(출석인정) / 0=무효(검수 탈락)
    void_reason  TEXT                        -- 무효 처리 사유
);
CREATE INDEX IF NOT EXISTS idx_sub_partner_date ON submissions(partner_id, post_date);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_id INTEGER REFERENCES partners(id) ON DELETE CASCADE,
    type       TEXT NOT NULL,               -- kick / pause / reactivate / join / forfeit
    date       TEXT NOT NULL,               -- YYYY-MM-DD (forfeit 은 YYYY-MM)
    reason     TEXT
);

-- 일일 글감/소스 배포 (운영자가 매일 등록 → 파트너에게 방송)
CREATE TABLE IF NOT EXISTS drops (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    drop_date  TEXT NOT NULL,               -- YYYY-MM-DD
    dtype      TEXT NOT NULL DEFAULT 'ai',  -- ai / marketing / evergreen
    title      TEXT NOT NULL,
    body       TEXT,                        -- 글감 본문/캡션
    assets     TEXT,                        -- 다운로드 경로/URL, 줄바꿈 구분
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_drops_date ON drops(drop_date);

-- 설정 (관리자 비밀번호 해시 등 key-value)
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- 자료실 (글감용 사진·영상·파일 저장소 — 파트너가 다운로드)
CREATE TABLE IF NOT EXISTS library (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL DEFAULT 'file',  -- file(업로드) / link(Drive 등 외부)
    title       TEXT NOT NULL,
    category    TEXT,                          -- 분류(선택)
    stored_name TEXT,                          -- file: 디스크 저장 파일명
    orig_name   TEXT,                          -- file: 원본 파일명(다운로드명)
    url         TEXT,                          -- link: 외부 URL
    size        INTEGER,
    created_at  TEXT NOT NULL
);

-- 운영 공지 (글감과 별도 — 일정 변경·안내 등 운영 메시지)
CREATE TABLE IF NOT EXISTS notices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_date TEXT NOT NULL,              -- YYYY-MM-DD
    title       TEXT NOT NULL,
    body        TEXT,
    active      INTEGER NOT NULL DEFAULT 1, -- 1=게시중 / 0=내림
    created_at  TEXT NOT NULL
);

-- 개인별 매출 (수익 어트리뷰션 → 월 정산)
CREATE TABLE IF NOT EXISTS sales (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_id  INTEGER NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
    product_key TEXT NOT NULL,
    amount      INTEGER NOT NULL,           -- 결제 금액(원)
    sale_date   TEXT NOT NULL,              -- YYYY-MM-DD
    order_ref   TEXT,                       -- 주문번호 등
    note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_sales_partner ON sales(partner_id, sale_date);
"""


def init_db() -> None:
    conn = connect()
    try:
        if is_postgres():
            conn.executescript(_pg_schema())   # 새 Postgres: 스키마에 모든 컬럼 포함
            # 기존 테이블 컬럼 추가(있으면 무시)
            conn.execute("ALTER TABLE partners ADD COLUMN IF NOT EXISTS sales_url TEXT")
            conn.execute("ALTER TABLE partners ADD COLUMN IF NOT EXISTS openchat_url TEXT")
            conn.commit()
        else:
            conn.executescript(SCHEMA)
            # 기존 SQLite DB 마이그레이션: 누락 컬럼 추가
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(partners)")}
            if "portal_token" not in cols:
                conn.execute("ALTER TABLE partners ADD COLUMN portal_token TEXT")
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_partner_token "
                             "ON partners(portal_token)")
            if "sales_url" not in cols:
                conn.execute("ALTER TABLE partners ADD COLUMN sales_url TEXT")
            if "openchat_url" not in cols:
                conn.execute("ALTER TABLE partners ADD COLUMN openchat_url TEXT")
            scols = {r["name"] for r in conn.execute("PRAGMA table_info(submissions)")}
            if "valid" not in scols:
                conn.execute("ALTER TABLE submissions ADD COLUMN valid INTEGER NOT NULL DEFAULT 1")
                conn.execute("ALTER TABLE submissions ADD COLUMN void_reason TEXT")
            conn.commit()
    finally:
        conn.close()
