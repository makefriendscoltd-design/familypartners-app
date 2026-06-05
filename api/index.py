"""Vercel 서버리스 진입점 — 모든 요청이 여기로 라우팅(vercel.json rewrite)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fp import db          # noqa: E402
from fp.server import app  # noqa: E402  (WSGI 앱)

# 콜드스타트 시 테이블 보장(CREATE TABLE IF NOT EXISTS — idempotent)
try:
    db.init_db()
except Exception:
    pass
