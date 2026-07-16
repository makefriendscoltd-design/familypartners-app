#!/usr/bin/env bash
# Family Partners → Oracle 서버 배포 (familypartners.aimax.ai.kr)
#
# 로컬(민수 맥)에서 repo 루트 기준으로 실행:  bash deploy/deploy_oracle.sh
#
# 하는 일: 앱 패키징 → scp → 서버에서 언타르(코드만) → systemd 서비스/타이머 설치
#          → Caddy 서브도메인 라우트 주입 → reload → 헬스체크.
# data/(SQLite DB) 와 .env 는 덮어쓰지 않고 보존한다.
#
# 사전조건(runbook 참고):
#   1) DNS: familypartners.aimax.ai.kr -> api.aimax.ai.kr 와 동일 IP (전파 완료)
#   2) 서버 /home/ubuntu/familypartners/.env 준비 (deploy/.env.example 참고, CRON_SECRET/PPURIO)
#   3) 데이터 이전: 서버 data/challenge.db (migrate_neon_to_sqlite.py 결과) 배치
set -euo pipefail

REMOTE_HOST="${FP_REMOTE_HOST:-100.69.85.89}"
REMOTE_PORT="${FP_REMOTE_PORT:-3333}"
REMOTE_USER="${FP_REMOTE_USER:-ubuntu}"
REMOTE_DIR="/home/${REMOTE_USER}/familypartners"
SSH="ssh -p ${REMOTE_PORT} ${REMOTE_USER}@${REMOTE_HOST}"
TS="$(date +%Y%m%d-%H%M%S)"

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

echo "==> 패키징 (코드만, data/ .env .git 제외)"
TAR="/tmp/familypartners-${TS}.tgz"
tar -czf "$TAR" \
  --exclude='.git' --exclude='data' --exclude='.env' \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='.venv' \
  fp api config assets requirements.txt deploy

echo "==> 로컬 문법 점검"
python3 -m py_compile fp/server.py fp/db.py deploy/deploy_caddy_route.py deploy/migrate_neon_to_sqlite.py

echo "==> 전송: $TAR -> ${REMOTE_HOST}:/tmp/"
scp -P "${REMOTE_PORT}" "$TAR" "${REMOTE_USER}@${REMOTE_HOST}:/tmp/familypartners-deploy.tgz"

echo "==> 원격 배포"
$SSH REMOTE_DIR="$REMOTE_DIR" TS="$TS" bash -s <<'REMOTE'
set -euo pipefail
mkdir -p "$REMOTE_DIR" "$REMOTE_DIR/data" "/home/ubuntu/familypartners-backups"

# 기존 코드 백업(있으면)
if [ -d "$REMOTE_DIR/fp" ]; then
  tar -czf "/home/ubuntu/familypartners-backups/code-${TS}.tgz" -C "$REMOTE_DIR" fp api config assets 2>/dev/null || true
fi

# 코드만 덮어쓰기(data/, .env 보존)
tar -xzf /tmp/familypartners-deploy.tgz -C "$REMOTE_DIR"
rm -f /tmp/familypartners-deploy.tgz

# .env 없으면 예제 복사 후 중단 안내
if [ ! -f "$REMOTE_DIR/.env" ]; then
  cp "$REMOTE_DIR/deploy/.env.example" "$REMOTE_DIR/.env"
  chmod 600 "$REMOTE_DIR/.env"
  echo "!! .env 를 새로 만들었습니다 — CRON_SECRET/PPURIO 값을 채운 뒤 다시 실행하세요: $REMOTE_DIR/.env"
  exit 3
fi

# DB 초기화/마이그레이션 (SQLite; 이미 있으면 스키마 보정만)
cd "$REMOTE_DIR"
FP_DB="$REMOTE_DIR/data/challenge.db" python3 -m fp init

# systemd 유닛 설치
sudo cp "$REMOTE_DIR/deploy/familypartners.service"      /etc/systemd/system/familypartners.service
sudo cp "$REMOTE_DIR/deploy/familypartners-sms.service"  /etc/systemd/system/familypartners-sms.service
sudo cp "$REMOTE_DIR/deploy/familypartners-sms.timer"    /etc/systemd/system/familypartners-sms.timer
sudo systemctl daemon-reload
sudo systemctl enable --now familypartners.service
sudo systemctl restart familypartners.service
sudo systemctl enable --now familypartners-sms.timer

# Caddy 서브도메인 라우트 주입 + reload
sudo python3 "$REMOTE_DIR/deploy/deploy_caddy_route.py"
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy

# 헬스체크(로컬 루프백)
sleep 1
code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18790/feed || echo 000)"
echo "== local /feed HTTP $code =="
sudo systemctl --no-pager status familypartners.service | sed -n '1,6p'
REMOTE

echo ""
echo "============================================================"
echo "  배포 완료. 외부 확인:"
echo "    curl -I https://familypartners.aimax.ai.kr/feed"
echo "  (DNS/TLS 전파 후 200. 최초 TLS 발급에 수십 초 걸릴 수 있음)"
echo "============================================================"
rm -f "$TAR"
