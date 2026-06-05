#!/usr/bin/env bash
# 패밀리 파트너스 — Oracle/우분투 VM 설치 스크립트
# VM 안에서 프로젝트 폴더로 들어간 뒤 실행:  bash deploy/setup.sh
# 24시간 자동 구동(systemd) + 외부 공개(0.0.0.0:8080) + 방화벽 개방.
set -e

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(whoami)"
PORT="${PORT:-8080}"
PY="$(command -v python3 || true)"

echo "==> 앱 폴더: $APP_DIR"
echo "==> 실행 계정: $USER_NAME / 포트: $PORT"

if [ -z "$PY" ]; then
  echo "==> python3 설치"
  sudo apt-get update -y && sudo apt-get install -y python3
  PY="$(command -v python3)"
fi
echo "==> python3: $PY ($($PY --version))"

echo "==> DB 초기화(빈 장부)"
cd "$APP_DIR" && "$PY" -m fp init

echo "==> systemd 서비스 등록 (familypartners)"
sudo tee /etc/systemd/system/familypartners.service >/dev/null <<UNIT
[Unit]
Description=Family Partners
After=network.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$APP_DIR
Environment=HOST=0.0.0.0
Environment=PORT=$PORT
ExecStart=$PY -m fp serve
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable familypartners >/dev/null 2>&1 || true
sudo systemctl restart familypartners

echo "==> OS 방화벽 개방(포트 $PORT)"
if command -v ufw >/dev/null 2>&1 && sudo ufw status | grep -q active; then
  sudo ufw allow "$PORT"/tcp || true
fi
# Oracle 우분투 기본 iptables 개방(중복이면 무시)
sudo iptables -C INPUT -p tcp --dport "$PORT" -j ACCEPT 2>/dev/null || \
  sudo iptables -I INPUT 6 -p tcp --dport "$PORT" -j ACCEPT || true
( command -v netfilter-persistent >/dev/null 2>&1 && sudo netfilter-persistent save ) 2>/dev/null || true

IP="$(curl -s ifconfig.me 2>/dev/null || echo '<VM-공개IP>')"
echo ""
echo "============================================================"
echo "  설치 완료. 상태 확인:  sudo systemctl status familypartners"
echo "  접속 주소:  http://$IP:$PORT"
echo ""
echo "  ⚠️ 오라클 콘솔에서 'Security List' 인그레스 규칙으로"
echo "     TCP $PORT 포트를 0.0.0.0/0 에 열어야 외부 접속됩니다."
echo "     (가이드: deploy/ORACLE.md 6단계)"
echo "============================================================"
