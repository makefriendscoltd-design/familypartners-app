#!/usr/bin/env python3
"""Caddyfile 에 familypartners.aimax.ai.kr 서브도메인 블록을 멱등하게 주입.

서버에서 root 로 실행:  sudo python3 deploy/deploy_caddy_route.py
- 이미 블록이 있으면 아무것도 안 함.
- 없으면 백업 후 사이트 블록을 추가.
서브도메인이라 경로 스트립이 필요 없음(앱이 / 절대경로를 씀).
"""
import re
import shutil
import sys
import time

CADDYFILE = "/etc/caddy/Caddyfile"
DOMAIN = "familypartners.aimax.ai.kr"
PORT = "18790"

BLOCK = f"""
{DOMAIN} {{
\treverse_proxy 127.0.0.1:{PORT}
}}
"""


def main() -> int:
    try:
        with open(CADDYFILE, encoding="utf-8") as f:
            txt = f.read()
    except FileNotFoundError:
        print(f"[caddy] {CADDYFILE} 없음 — 중단", file=sys.stderr)
        return 1

    if re.search(rf"(?m)^\s*{re.escape(DOMAIN)}\s*\{{", txt):
        print(f"[caddy] {DOMAIN} 블록 이미 존재 — 변경 없음")
        return 0

    bak = f"{CADDYFILE}.bak-familypartners-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(CADDYFILE, bak)
    print(f"[caddy] 백업 -> {bak}")

    if not txt.endswith("\n"):
        txt += "\n"
    txt += BLOCK
    with open(CADDYFILE, "w", encoding="utf-8") as f:
        f.write(txt)
    print(f"[caddy] 추가함: {DOMAIN} -> 127.0.0.1:{PORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
