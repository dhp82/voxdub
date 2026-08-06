@echo off
REM =====================================================================
REM  start_server.cmd — chay control server tren VPS
REM
REM  CACH DUNG:
REM    1. Doi ten file nay thanh  start_server.cmd
REM    2. Thay DAN_TOKEN_MOI_VAO_DAY bang token moi tu sinh, vi du chay
REM       PowerShell:
REM         -join ((48..57)+(97..122) | Get-Random -Count 48 | % {[char]$_})
REM    3. KHONG commit file da co token that (start_server.cmd da gitignore)
REM =====================================================================
set "ADMIN_TOKEN=DAN_TOKEN_MOI_VAO_DAY"
set "PORT=3001"
set "HOST=127.0.0.1"
set "TRUST_PROXY=1"
cd /d C:\voxdub\control_server
node server.js
