@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo  인터넷 공개 터널을 켭니다.
echo  - 먼저 start.bat 으로 서버가 켜져 있어야 합니다.
echo  - 아래에 나오는 https://....trycloudflare.com 주소가 공개 주소입니다.
echo  - 이 창을 닫으면 공개가 종료됩니다 (서버는 계속 돕니다).
echo ============================================================
echo.
cloudflared tunnel --protocol http2 --url http://localhost:8000
