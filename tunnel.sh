#!/usr/bin/env bash
# 인터넷 공개 터널 — 맥/리눅스 버전 (tunnel.bat 의 맥 버전)
# 먼저 ./start.sh 로 서버가 켜져 있어야 합니다.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${1:-8000}"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared 가 설치돼 있지 않습니다. 아래 한 줄 실행 후 다시 시도하세요:"
  echo "    brew install cloudflared"
  exit 1
fi

if ! lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "$PORT 포트에 서버가 안 떠 있습니다. 먼저 ./start.sh 를 실행하세요."
  exit 1
fi

cat <<'MSG'
============================================================
 인터넷 공개 터널을 켭니다.
 - 아래에 나오는 https://....trycloudflare.com 주소가 공개 주소입니다.
 - 이 창을 닫으면 공개가 종료됩니다 (서버는 계속 돕니다).
============================================================
MSG

exec cloudflared tunnel --protocol http2 --url "http://localhost:$PORT"
