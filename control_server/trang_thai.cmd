@echo off
rem Xem trang thai hien tai + thong ke server.
cd /d "%~dp0"
call config.cmd
echo --- /status (public) ---
curl -s %SERVER%/status
echo.
echo --- /admin/status (thong ke) ---
curl -s %SERVER%/admin/status -H "X-Admin-Token: %TOKEN%"
echo.
pause
