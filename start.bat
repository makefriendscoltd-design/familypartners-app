@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 패밀리 파트너스 운영 시스템을 시작합니다...
python -m fp init
start "패밀리파트너스 서버 (닫으면 종료)" cmd /k "python -m fp serve --port 8000"
timeout /t 2 >nul
start "" http://localhost:8000
echo.
echo 브라우저에 운영 화면이 열립니다. 끝낼 때는 검은 '서버' 창을 닫으세요.
timeout /t 3 >nul
