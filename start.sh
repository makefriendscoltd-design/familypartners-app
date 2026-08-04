#!/usr/bin/env bash
# 패밀리 파트너스 운영 시스템 — 맥/리눅스 실행기 (start.bat 의 맥 버전)
# 사용법:  ./start.sh          → 8000 포트로 실행
#          ./start.sh 8080     → 포트 지정
set -euo pipefail
cd "$(dirname "$0")"

PORT="${1:-8000}"

# 가상환경 우선, 없으면 시스템 python3
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="$(command -v python3 || true)"
  [ -n "$PY" ] || { echo "python3 를 찾을 수 없습니다. brew install python 후 다시 실행하세요."; exit 1; }
  echo "[알림] .venv 가 없어 시스템 python3 를 씁니다. 권장:  uv venv && uv pip install -r requirements.txt"
fi

echo "패밀리 파트너스 운영 시스템을 시작합니다... (python: $PY)"
"$PY" -m fp init

# 이미 떠 있는 서버가 있으면 알려주고 중단
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "이미 $PORT 포트를 쓰는 프로세스가 있습니다. 아래 중 하나를 하세요:"
  echo "  - 브라우저에서 http://localhost:$PORT 열기"
  echo "  - 기존 서버 종료:  lsof -ti tcp:$PORT | xargs kill"
  exit 1
fi

# 서버가 뜨면 브라우저 자동 오픈
( for _ in $(seq 1 40); do
    if curl -s -o /dev/null "http://localhost:$PORT/" 2>/dev/null; then
      open "http://localhost:$PORT"; break
    fi
    sleep 0.5
  done ) &

echo
echo "브라우저에 운영 화면이 열립니다. 끝낼 때는 이 창에서 Ctrl+C 를 누르세요."
echo
exec "$PY" -m fp serve --port "$PORT"
