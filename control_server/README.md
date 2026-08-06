# AutoDub Control Server

Server nhỏ (Node.js, **zero dependency**) để **bật/tắt app AutoDub từ xa**.
App gọi `GET /status` lúc khởi động **và mỗi 30 phút khi đang chạy**; nếu
server trả `enabled: false` thì app hiện thông báo và thoát. Mất mạng /
server sập → app **vẫn chạy bình thường** (fail-open).

Thư mục này độc lập hoàn toàn — muốn gỡ tính năng thì xóa thư mục này và
`autodub_gui/remote_gate.py`.

## Kiến trúc hiện tại

```
App (exe) ──HTTPS──> nginx (voxdub-api.duckdns.org:443, TLS/win-acme)
                        └──proxy──> node server.js (127.0.0.1:3001)
```

- Node chỉ nghe **127.0.0.1:3001** — không bao giờ lộ trực tiếp ra Internet.
- nginx lo TLS (Let's Encrypt qua win-acme) và chuyển tiếp về Node.
- **KHÔNG có token mặc định** — thiếu biến môi trường `ADMIN_TOKEN` thì mọi
  request `/admin/*` trả 503. Token sinh bằng: `openssl rand -hex 24`
  (hoặc PowerShell: `-join ((48..57)+(97..122) | Get-Random -Count 48 | % {[char]$_})`).

## URL phía app (quan trọng)

URL kill-switch được **cố định trong `autodub_gui/remote_gate.py`** (mã hóa
base64 để không lộ khi quét chuỗi trong exe):

```python
# https://voxdub-api.duckdns.org/status
_DEFAULT_URL_B64 = "aHR0cHM6Ly92b3hkdWItYXBpLmR1Y2tkbnMub3JnL3N0YXR1cw=="
```

- **Bản exe**: chỉ dùng URL cố định này (hoặc URL nhúng lúc build nếu có).
  Người dùng **không thể** ghi đè hay tắt qua `.env`.
- **Chạy từ source (dev)**: nếu cả 2 nguồn trên rỗng mới rơi về biến môi
  trường `REMOTE_CONTROL_URL`.
- Đổi server: `py -c "import base64; print(base64.b64encode(b'https://domain/status').decode())"`
  rồi thay chuỗi `_DEFAULT_URL_B64`.

## Triển khai trên VPS Windows (Remote Desktop)

Yêu cầu: Node.js >= 18 đã cài, nginx đã có sẵn TLS cho
`voxdub-api.duckdns.org` (win-acme tự gia hạn).

### Bước 1 — Copy thư mục `control_server/` lên VPS

Ví dụ vào `C:\voxdub\control_server\` (qua RDP kéo-thả hoặc share clipboard).

### Bước 2 — Tạo file khởi động `start_server.cmd` trên VPS

Có sẵn mẫu `start_server.example.cmd` — chép thành `start_server.cmd`
(tên này đã gitignore) rồi dán token mới vào:

```bat
@echo off
REM Token do ban tu sinh — KHONG dung lai token cu, KHONG commit file nay
set "ADMIN_TOKEN=DAN_TOKEN_MOI_VAO_DAY"
set "PORT=3001"
set "HOST=127.0.0.1"
set "TRUST_PROXY=1"
cd /d C:\voxdub\control_server
node server.js
```

### Bước 3 — Chạy bền vững

Cách đơn giản nhất trên Windows: Task Scheduler.

1. Mở **Task Scheduler** → Create Task.
2. General: chọn *Run whether user is logged on or not*.
3. Triggers: *At startup*.
4. Actions: Start a program → `C:\voxdub\control_server\start_server.cmd`.
5. Settings: bật *Restart the task if it fails* (mỗi 1 phút, 3 lần).

(Hoặc dùng NSSM/pm2 nếu quen.)

### Bước 4 — Nối nginx vào backend

**Cách khuyến nghị**: dùng file cấu hình đầy đủ `nginx-voxdub.conf` kèm sẵn
trong thư mục này (có ACME passthrough, HSTS, timeout, 502 JSON fallback).
Hướng dẫn cài nằm ngay trong phần comment đầu file đó.

Hoặc sửa tay tối thiểu: trong server block 443, thay `location /` placeholder bằng:

```nginx
location / {
    proxy_pass http://127.0.0.1:3001;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto https;
}
```

Rồi `nginx -s reload`.

### Bước 5 — Kiểm tra

```bash
curl https://voxdub-api.duckdns.org/health
# → {"ok":true,"uptime_s":...,"enabled":true}
```

Firewall VPS chỉ cần mở 80/443 (nginx). **Đóng port 3001 và 8788** nếu
trước đây từng mở.

## API

| Endpoint | Auth | Mô tả |
|---|---|---|
| `GET /status` | không | App gọi — trả `{"enabled": bool, "message": "..."}` |
| `GET /health` | không | Cho uptime monitor — `{"ok": true, "uptime_s": ...}` |
| `POST /admin/set` | `X-Admin-Token` | Đổi trạng thái |
| `GET /admin/status` | `X-Admin-Token` | Trạng thái + thống kê (số lượt check, uptime) |

## Bật / tắt app

Trên máy admin: sửa `config.cmd` (server + token) một lần, rồi đúp chuột:

- `tat_app.cmd` — TẮT app từ xa (message tiếng Việt sửa trong `payload_off.json`)
- `bat_app.cmd` — BẬT lại
- `trang_thai.cmd` — xem trạng thái + thống kê

Hoặc bằng curl:

```bash
# TẮT app (người dùng mở app sẽ thấy thông báo rồi thoát;
# app đang mở sẽ nhận lệnh trong vòng 30 phút)
curl -X POST https://voxdub-api.duckdns.org/admin/set \
     -H "X-Admin-Token: <token>" \
     -H "Content-Type: application/json" \
     -d '{"enabled": false, "message": "App tạm dừng để bảo trì"}'

# BẬT lại
curl -X POST https://voxdub-api.duckdns.org/admin/set \
     -H "X-Admin-Token: <token>" \
     -H "Content-Type: application/json" \
     -d '{"enabled": true, "message": ""}'

# Xem trạng thái
curl https://voxdub-api.duckdns.org/status
```

## An toàn & vận hành

- **TLS bắt buộc ở rìa** (nginx) — token không bao giờ đi cleartext qua mạng.
- Token so sánh **timing-safe** (chống dò token bằng đo thời gian);
  **không có token mặc định** — thiếu `ADMIN_TOKEN` → `/admin/*` trả 503.
- Sai token 5 lần → IP bị khóa 15 phút. `/admin/*` giới hạn 10 req/phút/IP,
  `/status` 60 req/phút/IP. (Cần `TRUST_PROXY=1` khi sau nginx để rate-limit
  theo IP thật thay vì IP của proxy.)
- Mọi thay đổi trạng thái và mọi lần sai token ghi vào `access.log`;
  log tự xoay khi vượt 5 MB (giữ 1 bản `.1`).
- `state.json` ghi kiểu atomic (tmp + rename) — mất điện không hỏng file.
- Body giới hạn 4 KB, message giới hạn 500 ký tự.
- Tắt êm bằng SIGINT/SIGTERM.

**Lưu ý token**: token nằm trong `config.cmd` (máy admin) và
`start_server.cmd` (VPS) — cả hai đều đã gitignore, **đừng bao giờ commit
token thật**. Token cũ `Thanh2004@` đã lộ trong source — coi như bỏ, bắt
buộc dùng token mới.
