@echo off
chcp 65001 >nul
set VBS=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AgentOffice.vbs
(
echo Set sh = CreateObject^("WScript.Shell"^)
echo sh.CurrentDirectory = "%~dp0"
echo sh.Run "cmd /c python control_room.py", 0, False
) > "%VBS%"
echo 설치 완료: 다음 로그온부터 Agent Office 서버가 자동 시작됩니다.
echo 해제: 시작프로그램 폴더에서 AgentOffice.vbs 삭제
pause
