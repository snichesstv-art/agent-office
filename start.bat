@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Agent Office
echo ================================================
echo   Agent Office - Claude Code 에이전트 통제실
echo ================================================

REM 기존 서버 있으면 자동 종료 (포트 8787)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8787" ^| findstr "LISTENING"') do taskkill /f /pid %%a >nul 2>&1

REM 새 서버 시작 (명령 즉시실행까지 켜려면 끝에 --allow-launch 추가)
start "AgentOfficeServer" /min cmd /c "python control_room.py"

timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:8787
echo 완료! (첫 화면은 세션 집계 중이라 10~30초 걸릴 수 있음)
timeout /t 5 >nul
