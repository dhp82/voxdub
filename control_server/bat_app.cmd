@echo off
rem BAT lai app AutoDub.
cd /d "%~dp0"
call config.cmd
curl -s -X POST %SERVER%/admin/set -H "X-Admin-Token: %TOKEN%" -H "Content-Type: application/json" -d "{\"enabled\": true, \"message\": \"\"}"
echo.
pause
