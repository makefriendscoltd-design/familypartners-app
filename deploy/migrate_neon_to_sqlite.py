#!/usr/bin/env python3
"""Neon(Postgres) → 로컬 SQLite 1회 데이터 이전.

읽기 전용으로 Neon 을 열어 전 테이블을 그대로 SQLite 로 복사한다(id 보존 → FK 유지).
Neon 원본은 건드리지 않는다.

사용:
  NEON_URL="postgres://user:pass@host/db?sslmode=require" \
  python3 deploy/migrate_neon_to_sqlite.py --out data/challenge.db

  # 원본 URL 은 NEON_URL(권장) 또는 DATABASE_URL 로 전달.
  # 이미 파일이 있으면 --force 없이는 거부.
검증: 테이블별 원본/대상 행수를 출력한다(불일치 시 비정상 종료).
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import ssl
import sys
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from fp import db  # noqa: E402  (SCHEMA 재사용)

# 부모(partners) 먼저 → FK 자식 순서
TABLES = ["partners", "submissions", "events", "drops",
          "settings", "library", "notices", "sales"]


def pg_connect(url: str):
    import pg8000.dbapi
    u = urllib.parse.urlparse(url)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return pg8000.dbapi.connect(
        user=urllib.parse.unquote(u.username or ""),
        password=urllib.parse.unquote(u.password or ""),
        host=u.hostname or "localhost",
        port=u.port or 5432,
        database=(u.path or "/").lstrip("/").split("?")[0] or "postgres",
        ssl_context=ctx,
    )


def fetch_all(pg, table):
    cur = pg.cursor()
    cur.execute(f"SELECT * FROM {table}")
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return cols, rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "data" / "challenge.db"))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    src_url = os.environ.get("NEON_URL") or os.environ.get("DATABASE_URL")
    if not src_url:
        print("ERROR: NEON_URL(또는 DATABASE_URL) 환경변수가 필요합니다.", file=sys.stderr)
        return 2

    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"ERROR: {out} 이미 존재. --force 로 덮어쓰기.", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    # 대상 SQLite: 앱 스키마 그대로 생성
    dest = sqlite3.connect(str(out))
    dest.executescript(db.SCHEMA)
    dest.execute("PRAGMA foreign_keys = OFF")

    pg = pg_connect(src_url)
    ok = True
    for t in TABLES:
        try:
            cols, rows = fetch_all(pg, t)
        except Exception as e:  # 원본에 테이블이 없을 수도(구버전) — 건너뜀
            print(f"[skip] {t}: {e}")
            continue
        if rows:
            placeholders = ",".join(["?"] * len(cols))
            collist = ",".join(f'"{c}"' for c in cols)
            dest.executemany(
                f'INSERT INTO {t} ({collist}) VALUES ({placeholders})',
                [tuple(r[c] for c in cols) for r in rows],
            )
        dest.commit()
        dcount = dest.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        flag = "OK" if dcount == len(rows) else "MISMATCH"
        if dcount != len(rows):
            ok = False
        print(f"[{flag}] {t}: src={len(rows)} dest={dcount}")

    dest.execute("PRAGMA foreign_keys = ON")
    dest.commit()
    dest.close()
    pg.close()
    print(f"\n완료 -> {out}")
    if not ok:
        print("경고: 일부 테이블 행수 불일치 — 확인 필요.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
