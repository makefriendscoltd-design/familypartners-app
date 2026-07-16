#!/usr/bin/env bash
# GitHub main → Oracle 자동 배포 (서버 폴링, root systemd timer 로 2분마다 실행)
#
# 동작: origin/main 에 새 커밋 있으면 → 코드 갱신 → 문법검사 → DB 스키마 보정
#       → 서비스 재시작 → 헬스체크. 실패 시 이전 커밋으로 자동 롤백.
# data/ 와 .env 는 git 미추적(.gitignore)이라 절대 건드리지 않음.
set -uo pipefail

APP=/home/ubuntu/familypartners
HEALTH_URL=http://127.0.0.1:18790/feed
LOCK=/tmp/familypartners-autodeploy.lock

exec 9>"$LOCK"
flock -n 9 || exit 0   # 이전 실행이 아직 돌고 있으면 조용히 종료

cd "$APP"
git fetch -q origin main || { echo "[autodeploy] fetch 실패 (네트워크?)"; exit 1; }
local_rev="$(git rev-parse HEAD)"
remote_rev="$(git rev-parse origin/main)"
[ "$local_rev" = "$remote_rev" ] && exit 0   # 변경 없음

echo "[autodeploy] ${local_rev:0:7} -> ${remote_rev:0:7} 배포 시작"
git reset --hard -q origin/main

health() { curl -s -o /dev/null -m 10 -w '%{http_code}' "$HEALTH_URL" || echo 000; }

rollback() {
    echo "[autodeploy] 롤백 -> ${local_rev:0:7}"
    git reset --hard -q "$local_rev"
    sudo systemctl restart familypartners.service
    sleep 2
    echo "[autodeploy] 롤백 후 헬스: $(health)"
}

if ! python3 -m py_compile fp/*.py api/*.py; then
    echo "[autodeploy] 문법검사 실패 — 배포 중단·롤백"
    rollback
    exit 1
fi

FP_DB="$APP/data/challenge.db" python3 -m fp init >/dev/null 2>&1

sudo systemctl restart familypartners.service
sleep 2
code="$(health)"
if [ "$code" != "200" ]; then
    echo "[autodeploy] 헬스체크 실패 (HTTP $code)"
    rollback
    exit 1
fi

echo "[autodeploy] 완료 $(git rev-parse --short HEAD) health=$code"
