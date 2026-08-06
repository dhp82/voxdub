@echo off
rem TAT app AutoDub tu xa. Sua message trong payload_off.json (UTF-8) neu muon co dau.
cd /d "%~dp0"
call config.cmd
curl -s -X POST %SERVER%/admin/set -H "X-Admin-Token: %TOKEN%" -H "Content-Type: application/json" --data-binary @payload_off.json
echo.
pause
