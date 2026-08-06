# Kế hoạch hệ thống Credit "Vox" — VoxDub Studio (BẢN CUỐI — SẴN SÀNG KHỞI CÔNG)

> Phiên bản: 3.1 — 2026-08-05 (rà soát lại theo code thực tế; thay thế 3.0)
> Trạng thái: **ĐÃ DUYỆT TOÀN BỘ — CHƯA KHỞI CÔNG** (chờ lệnh của chủ dự án)
> Đối tượng đọc: **AI agent / dev triển khai ở phiên làm việc SAU, không có
> ngữ cảnh gì ngoài file này** — vì vậy mục 1 mô tả bối cảnh repo chi tiết.
> Phạm vi: app VoxDub (PySide6/Python, repo này) + server (nâng cấp từ
> `control_server/`).

---

## 0. Quyết định ĐÃ CHỐT (đừng hỏi lại chủ dự án những mục này)

| # | Vấn đề | Quyết định | Ngày |
|---|---|---|---|
| 1 | Cách tính giá | **Theo số câu thoại** (đếm sau bước ASR) | 2026-08-03 |
| 2 | Tỷ giá | **1 Vox = 10 VND** (config server, nâng sau được) | 2026-08-03 |
| 3 | User tự hủy giữa chừng | **Hoàn 100%**; chạy lại phần dang dở → quote mới, tính đủ như video mới | 2026-08-03 |
| 4 | Quà 1 tháng | **Có trần 1.500 Vox/ngày** — ĐÃ DUYỆT cùng 3 lớp chống farm (mục 8.2) | 2026-08-03 |
| 5 | Dịch khi billing bật | **Ép 100% qua server**. Engine cũ **ĐÃ XÓA HOÀN TOÀN** (xem mục 9.3 — đã dọn toàn bộ tài liệu và code) | 2026-08-03 |
| 6 | Cổng thanh toán | **PayOS** — chủ dự án ĐÃ CÓ hộ kinh doanh hợp pháp + tài khoản MB | 2026-08-03 |
| 7 | Free mode (billing tắt) | **Giữ vĩnh viễn** đường user tự dùng key Gemini/OpenRouter của họ | 2026-08-03 |
| 8 | Điều kiện nhận quà | **CHỈ CẦN xác thực Gmail/email + nhập SĐT + tên. KHÔNG OTP SĐT** (bỏ hẳn SMS/OTP khỏi kế hoạch) | 2026-08-03 |
| 9 | Domain | **CHƯA CHỐT TÊN** — việc đầu tiên của Giai đoạn 0 là hỏi chủ dự án tên domain rồi mua | — |
| 10 | Thời điểm | **Chưa khởi công** — agent sau chỉ bắt đầu khi chủ dự án ra lệnh, và bắt đầu từ Giai đoạn 0 | — |

---

## 1. BỐI CẢNH REPO (đọc kỹ — agent sau không có thông tin nào khác)

> Đã RÀ SOÁT LẠI theo code thực tế ngày 2026-08-05 (mục 15C liệt kê những gì
> đã đổi so với bản 3.0). Nếu repo lại thay đổi sau ngày này: khảo sát lại
> code trước, cập nhật file này, rồi mới làm (mục 16).

### 1.1 Sản phẩm hiện tại

**VoxDub Studio** — app Windows lồng tiếng video (chủ yếu tiếng Trung) sang
tiếng Việt. Free hoàn toàn, chạy local. Pipeline: tải video → tách audio →
tách nhạc nền (Demucs) → ASR (Whisper hoặc Paraformer) → dịch → TTS local
(**chỉ VieNeu** — các engine TTS cũ đã bị gỡ) → ghép video. Đóng gói
PyInstaller onedir qua `py scripts/build_exe.py` (KHÔNG chạy PyInstaller
trực tiếp), exe `VoxDub.exe`. Version app hiện tại: `2.0.0`
(`autodub_gui/__init__.py`).

**Kiến trúc dịch hiện tại (QUAN TRỌNG — đã đổi so với bản 3.0):**

- `TRANSLATE_ENGINES = ("openrouter", "gemini", "openai", "anthropic",
  "deepseek", "custom")` — default `openrouter` (`autodub/config.py`).
- `autodub/text/translate_openai.py` — engine đa nhà cung cấp tương thích
  OpenAI (dict `PROVIDERS` chứa openrouter/openai/anthropic/deepseek/custom,
  mỗi nơi một bộ key/url/model trong `.env`). `translate_gemini.py` riêng
  cho Gemini API gốc.
- `autodub/text/translate_common.py` — helper dùng chung (`TranslateError`,
  `parse_response_segments`, `merge_translations`, `contains_cjk`,
  `_repair_json`...).
- **Dịch là 3 LƯỢT, không phải 1** (xem `pipeline.py::_auto_translate`):
  1. **Lượt 0 — phân tích ngữ cảnh** (`translate_analysis.py`): 1 call lớn
     tóm tắt video, xưng hô, thuật ngữ; cache `data/video_context.json`;
     bật/tắt bằng `TRANSLATE_ANALYSIS`.
  2. **Lượt dịch** — theo batch, retry, chia đôi batch khi lỗi, kèm
     `_fix_cjk_leftovers` (dịch lại câu còn lẫn chữ Hán).
  3. **Lượt rà soát** (`translate_review.py`): soát câu tràn khung / lẫn
     chữ Hán / sót ý rồi dịch lại đúng các câu đó; bật/tắt bằng
     `TRANSLATE_REVIEW`.
  Cả 3 lượt đều đi qua cùng engine user chọn. Mô hình đếm/trần ở mục 9.1
  đã được cập nhật theo thực tế này.

### 1.2 File/cơ chế SẼ ĐỤNG TỚI khi triển khai

| Đường dẫn | Vai trò | Liên quan gì tới kế hoạch |
|---|---|---|
| `autodub/pipeline.py` | Pipeline chính. **Đã có cơ chế dừng-resume**: khi thiếu bản dịch, `run()` trả `DubResult(status="translate_pending", work_dir=...)`, GUI hiện banner + nút tiếp tục, chạy lại bằng `DubRequest(resume_dir=...)` — mọi bước xong rồi tự skip nhờ cache file. `transcript_original.json` được ghi qua `data_path(...)` trước bước dịch; `_auto_translate` trả `None` là dừng chờ dịch tay. | **Điểm dừng quote (`status="quote_pending"`) làm Y HỆT pattern này** — thêm 1 nhánh return sau bước ASR (sau khi ghi `transcript_original.json`, trước lời gọi `_auto_translate`), khi billing bật. |
| `autodub/workdir.py` | Bố cục thư mục output: gốc = `dubbed_video.mp4` + `transcript_vi.srt`; kỹ thuật vào `data/` (`original_audio.wav`, transcripts, segments, report...); metadata vào `youtube/`. Có `is_legacy_layout()` fallback thư mục cũ. Truy cập qua `data_path()`/`data_dir()`/`youtube_dir()`. | `data/job_token.json`, `data/job_status.json` đặt ở đây (thêm qua `data_path`). Hủy job → xóa `data/transcript_vi.json` + `transcript_vi.srt` (gốc) + `youtube/`. `video_hash` = SHA256 của `data/original_audio.wav`. |
| `autodub/text/translate_common.py` | Helper dịch dùng chung (đã tách sẵn — việc này XONG rồi). | `translate_server.py` mới import từ đây, không đụng gì thêm. |
| `autodub/text/translate_openai.py` | Engine đa nhà cung cấp: dict `PROVIDERS`, hàm `chat()` (dùng chung cho cả metadata), `translate_segments()`, `check_model()`. Prompt build ở `translate_hint.py::build_translation_prompt`. | Free mode giữ nguyên. Billing bật → **cách làm gọn nhất: thêm engine `"server"`** — hoặc file `translate_server.py` copy khung `translate_openai.py`, đổi đích HTTP sang `/translate/batch` + auth `Bearer job_token`, bỏ key user. Tái dụng toàn bộ prompt/retry/repair. |
| `autodub/text/translate_analysis.py`, `translate_review.py` | Lượt 0 (phân tích ngữ cảnh, cache theo work_dir) và lượt rà soát. Đều gọi engine đang chọn. | Billing bật → 2 lượt này CŨNG phải qua server (mục 9.1): analysis đi endpoint riêng đếm 1 lần/job; review đi `/translate/batch` trong trần chung. |
| `autodub/content/generator.py` | Tạo metadata YouTube — thử engine dịch đang chọn trước, fallback Gemini rồi ngược lại (chuỗi `attempts`). `GENERATE_METADATA` + `GENERATE_THUMBNAIL_IMAGES` trong Settings. | Billing bật → BỎ chuỗi fallback, chỉ 1 đường `/metadata/generate` của server, tính 20 Vox (thumbnail 15 Vox nếu bật). |
| `autodub_gui/app.py` | `MainWindow` (pages lazy qua `_ensure_page`), kill-switch check chạy nền, 30 phút/lần. | Sidebar thêm khối "Tên · N Vox" (KHÔNG emoji — test cấm); parse thêm `billing` từ payload /status. |
| `autodub_gui/remote_gate.py` | Gọi `GET /status`. URL cố định base64 `_DEFAULT_URL_B64` (hiện `http://14.225.192.101:8788/status` — HTTP trần!). Fail-open. Exe bỏ qua `.env`. | Đổi URL sang domain HTTPS mới (đổi chuỗi base64 + build lại). Mở rộng parse payload (mục 5). Kill-switch giữ fail-open; billing thì fail-closed (mục 2.3). |
| `autodub_gui/pages/new_project_page.py` | Trang tạo dự án (thay `dub_tab.py` cũ). Có sẵn `pending_banner` (Banner "warning") + nút "Đã dịch xong, tiếp tục" cho `translate_pending`. | Banner quote làm cùng khuôn `pending_banner`. |
| `autodub_gui/pages/batch_page.py` | Trang batch (thay `batch_tab.py` cũ); chạy tuần tự qua `BatchWorker` (`autodub_gui/workers.py`). | Thêm cột Vox + confirm tuần tự (mục 7.3, 8.3). |
| `autodub_gui/pages/settings_page.py` + `settings_fields.py` + `settings_panels.py` | Trang Cài đặt **spec-driven**: 6 tab (`Cơ bản / Giọng đọc / Phụ đề / Hiệu suất / Kết nối / Nâng cao`), field khai báo trong `settings_fields.py`, panel engine (Gemini + 5 nơi OpenAI-compat) render từ spec trong `settings_panels.py`. `tests/test_settings_fields.py` ép key spec ↔ `.env.example` đồng bộ. | Billing bật → ẩn nhóm engine dịch trong tab "Kết nối", hiện "Dịch do máy chủ VoxDub đảm nhận [OK]". Ẩn theo cờ runtime, KHÔNG xóa field khỏi spec (giữ free mode + test xanh). |
| `autodub_gui/run_state.py` | `RunRegistry` (job đang chạy, activity feed 20 sự kiện, chấm chưa đọc). | `billing_client` gắn vào đây cập nhật số dư sau confirm/complete/cancel; thông báo server (nạp ok, sắp hết Vox) trộn vào activity feed. |
| `autodub_gui/status_text.py`, `tokens.py` | Chuỗi trạng thái `[OK]/[!]/[X]` (app CẤM emoji — `tests/test_gui_no_emoji.py`); `tokens.py` là file DUY NHẤT chứa mã màu hex (`tests/test_ui_tokens.py`). | Mọi UI credit dùng 2 file này. Các chuỗi có emoji trong file kế hoạch này (mục 7.1, 8.1, 10.2) chỉ là MINH HỌA — code thật dùng hằng `status_text.py`. |
| `autodub/editor.py` | `rebuild_output()` / `rebuild_subtitles()` — xuất lại video từ work_dir. | Lớp 7.2.2: check `data/job_status.json` trước khi rebuild. |
| `control_server/server.js` | Server Node **zero-dependency** hiện tại: `/status`, `/health`, `/admin/set|status`, rate-limit, timing-safe token, atomic state.json, systemd unit `autodub-control.service`. | **Nền tảng để nâng cấp** — giữ nguyên hành vi `/status` (chỉ THÊM trường), port code rate-limit/token sang server mới. |
| `tests/` | **440 test** pytest đang xanh (2026-08-05). | Sau mỗi giai đoạn phải xanh lại. Test mới của credit đặt cạnh (`test_billing_client.py`, `test_pipeline_quote.py`...). |

### 1.3 Việc tồn đọng NGUY HIỂM (từ audit trước — chưa ai làm)

- **ADMIN_TOKEN của control server + 2 API key (Gemini/OpenRouter trong .env
  máy dev) ĐÃ TỪNG LỘ** → bắt buộc rotate ở Giai đoạn 0 trước khi đụng tới
  bất cứ thứ gì có tiền.
- Server đang chạy **HTTP trần trên IP** (`http://14.225.192.101:8788`) —
  có tiền là phải domain + TLS. (Đã xác nhận 2026-08-05: `remote_gate.py`
  vẫn trỏ IP trần — hạng mục GĐ0 còn nguyên.)

---

## 2. Nguyên tắc thiết kế (bất di bất dịch)

1. **Server là nguồn sự thật duy nhất về tiền.** Số dư chỉ đổi qua bảng
   `ledger` (append-only); app chỉ hiển thị, không bao giờ tự cộng trừ local.
2. **Billing tắt = app hôm nay, không thiếu không thừa.** Mọi check credit
   bọc `if billing_enabled`. Free mode: đăng nhập là TÙY CHỌN, không màn hình
   nào nhắc tiền. Free mode dùng key dịch của user (quyết định #7, vĩnh viễn).
3. **Fail-closed có nhân nhượng.** Video ĐÃ trừ Vox mà mất mạng → chạy nốt
   (job_token cache trong `data/job_token.json`). Video MỚI mà không xác minh
   được số dư → không bắt đầu, báo "không kết nối được máy chủ".
   (Đối lập với kill-switch: kill-switch fail-OPEN, billing fail-CLOSED.)
4. **Idempotency mọi chỗ có tiền** — trừ/cộng/hoàn/webhook đều có key duy
   nhất; gọi 2 lần = no-op lần 2.
5. **Không tin client.** App đếm câu, server ra giá cuối; proxy dịch đếm số
   câu THỰC đi qua — vượt trần `num_segments × translate.segment_cap_factor`
   (khởi điểm ×1.5 — mục 9.1) là 403.
6. **Mọi tham số tiền nằm bảng `config` server** — đổi giá/trần/model không
   cần release app.
7. **Ledger đối soát được**: `wallets.balance == SUM(ledger.delta)`; cron đêm
   kiểm, lệch 1 Vox cũng báo admin.

---

## 3. Đơn vị Vox & bảng giá

### 3.1 Công thức giá 1 video (giá trị KHỞI ĐIỂM — tất cả nằm trong `config`)

```
base        = 30 Vox                  # phí nền mỗi video
per_segment = 1 Vox × số câu          # dịch qua server
metadata    = 20 Vox   (nếu user bật tạo tiêu đề/mô tả YouTube)
thumbnail   = 15 Vox   (nếu user bật tạo ảnh bìa — Gemini image API)
tổng = base + per_segment + (metadata) + (thumbnail)
```

| Ví dụ | Tính | Vox | VND |
|---|---|---|---|
| 100 câu + metadata | 30+100+20 | 150 | 1.500 |
| 30 câu, không metadata | 30+30 | 60 | 600 |
| 300 câu + metadata + thumbnail | 30+300+20+15 | 365 | 3.650 |

### 3.2 Biên lợi nhuận (theo dõi từ ngày đầu — 1 Vox = 10đ nên biên MỎNG)

**LƯU Ý (cập nhật 2026-08-05): pipeline dịch là 3 LƯỢT** (phân tích ngữ
cảnh → dịch batch + vá CJK → rà soát — mục 1.1). Chi phí 1 video 100 câu
với model rẻ (gemini-flash qua OpenRouter) ước tính CẢ 3 LƯỢT: **~40–60k
token ≈ 400–700 VND**; metadata ~50 VND. Giá bán 1.500 VND ⇒ biên ~50–60%
(mỏng hơn ước tính cũ vốn chỉ đếm 1 lượt). Model đắt sẽ ăn thủng giá ngay,
vì vậy BẮT BUỘC:

- Bảng `job_usage` ghi token thực + chi phí ước tính từng job, **tách theo
  pha** (`analysis` / `translate` / `review` / `metadata`) để biết pha nào
  ăn tiền.
- Dashboard có thẻ **"chi phí API / doanh thu 7 ngày"** — vượt 50% hiện đỏ.
- Admin đổi model / nâng hệ số giá trên dashboard, hiệu lực tức thì.
- Nếu biên vẫn mỏng: admin có quyền tắt lượt analysis/review **phía server**
  qua `config: translate.analysis_enabled / review_enabled` (đánh đổi chất
  lượng — quyết định lúc vận hành, không hardcode).

### 3.3 Miễn phí kể cả khi billing bật

- Tab "Tải video" (chỉ tải, chạy máy user).
- Editor: sửa câu + re-TTS từng câu của video **đã trả phí** (trần 200 lần
  re-TTS/video, đếm local trong `data/job_status.json`).
- Rebuild/xuất lại video đã trả phí.
- Nghe thử giọng trong Cài đặt.

> Logic: thu ở chỗ tốn tài nguyên server (dịch, metadata) và ở giá trị lõi
> (một video hoàn chỉnh); thứ chạy 100% máy user thì không thu.

### 3.4 Gói nạp (khởi điểm — bảng `packages`, admin CRUD)

| Gói | VND | Vox gốc | Bonus | Thực nhận |
|---|---|---|---|---|
| Dùng thử | 20.000 | 2.000 | — | 2.000 (~13 video) |
| Phổ thông | 50.000 | 5.000 | +10% | 5.500 |
| Pro | 100.000 | 10.000 | +20% | 12.000 |
| Studio | 500.000 | 50.000 | +30% | 65.000 |

- Vox nạp lẻ **không hết hạn**; Vox quà hết hạn theo gói.
- Thứ tự tiêu: **quà (có hạn, trong trần ngày) trước → nạp sau**.
- PayOS tối thiểu ~2.000 VND/giao dịch → gói thấp nhất 20k là an toàn.

### 3.5 Gói quà 1 tháng (user mới) — ĐÃ CẬP NHẬT THEO QUYẾT ĐỊNH #8

| Tham số | Giá trị |
|---|---|
| Điều kiện nhận | **email verified (mã 6 số hoặc Google OAuth) + đã NHẬP SĐT + tên** — KHÔNG OTP, SĐT không cần xác minh |
| Thời hạn | 30 ngày kể từ lúc nhận |
| Trần tiêu | **1.500 Vox/ngày** (~10 video 100 câu/ngày) — reset 0h giờ VN |
| Chống farm | mục 8.2 (vì bỏ OTP nên phải bù bằng lớp khác — ĐỌC KỸ) |
| Công tắc | `welcome_gift_enabled` — tắt: người đã nhận dùng nốt `expires_at` của họ; người đăng ký sau không nhận |

SĐT nhập tay lưu vào `users.phone` (mục đích marketing như chủ dự án yêu
cầu) — hiển thị form ghi rõ "dùng để hỗ trợ và thông báo ưu đãi" + checkbox
opt-in riêng cho quảng cáo (NĐ13).

---

## 4. Kiến trúc tổng thể

```
┌────────────── App VoxDub (máy user) ───────────────┐
│  GUI PySide6                                        │
│  ├─ auth_client.py     đăng nhập/đăng ký/Google     │
│  ├─ billing_client.py  quote/confirm/cancel/topup   │
│  └─ pipeline (cũ) + điểm dừng "quote_pending"       │
│     dịch qua translate_server.py khi billing bật    │
└──────────────┬─────────────────────────────────────┘
               │ HTTPS bắt buộc (Caddy + Let's Encrypt + domain mới)
┌──────────────▼─────────────────────────────────────┐
│  VoxDub API Server (VPS — nâng cấp control_server)  │
│  Node.js ≥ 20 + Fastify + better-sqlite3 (WAL)      │
│  ├─ /status         kill-switch + billing + gift    │
│  ├─ /auth/*         email+pass, Google OAuth PKCE   │
│  ├─ /billing/*      quote, confirm, cancel, ledger  │
│  ├─ /topup/*        PayOS: tạo đơn QR, webhook      │
│  ├─ /translate/*    proxy dịch (server giữ key)     │
│  ├─ /metadata/*     proxy tiêu đề/mô tả             │
│  ├─ /admin/*        API cho dashboard               │
│  └─ /admin-ui/      web dashboard (tĩnh)            │
└──────────────┬─────────────────────────────────────┘
        ┌──────┴───────┬──────────────┬──────────────┐
        ▼              ▼              ▼              ▼
     PayOS        Google OAuth   OpenRouter      Gemini
  (MB hộ KD)                     (key server)  (key server)
```

**Stack chốt** (bỏ triết lý zero-dependency của server cũ — tiền cần đồ tử tế):
- `fastify` (schema validation built-in), `better-sqlite3` (WAL,
  `BEGIN IMMEDIATE` cho transaction tiền; đủ ~10k user, SQL chuẩn để sau
  migrate Postgres), `argon2`, `jsonwebtoken`, `google-auth-library`,
  `@payos/node` (SDK chính thức).
- Email verify: SMTP qua **Brevo** free tier (300 mail/ngày) hoặc tương đương.
- Captcha: **Cloudflare Turnstile** (free) trên form đăng ký.
- Backup: cron `sqlite3 .backup` mỗi 6h → nén → rsync/object storage;
  **restore drill mỗi tháng** (backup chưa restore thử = không có backup).
- `/status` giữ tương thích tuyệt đối với app cũ (CHỈ thêm trường mới).

---

## 5. Ba công tắc điều khiển từ xa

`GET /status` (public; app gọi lúc mở + mỗi 30 phút — nhịp hiện tại):

```json
{
  "enabled": true,               // ① kill-switch cũ — false là app thoát (fail-open)
  "message": "",
  "billing": {
    "enabled": false,            // ② CÔNG TẮC BILLING
    "min_app_version": "2.1",    //    app cũ hơn → yêu cầu update mới dùng chế độ trả phí
    "welcome_gift_enabled": true // ③ CÔNG TẮC QUÀ 1 THÁNG
  }
}
```

| Tình huống | Hành vi |
|---|---|
| billing `false` (mặc định khi deploy) | App free 100% như hiện nay. Nút "Đăng nhập" có, TÙY CHỌN. |
| billing `true` | Bắt buộc đăng nhập trước khi lồng tiếng; sidebar hiện "👤 Tên · 4.800 Vox"; pipeline dừng quote; nạp Vox mở; Cài đặt ẩn panel engine dịch. |
| billing bật GIỮA job đang chạy | Job đã bắt đầu dưới luật free → chạy nốt free; job mới theo luật mới. |
| billing tắt sau khi từng bật | Free ngay; số dư Vox **đóng băng không mất** — bật lại còn nguyên. |
| gift tắt | grants đã cấp giữ `expires_at` riêng → dùng nốt; user mới không nhận. |

---

## 6. Tài khoản & xác thực (KHÔNG CÓ OTP — quyết định #8)

- **Email + mật khẩu**: argon2id; verify email bằng mã 6 số (hết hạn 10 phút,
  5 lần thử, gửi lại tối đa 3 lần/giờ).
- **Google OAuth**: Authorization Code + **PKCE**, mở browser HỆ THỐNG +
  loopback `http://127.0.0.1:<port>/callback` (KHÔNG webview — Google chặn).
  Đăng nhập Google = email verified luôn.
- **SĐT**: chỉ là Ô NHẬP trong form hồ sơ (bắt buộc điền để nhận quà, không
  verify). Lưu `users.phone` + chuẩn hóa `phone_normalized` (bỏ khoảng
  trắng, +84 → 0) để check trùng.
- Token: **access JWT 15 phút + refresh 30 ngày** (rotate mỗi lần dùng, lưu
  hash, thu hồi từng phiên). App lưu refresh token bằng **Windows Credential
  Manager** (lib `keyring`) — TUYỆT ĐỐI không ghi `.env`.
- **Thiết bị**: tối đa 2 thiết bị hoạt động/tài khoản (fingerprint = SHA256
  của Windows MachineGuid trong registry `HKLM\SOFTWARE\Microsoft\Cryptography`).
  Máy thứ 3 → đá phiên cũ nhất + thông báo.
- **NĐ13/2023** (dữ liệu cá nhân — hộ KD vẫn thuộc phạm vi): consent lúc đăng
  ký, trang privacy policy, `DELETE /me` (soft-delete 30 ngày rồi xóa thật,
  ledger giữ dạng ẩn danh vì nghĩa vụ kế toán), opt-in riêng cho quảng cáo.

---

## 7. Luồng credit lõi

### 7.1 Một video

```
STEP 1-3: tải video → tách audio → ASR         (free — chạy máy user)
     │
     ▼
[billing bật?] ─không─► chạy tiếp như hiện nay (key dịch của user)
     │ có
     ▼
POST /billing/quote
  gửi:  {video_hash, num_segments, total_speech_s, want_metadata,
         want_thumbnail, app_version}
  nhận: {quote_id, vox_cost, breakdown, balance, expires_at: +30 phút}
  (video_hash = SHA256 của data/original_audio.wav — ổn định qua resume)
     │
     ▼
Pipeline return DubResult(status="quote_pending", quote=...)
GUI banner (làm y khuôn banner translate_pending có sẵn trong
pages/new_project_page.py — `pending_banner`; chuỗi thật KHÔNG emoji):
  ┌──────────────────────────────────────────────────────┐
  │ Video có 100 câu thoại — chi phí 150 Vox (1.500đ)    │
  │ Số dư: 4.800 Vox                                     │
  │ [▶ Tiếp tục — trừ 150 Vox]  [Hủy]  [Nạp thêm Vox]   │
  └──────────────────────────────────────────────────────┘
     │ Tiếp tục
     ▼
POST /billing/confirm {quote_id}
  server TRANSACTION (BEGIN IMMEDIATE):
    khóa ví → resolve nguồn trừ (quà trong trần ngày trước → nạp sau)
    → đủ: ledger{consume,-150,idempotency_key} + jobs{status:"paid"}
          → trả {job_token}   (JWT 24h: job_id, num_segments, video_hash)
    → thiếu: 402 {need_topup: N} → GUI mở dialog nạp
     │
     ▼
App ghi data/job_token.json → pipeline resume:
  dịch qua POST /translate/batch (Bearer job_token) → TTS local → ghép local
     │
     ├─ XONG      → POST /billing/jobs/{id}/complete
     │              app ghi data/job_status.json = {"status":"completed"}
     ├─ LỖI HỆ THỐNG → POST /billing/jobs/{id}/fail {error}
     │              → server HOÀN 100% (ledger refund), job=failed_refunded
     └─ USER HỦY  → POST /billing/jobs/{id}/cancel → HOÀN 100%
                    → app xóa data/transcript_vi.json + transcript_vi.srt
                      + youtube/ + ghi job_status.json = {"status":"cancelled"}
```

- Quote hết hạn 30 phút → phải quote lại.
- Resume video **đã trả** (job `paid` dở hoặc `completed`): server thấy
  video_hash + job hợp lệ → KHÔNG trừ lại, trả lại job_token.
- Resume video **đã hủy**: quote mới, tính đủ (quyết định #3). Cache TTS wav
  local còn → chạy lại nhanh, nhưng tiền đủ.
- Job `paid` quá 48h không complete/fail → cron auto-fail-refund + báo admin.

### 7.2 Bịt lỗ hổng hoàn-100% (ĐÃ DUYỆT — 3 lớp, giữ nguyên khi triển khai)

Kẽ hở: sau bước dịch, `transcript_vi.json` đã trên máy user → hủy lấy lại
tiền → tự rebuild = video free. Bịt:

1. **App xóa sản phẩm trả phí khi hủy**: `data/transcript_vi.json`,
   `transcript_vi.srt` (gốc work_dir), thư mục `youtube/`. Segments wav giữ
   (vô nghĩa khi thiếu bản dịch).
2. **Editor/rebuild đòi job completed**: `data/job_status.json` phải là
   `completed`; `cancelled` → app từ chối rebuild, yêu cầu chạy lại (quote
   mới). Free mode / work_dir cũ không có file này → cho qua (tương thích).
3. **Guard server**: user có **>3 lần hủy-sau-khi-dịch-xong trong 30 ngày**
   (đếm bằng `jobs.cancel_after_translate`) → các lần hủy sau hoàn
   `100% − per_segment×số câu đã dịch`, app hiện thông báo minh bạch. Tham
   số `cancel_guard.max_free = 3` trong config.

### 7.3 Batch

```
1. Batch ASR lần lượt (không dừng chờ), mỗi video xong ASR → quote ngay
2. GUI bảng batch thêm cột "Vox", dòng tổng:
   "Tổng tạm tính: 312 Vox — Số dư: 4.650"
   [▶ Chạy tất cả khi tính xong]   [▶ Chạy N video đã tính]
3. User xác nhận 1 LẦN → app confirm TUẦN TỰ từng quote:
   ok → video vào hàng dịch/TTS; hết Vox giữa chừng → video còn lại đánh
   dấu "Thiếu Vox ⚠", batch hoàn thành phần đã trả, nút [Nạp rồi chạy tiếp]
4. Hủy 1 video trong batch = luật 7.2 áp riêng video đó
```

Không escrow tổng (giam tiền user, code phức tạp) — trừ ngay trước khi từng
video vào bước dịch.

### 7.4 Số dư trên GUI

Sidebar `👤 Tên · 4.800 Vox`; refresh khi: mở app, sau confirm/nạp/hủy, bấm
tay. KHÔNG poll liên tục. Lệch do dùng 2 máy → confirm là trọng tài; 402 thì
GUI cập nhật số thật + mở dialog nạp.

---

## 8. Quà tặng & chống farm KHÔNG CÓ OTP (quan trọng — đã bỏ xác minh SĐT)

### 8.1 Luồng nhận quà

```
Đăng ký (email/Google) → verify email → form hồ sơ: TÊN + SĐT (nhập tay)
→ nếu welcome_gift_enabled && user chưa từng nhận && qua các check 8.2
→ tạo grants{package: gift-30d, vox_per_day_cap: 1500, expires_at: +30d}
→ app hiện "🎁 Bạn được tặng gói dùng thử 30 ngày (tối đa 1.500 Vox/ngày)"
```

### 8.2 Chống farm (bù cho việc bỏ OTP — TẤT CẢ các lớp sau đều bắt buộc)

Bỏ OTP nghĩa là SĐT nhập tay **không đáng tin** — một người có thể gõ số bừa.
Các lớp bù (đơn lẻ đều vượt được, cộng lại đủ đắt để farm không bõ):

| Lớp | Chi tiết |
|---|---|
| 1. Email chuẩn hóa | Gmail: bỏ dấu chấm + phần sau dấu `+` trước khi check UNIQUE (`a.b+x@gmail.com` ≡ `ab@gmail.com`). Chặn domain email rác dùng danh sách disposable-email-domains (cập nhật định kỳ). |
| 2. SĐT chuẩn hóa UNIQUE | `phone_normalized` UNIQUE cho quà — 1 số chỉ nhận quà 1 lần trọn đời (dù không verify, vẫn chặn kẻ lười đổi số). Validate định dạng VN (10 số, đầu 03/05/07/08/09). |
| 3. Thiết bị | 1 device fingerprint chỉ gắn tối đa **2 tài khoản từng nhận quà** — chặn farm hàng loạt trên 1 máy. |
| 4. Turnstile | Trên form đăng ký + form nhận quà. |
| 5. Trần ngày | 1.500 Vox/ngày — farm 10 acc cũng chỉ tiêu chậm, trong khi chi phí server cho họ vẫn bị trần chặn. |
| 6. Công tắc + hạ trần nhanh | Thấy farm bùng → admin tắt `welcome_gift_enabled` hoặc hạ trần ngay trên dashboard, không cần release. |
| 7. Nâng cấp tương lai (ghi nhận, chưa làm) | Nếu farm vẫn đau: bật lại OTP chỉ-cho-quà (thêm cột `phone_verified`, code chừa sẵn chỗ). |

### 8.3 Dữ liệu marketing

`users`: email, tên, SĐT (nhập tay), nguồn đăng ký, ngày, last_seen, tổng
nạp/tiêu, số video. Trang Users trên dashboard: lọc/sort/**export CSV**.
Ghi chú NĐ13 ở mục 6 áp dụng.

---

## 9. Dịch 100% qua server (engine cũ đã xóa toàn bộ — mục 9.3)

### 9.1 Billing BẬT — phải phủ CẢ 3 LƯỢT dịch (cập nhật 2026-08-05)

Pipeline dịch hiện có 3 lượt (mục 1.1): **analysis → translate (+ vá CJK)
→ review**. Cả 3 đều phải qua server khi billing bật, nếu không user tự
thấy app gọi thẳng OpenRouter bằng key nào đó — vô nghĩa.

**Phía app:**

- Cách làm: thêm engine `"server"` vào kiến trúc engine hiện tại — hoặc
  file mới `autodub/text/translate_server.py` copy khung
  `translate_openai.py` (hàm `chat()` + `translate_segments()`), đổi:
  - endpoint → `https://<domain>/translate/batch` (và `/translate/analyze`)
  - auth header → `Bearer <job_token>` (đọc từ `data/job_token.json`)
  - bỏ toàn bộ key user.
- **GIỮ nguyên Ở PHÍA APP**: `build_translation_prompt` (ngữ cảnh user +
  context giữa batch), retry, chia đôi batch, `_repair_json`,
  `_fix_cjk_leftovers`, và 2 module `translate_analysis.py` /
  `translate_review.py` — chúng chỉ đổi "đường dây" gọi chat, không đổi
  logic. Server chỉ là proxy có đếm.
- Billing bật → `_auto_translate` ép engine = server cho cả 3 lượt, bỏ qua
  `translate_engine` trong `.env`.

**Phía server — 2 endpoint proxy:**

- `POST /translate/analyze` (Bearer job_token): cho lượt 0. Đếm **tối đa
  2 lần/job** (1 chính + 1 retry). Body = prompt app build sẵn; server
  forward tới engine admin chọn, ghi `job_usage(phase='analysis')`.
- `POST /translate/batch` (Bearer job_token): cho lượt dịch + vá CJK +
  lượt review. Server cộng dồn `segments_translated` theo job — trần
  **`num_segments × 1.5`** (đệm cho: retry, chia đôi batch, vá CJK, review
  dịch lại câu nghi vấn — trần cũ ×1.15 KHÔNG đủ vì chỉ tính retry). Vượt
  → 403 → app báo "vượt hạn mức đã trả". Ghi
  `job_usage(phase='translate'|'review')` (app gửi kèm header
  `X-Vox-Phase` để server phân loại, không tin để tính tiền — chỉ để
  thống kê).
- Server forward tới engine admin chọn (`config: translate.engine/model`),
  trả nguyên văn response (app parse như hiện tại).
- `/metadata/generate` tương tự: app gửi prompt, server forward + đếm 1
  lần/job (`phase='metadata'`). Billing bật → `content/generator.py` BỎ
  chuỗi fallback nhiều engine, chỉ 1 đường server.
- Config server có `translate.analysis_enabled` / `translate.review_enabled`
  (mục 3.2) — tắt thì `/translate/analyze` trả 204 và app skip lượt đó
  (app phải chịu được response "skip").

### 9.2 Billing TẮT (free mode — vĩnh viễn, quyết định #7)

Engine + key user như hiện nay (`gemini` hoặc 5 nơi OpenAI-compat:
openrouter/openai/anthropic/deepseek/custom). Không đổi gì.

### 9.3 Checklist dọn engine cũ — **ĐÃ HOÀN THÀNH** ✔

Đã xác minh trên code ngày 2026-08-05:

- [x] `translate_common.py` đã tồn tại, chứa helper dùng chung.
- [x] `translate_<engine_cũ>.py` + test của nó đã xóa; `config.py` không còn
      field cũ; `TRANSLATE_ENGINES` chỉ còn 6 engine hiện tại.
- [x] Settings không còn panel engine cũ (giờ là spec-driven, 6 engine ở
      `settings_fields.py`).
- [x] `.env.example` không còn khối config engine cũ.
- [x] 440 test xanh.

**CÒN SÓT — chuyển thành việc của Giai đoạn 1 (dọn tài liệu, không đụng logic):**

*(Đã hoàn thành 2026-08-05 — không còn việc sót.)*

---

## 10. Nạp Vox — PayOS

### 10.1 Setup (NGOÀI code — trước Giai đoạn 3)

- [ ] Đăng ký merchant payos.vn bằng hộ KD + TK MB (chủ dự án đã có sẵn).
- [ ] Lấy `Client ID` + `API Key` + `Checksum Key` → ENV server (không vào repo).
- [ ] Khai webhook `https://<domain>/topup/webhook` (PayOS đòi HTTPS).
- [ ] Test toàn bộ trên **sandbox PayOS** trước khi dùng key production.

### 10.2 Luồng nạp

```
App:    "Nạp Vox" → chọn gói → POST /topup/create {package_id}
Server: orders{status:pending, expires:15'} → PayOS createPaymentLink
        (orderCode=order_id, amount, description "VOX<order_id>")
        → trả {order_id, qr_data, checkout_url, amount_vnd, vox, expires_at}
App:    dialog QR (render qr_data) + nút "Mở trang thanh toán" (checkout_url,
        dự phòng) + đếm ngược + poll GET /topup/{id} mỗi 3s
User:   quét QR app ngân hàng bất kỳ → chuyển
PayOS → POST /topup/webhook (verify CHECKSUM) 
Server: TRANSACTION + idempotency (key = PayOS transaction reference):
        orders.paid → ledger{topup,+vox} → wallets
App:    poll thấy paid → "✅ Đã cộng 5.500 Vox"
```

### 10.3 Edge case bắt buộc

| Tình huống | Xử lý |
|---|---|
| Webhook đến 2 lần / đua với poll | idempotency theo mã GD PayOS — lần 2 no-op |
| Webhook không đến | cron mỗi phút `getPaymentLinkInformation` cho đơn pending |
| Đơn quá hạn 15' nhưng tiền vào sau | PayOS quản orderCode → `expired` nhận tiền → chuyển `paid` bình thường |
| Sai số tiền (hiếm với QR động) | `mismatched` → admin resolve (cộng theo thực nhận / refund) |
| User đóng dialog rồi tiền mới vào | webhook vẫn cộng; mở app sau thấy số dư + dòng lịch sử |
| Hoàn tiền khiếu nại | admin: PayOS refund API + ledger `admin_adjust` âm + bắt buộc note |

---

## 11. Data model (SQLite, migration đánh số)

```sql
users        (id, email UNIQUE, email_normalized UNIQUE, email_verified,
              phone, phone_normalized,          -- KHÔNG verified (quyết định #8)
              name, google_sub UNIQUE NULL, password_hash NULL,
              marketing_opt_in, source, status, -- active|banned|deleted
              created_at, last_seen_at, deleted_at)
devices      (id, user_id, fingerprint_hash, name, last_seen_at)
sessions     (id, user_id, refresh_token_hash, device_id, expires_at, revoked)

wallets      (user_id PK, balance_vox)          -- derived; CHỈ đổi qua ledger
ledger       (id, user_id, delta_vox, type,     -- topup|consume|refund|grant_consume|admin_adjust
              ref_id, idempotency_key UNIQUE, note, created_at)
grants       (id, user_id, package_id, vox_per_day_cap, vox_used_today,
              day_anchor, granted_at, expires_at, source)
              -- gift: UNIQUE(user_id, source); chống farm thêm:
gift_claims  (phone_normalized UNIQUE, device_fingerprint, user_id, created_at)
              -- + CHECK app-side: 1 fingerprint ≤ 2 claims

quotes       (id, user_id, video_hash, num_segments, vox_cost, breakdown_json,
              want_metadata, want_thumbnail, expires_at, status) -- open|confirmed|expired
jobs         (id, quote_id, user_id, video_hash, vox_paid, status,
              -- paid|completed|failed_refunded|cancelled
              segments_translated, cancel_after_translate,
              created_at, finished_at)
job_usage    (job_id, phase,     -- analysis|translate|review|metadata (mục 9.1)
              engine, model, tokens_in, tokens_out, est_cost_vnd)

orders       (id, user_id, package_id, amount_vnd, vox, provider,  -- 'payos'
              provider_ref, status,   -- pending|paid|expired|mismatched|refunded
              created_at, paid_at)
packages     (id, kind, name, vnd, vox, bonus_pct, vox_per_day_cap,
              duration_days, active)   -- kind: topup|gift
config       (key PK, value, updated_at, updated_by)
audit_log    (id, admin_id, action, payload_json, created_at)
```

Seed `config` khởi điểm:
```
billing_enabled=false        gift_enabled=true       vox_per_vnd=0.1
price.base=30                price.per_segment=1
price.metadata=20            price.thumbnail=15
gift.vox_per_day_cap=1500    gift.duration_days=30
cancel_guard.max_free=3      quote.ttl_minutes=30
job.auto_refund_hours=48     translate.engine=openrouter
translate.model=google/gemini-2.5-flash
translate.segment_cap_factor=1.5          # trần = num_segments × factor (mục 9.1)
translate.analysis_enabled=true           # lượt 0 qua /translate/analyze
translate.review_enabled=true             # lượt rà soát (trong trần chung)
min_app_version=2.1
```

---

## 12. API server đầy đủ

```
public
  GET  /status            (mục 5 — tương thích app cũ tuyệt đối)
  GET  /health

auth
  POST /auth/register     {email, password, name, turnstile_token}
  POST /auth/verify-email {code}
  POST /auth/login | /auth/refresh | /auth/logout
  POST /auth/google       {code, pkce_verifier}
  PUT  /me/profile        {name, phone}   → có thể kích hoạt cấp quà (mục 8.1)
  GET  /me                profile + balance + grants hiệu lực
  DELETE /me              (NĐ13)

billing (Bearer access token)
  POST /billing/quote
  POST /billing/confirm   {quote_id} → {job_token} | 402 {need_topup}
  POST /billing/jobs/:id/complete | /fail {error} | /cancel
  GET  /billing/ledger?page=

topup
  POST /topup/create      {package_id}
  GET  /topup/:id
  POST /topup/webhook     (PayOS — verify checksum)

proxy (Bearer job_token)
  POST /translate/analyze  (lượt 0 — tối đa 2 lần/job; 204 nếu admin tắt)
  POST /translate/batch    (lượt dịch + vá CJK + review — trần ×1.5)
  POST /metadata/generate

admin (GĐ2: X-Admin-Token như server cũ; GĐ5: admin account + TOTP)
  GET/PUT /admin/config
  GET  /admin/users?q=  /admin/users/:id  GET /admin/users/export.csv
  POST /admin/users/:id/adjust {delta_vox, note} | /ban | /unban
  GET  /admin/orders?status=   POST /admin/orders/:id/resolve
  GET  /admin/stats    GET /admin/audit
```

## 13. Admin Dashboard (`/admin-ui/`, SPA tĩnh — đề xuất HTML + htmx + Pico.css)

1. **Tổng quan**: 3 công tắc to + thẻ: user mới, doanh thu, Vox tiêu, jobs,
   **chi phí API/doanh thu 7 ngày** (đỏ khi >50%).
2. **Bảng giá & gói**: sửa hệ số + preview "video 100 câu = ? Vox = ? VND";
   CRUD packages.
3. **Dịch**: engine + model + API key (mask trừ 4 ký tự cuối).
4. **Users**: tìm, ledger từng người, adjust (bắt note), ban, export CSV.
5. **Đối soát**: orders mismatched + resolve.
6. **Audit**: mọi thay đổi — ai/lúc nào/gì.

---

## 14. Rủi ro & biện pháp

| # | Rủi ro | Mức | Biện pháp |
|---|---|---|---|
| 1 | Exe bị mod bỏ check billing | Cao | Giá trị nằm server: dịch/metadata cần job_token → mod không dịch chùa bằng key server. TTS local mod được nhưng chi phí server = 0. Không đầu tư DRM thêm. |
| 2 | Farm bản dịch qua hủy-hoàn-100% | Cao | 3 lớp mục 7.2 (ĐÃ DUYỆT). |
| 3 | Farm quà khi KHÔNG có OTP | **Cao (mới)** | 6 lớp mục 8.2 + đường lui bật OTP sau. Theo dõi tỷ lệ gift/ngày trên dashboard tuần đầu. |
| 4 | Double-spend (2 máy 1 acc) | Cao | `BEGIN IMMEDIATE` + ledger idempotency UNIQUE; test 2 confirm song song. |
| 5 | Webhook giả | Cao | Verify checksum PayOS + đối chiếu amount + idempotency mã GD. |
| 6 | Mất DB | Cao | Backup 6h + WAL + restore drill tháng; ledger rebuild được wallet. |
| 7 | Chi phí API > doanh thu (1 Vox = 10đ biên mỏng) | Trung | job_usage + cảnh báo 50% + admin đổi model/giá tức thì. |
| 8 | Server sập giữa job đã trừ | Trung | job_token cache local chạy nốt + auto-refund 48h + nút "Báo lỗi" GUI. |
| 9 | PayOS/MB bảo trì | Thấp | Billing vẫn chạy bằng số dư; dialog nạp báo bảo trì. |
| 10 | Pháp lý: thuế hộ KD, NĐ13, hóa đơn | Cao khi có doanh thu | Hộ KD có rồi; còn: kê khai thuế theo doanh thu, consent + privacy + delete-account (trong checklist). |
| 11 | Token/key ĐÃ LỘ từ trước | **CHẶN** | Rotate ở GĐ0 — đứng đầu checklist. |
| 12 | App cũ gọi /status | Thấp | Chỉ THÊM trường. |
| 13 | User chuyển 2 lần 1 đơn | Thấp | PayOS quản orderCode; lọt → mismatched admin xử. |
| 14 | Đồng hồ máy user sai | Thấp | App so hạn quote/token bằng thời gian server trả, không dùng clock local. |
| 15 | SĐT nhập bừa làm bẩn data marketing | Trung (mới) | Validate định dạng VN + normalized UNIQUE cho quà; chấp nhận không sạch 100% (đánh đổi đã chốt khi bỏ OTP). |

---

## 15. LỘ TRÌNH + CHECKLIST KHỞI CÔNG (agent sau làm TUẦN TỰ từ đây)

> Tổng ~6–8 tuần. Mỗi giai đoạn kết thúc bằng **Nghiệm thu** — chưa đạt chưa
> sang giai đoạn sau. Khi bắt đầu: tạo task list từ đúng các gạch đầu dòng này.

### Giai đoạn 0 — Dọn nền & pháp lý (2–3 ngày) — LÀM TRƯỚC TIÊN

- [ ] **HỎI CHỦ DỰ ÁN TÊN DOMAIN** (quyết định #9 còn treo) → mua.
- [ ] **Rotate ADMIN_TOKEN** của control server + **2 API key đã lộ**
      (Gemini + OpenRouter trong .env máy dev — cấp key mới, hủy key cũ).
- [ ] Caddy (hoặc nginx+certbot) trên VPS → HTTPS cho server hiện tại.
- [ ] App: đổi `_DEFAULT_URL_B64` trong `autodub_gui/remote_gate.py` sang
      `https://<domain>/status` (base64) → build exe mới.
- [ ] Server cũ: `/status` thêm `billing:{enabled:false, min_app_version,
      welcome_gift_enabled}` — deploy, xác nhận app cũ + mới đọc ổn.
- [ ] Đăng ký PayOS merchant (hộ KD + MB) → 3 key, để chế độ sandbox.
- [ ] Soạn nháp privacy policy + consent + điều khoản (tiếng Việt).
- [ ] **Nghiệm thu**: HTTPS xanh; app cũ ngoài kia chạy bình thường; key cũ
      đã vô hiệu; curl /status thấy object billing.

### Giai đoạn 1 — Server nền tảng + Auth (1.5 tuần)

Server (repo mới `voxdub-server/` hoặc mở rộng `control_server/` — đề xuất
repo/thư mục mới, giữ server cũ chạy tới khi cutover):
- [ ] Fastify + better-sqlite3 + migrations đánh số + seed config (mục 11).
- [ ] Port rate-limit + timing-safe token + atomic write từ `server.js` cũ.
- [ ] users/devices/sessions/wallets/ledger/config/audit_log.
- [ ] /auth/register + verify-email (SMTP Brevo) + Turnstile.
- [ ] /auth/login|refresh(rotate)|logout, /me, PUT /me/profile, DELETE /me.
- [ ] Google OAuth PKCE loopback.
- [ ] Cron backup 6h + script restore + chạy restore thử 1 lần.
- [ ] systemd unit mới (cạnh unit cũ) + Caddy route.
App:
- [ ] `autodub_gui/auth_client.py` (requests + keyring) + `account_dialog.py`
      (đăng nhập/đăng ký/Google/hồ sơ tên+SĐT).
- [ ] Sidebar hiện tên khi đăng nhập (billing tắt — login tùy chọn).
- [ ] Bump `autodub_gui/__init__.py::__version__` → `2.1.0` (khớp
      `min_app_version=2.1` trong seed config) — bump ở release nào có
      auth_client cũng được, miễn TRƯỚC khi bật billing.
- [ ] **Nghiệm thu**: đăng ký/verify email/login/Google chạy thật qua HTTPS;
      delete account hoạt động; pytest xanh (440+); free mode hành vi không
      đổi (chạy 1 video thật end-to-end).

### Giai đoạn 2 — Credit lõi (2 tuần) — TRỌNG TÂM

Server:
- [ ] quotes/jobs/job_usage/packages/grants/gift_claims.
- [ ] /billing/quote (giá từ config) · /billing/confirm (transaction,
      idempotency, thứ tự quà→nạp, trần ngày grant).
- [ ] /billing/jobs complete|fail|cancel + guard 7.2.3.
- [ ] Cron: auto-fail-refund 48h; đối soát đêm ledger↔wallet; reset
      vox_used_today theo giờ VN.
- [ ] **Test tự động bắt buộc**: 2 confirm song song (1 ok 1 idempotent);
      confirm thiếu 1 Vox → 402; fail → refund đúng; cancel → refund + đếm
      cancel_after_translate; guard kích hoạt đúng lần thứ 4.
App:
- [ ] `billing_client.py` (quote/confirm/cancel; cache `data/job_token.json`).
- [ ] `pipeline.py`: nhánh `status="quote_pending"` sau ASR khi billing bật
      (đặt ngay sau chỗ ghi `transcript_original.json` + trước `_auto_translate`;
      resume đọc job_token từ work_dir); gọi complete/fail/cancel đúng chỗ
      (fail = except nhánh lỗi hệ thống; cancel = PipelineCancelled do user).
- [ ] `pages/new_project_page.py`: banner quote theo khuôn `pending_banner`
      (dòng ~126) + nút Nạp — chuỗi hiển thị dùng `status_text.py`, KHÔNG emoji.
- [ ] Hủy → xóa file theo 7.2.1 + ghi `data/job_status.json`;
      `editor.py::rebuild_output` + `pages/editor_page.py` check job_status
      (lớp 7.2.2).
- [ ] Sidebar số dư khi billing bật.
Admin:
- [ ] Dashboard v1: 3 công tắc + bảng giá + users + adjust + audit.
- [ ] **Nghiệm thu** (server test, billing bật): quote→confirm→dịch→complete;
      hủy→hoàn→chạy lại→quote mới; 2 máy 1 acc; tắt billing giữa chừng job
      chạy nốt; bật lại số dư còn nguyên.

### Giai đoạn 3 — PayOS (1 tuần)

- [ ] orders + seed 4 gói topup.
- [ ] /topup/create (SDK, sandbox) · /topup/webhook (checksum + idempotency)
      · cron poll pending.
- [ ] App `topup_dialog.py`: gói → QR + checkout_url + poll + done.
- [ ] Admin trang đối soát.
- [ ] Chạy đủ bảng edge case 10.3 trên sandbox → đổi key production.
- [ ] **Nghiệm thu**: nạp THẬT 20.000đ từ TK MB cá nhân → Vox vào đúng;
      webhook bắn lại không nhân đôi; đơn quá hạn trả muộn vẫn cộng.

### Giai đoạn 4 — Dịch qua server + Quà + Batch (1–1.5 tuần)

- [ ] `/translate/analyze` (≤2 lần/job) + `/translate/batch` (trần
      `num_segments × translate.segment_cap_factor`) + `/metadata/generate`
      — ghi `job_usage` theo phase (mục 9.1).
- [ ] App: engine `"server"` / `translate_server.py`; billing bật →
      `_auto_translate` ép cả 3 lượt (analysis/translate/review) qua server;
      app chịu được 204 từ /translate/analyze (skip lượt 0);
      `content/generator.py` bỏ fallback khi billing bật;
      `pages/settings_page.py` ẩn nhóm engine tab "Kết nối" + hiện
      "Dịch do máy chủ VoxDub đảm nhận [OK]" (ẩn runtime, không xóa spec —
      giữ `test_settings_fields.py` xanh).
- [ ] Cấp quà theo mục 8.1 + đủ 6 lớp 8.2 (email normalize, phone normalize
      UNIQUE, fingerprint ≤2, Turnstile, trần ngày, công tắc).
- [ ] Batch: cột Vox + tổng + confirm tuần tự + thiếu Vox giữa chừng.
- [ ] Metadata/thumbnail cộng phí đúng breakdown.
- [ ] **Nghiệm thu**: video dịch không có key nào phía user; user mới nhận
      quà đúng luồng, chạm trần bị chặn lịch sự; tắt gift người cũ vẫn dùng;
      farm thử 3 acc trên 1 máy → acc thứ 3 không nhận quà; batch 5 video
      hết Vox ở video 4 xử lý đúng.

### Giai đoạn 5 — Go-live (1 tuần + theo dõi)

- [ ] Admin account + TOTP thay X-Admin-Token cho dashboard.
- [ ] Trang privacy + consent trong app + link.
- [ ] Beta kín: bật billing qua allowlist user_id (config) cho 5–10 user quen.
- [ ] Theo dõi 1 tuần: chi phí/doanh thu, refund, khiếu nại nạp, tỷ lệ nhận quà.
- [ ] Chỉnh giá nếu biên < 50%.
- [ ] Thông báo trước 7 ngày qua `message` của /status → bật billing toàn bộ.
- [ ] Thiết lập kê khai thuế hộ KD theo doanh thu thật.
- [ ] **Nghiệm thu cuối**: 2 tuần vận hành không sai một giao dịch (đối soát
      đêm sạch); restore drill pass.

---

## 15B. BỔ SUNG 2026-08-04 — CÁC HẠNG MỤC UI HOÃN TỪ ĐỢT ĐẠI TU GIAO DIỆN

> Nguồn: `docs/KE_HOACH_UI_VOXDUB.md` §3.2. Đợt đại tu giao diện (bám ảnh tham
> chiếu VoxDub Studio) đã **cố ý KHÔNG làm** các hạng mục dưới đây vì chúng
> thuộc phạm vi credit/tài khoản. Khi kế hoạch credit khởi công, làm chúng
> **cùng giai đoạn tương ứng** đã ghi ở cột cuối.

### 15B.1 Phần tử giao diện có trong ảnh nhưng đã hoãn

| # | Phần tử trong ảnh tham chiếu | Đợt UI đã làm gì thay thế | Làm ở giai đoạn nào |
|---|---|---|---|
| 1 | Header: avatar + tên **"Dylan"** | Avatar chữ cái đầu từ `.env` key `DISPLAY_NAME` (mặc định `os.getlogin()`); click → mở Cài đặt | **GĐ1** — thay bằng tên tài khoản thật sau đăng nhập |
| 2 | Header: badge **"Pro"** cạnh avatar | **Bỏ hẳn** (chưa có gói) | **GĐ2** — hiện badge theo gói đang dùng |
| 3 | Sidebar: card **"Pro Plan · Còn 27 ngày · [Nâng cấp ngay →]"** | Thay bằng **`SystemStatusCard`** (Giọng đọc / Dịch / FFmpeg + nút "Kiểm tra lại"), **giữ nguyên vị trí và kích thước** để hoán đổi không lệch bố cục | **GĐ2** — đổi thành `👤 Tên · N Vox` + nút "Nạp Vox" (kế hoạch mục 7.4) |
| 4 | Header: **chuông thông báo** | "Trung tâm hoạt động" local: 20 sự kiện gần nhất từ `autodub_gui/run_state.py` (chạy xong / lỗi / lưu cài đặt), chấm đỏ chưa đọc | **GĐ2–4** — trộn thêm thông báo server (nạp thành công, sắp hết Vox, quà hết hạn) |
| 5 | Màn "Tạo dự án": chưa có bước xác nhận chi phí | Stepper 6 bước thuần cấu hình | **GĐ2** — chèn banner quote sau ASR, làm y khuôn `pending_banner` (mục 7.1) |
| 6 | Màn "Xử lý hàng loạt": bảng 4 cột (Video/Trạng thái/Tiến trình/Thao tác) | Đã dựng đủ 4 cột | **GĐ4** — thêm **cột "Vox"** + dòng tổng "Tổng tạm tính: N Vox — Số dư: M" + confirm tuần tự (mục 7.3) |
| 7 | Màn "Cài đặt" tab **API** | Nay là tab **"Kết nối"** với panel Gemini + 5 nơi OpenAI-compat | **GĐ4** khi billing bật thì ẩn nhóm engine (runtime), hiện dòng "Dịch do máy chủ VoxDub đảm nhận" |
| 8 | Dialog nạp tiền / QR | Không có | **GĐ3** — `topup_dialog.py` (mục 10.2) |

### 15B.2 Ràng buộc mà đợt UI đã cam kết bảo tồn cho kế hoạch này

Agent làm kế hoạch credit **dựa vào được** các điểm sau (đợt UI có test/checklist bảo vệ):

- `pending_banner` (banner `translate_pending` + nút "Đã dịch xong, tiếp tục")
  **được giữ nguyên khuôn** trong `autodub_gui/pages/new_project_page.py` →
  banner quote copy y khuôn (mục 7.1).
- `autodub/pipeline.py`, `config.py`, `batch.py`, `workdir.py`, `progress.py`
  **không bị sửa** trong đợt UI (chỉ `autodub/editor.py` được thêm 5 hàm chỉnh
  segment) → điểm chèn `status="quote_pending"` ở mục 7.1 vẫn đúng như mô tả.
- `autodub_gui/remote_gate.py` **không bị đụng** → việc đổi `_DEFAULT_URL_B64`
  và mở rộng parse payload (mục 5, 6) làm được ngay.
- `autodub_gui/run_state.py::REGISTRY` (mới) là chỗ **duy nhất** biết job đang
  chạy → `billing_client` gắn vào đây để cập nhật số dư sau `confirm/complete/cancel`.
- `autodub_gui/tokens.py` là nguồn màu duy nhất → mọi UI credit dùng token này,
  **không tự đặt màu mới**.
- Có sẵn `ui/toast.py`, `ui/modal.py::ConfirmDialog`, `ui/table.py::DataTable`,
  `ui/empty.py` → UI credit tái dùng, không dựng lại.

### 15B.3 Việc PHẢI làm thêm ở kế hoạch credit do đợt UI sinh ra

> Cập nhật 2026-08-05: các mục đổi-đường-dẫn của bản cũ đã được viết thẳng
> vào mục 1.2 (bảng file). Còn hiệu lực:

- [ ] Trang Cài đặt giờ có **6 tab** (`Cơ bản / Giọng đọc / Phụ đề / Hiệu
      suất / Kết nối / Nâng cao`) và là **spec-driven**
      (`settings_fields.py`) — mọi thay đổi field phải qua spec, và
      `tests/test_settings_fields.py` ép spec ↔ `.env.example` đồng bộ.
- [ ] `tests/test_ui_tokens.py` sẽ **fail nếu UI credit hardcode mã hex** —
      dùng `tokens.py`.
- [ ] `tests/test_gui_no_emoji.py` vẫn hiệu lực — UI credit **không dùng
      emoji** (kể cả các ký hiệu quà/tiền/tích trong chuỗi thông báo ở mục
      7.1 / 8.1 / 10.2 của file này — chúng chỉ là minh họa; code thật dùng
      hằng trong `autodub_gui/status_text.py`, ví dụ `[OK]`).

---

## 15C. RÀ SOÁT LẠI 2026-08-05 — BẢN 3.0 → 3.1 (những gì đã đổi và VÌ SAO)

> Agent sau: mục này là "nhật ký lệch pha" giữa kế hoạch và code. Các mục
> thân bài Ở TRÊN đã được sửa cho khớp — mục này chỉ để hiểu lịch sử, không
> chứa việc phải làm (trừ khi repo lại đổi tiếp).

| # | Bản 3.0 viết | Thực tế code 2026-08-05 | Đã sửa ở |
|---|---|---|---|
| 1 | Xóa engine cũ là việc của GĐ1 (checklist 9.3) | **ĐÃ XONG hoàn toàn**: `translate_common.py` tồn tại, engine cũ đã xóa, config/settings/env sạch, tài liệu/comments đã dọn hết. | 9.3 đánh dấu ✔, GĐ1 hoàn thành |
| 2 | 2 engine dịch (Gemini/OpenRouter) + `translate_openrouter.py` | **6 engine**: `translate_openai.py` đa nhà cung cấp (openrouter/openai/anthropic/deepseek/custom) + `translate_gemini.py`; default `openrouter` | 1.1, 1.2, 9.1, 9.2 |
| 3 | Dịch 1 lượt, trần đếm segment ×1.15 | **Dịch 3 lượt** (analysis → translate + vá CJK → review, xem `_auto_translate`) → ×1.15 chặn nhầm lượt review; chi phí/video cao hơn ước tính cũ | 1.1, 3.2, 9.1 (trần ×1.5 + `/translate/analyze` + job_usage.phase), 11, 12 |
| 4 | `dub_tab.py` / `batch_tab.py` / `settings_tab.py` | `pages/new_project_page.py` / `pages/batch_page.py` / `pages/settings_page.py` (spec-driven, 6 tab, tab engine tên "Kết nối") | 1.2, GĐ2, GĐ4, 15B |
| 5 | TTS nhiều engine | Chỉ còn VieNeu | 1.1 |
| 6 | 249 test | **440 test** xanh | 1.2 |
| 7 | `min_app_version=2.1` (không nói bump ở đâu) | App đang `2.0.0` → thêm việc bump version vào GĐ1 | GĐ1 |
| 8 | — | `content/generator.py` giờ có chuỗi fallback nhiều engine → billing bật phải BỎ fallback | 1.2, 9.1, GĐ4 |
| 9 | — | Xác nhận các điểm neo VẪN ĐÚNG: `remote_gate.py` chưa đổi (IP trần), điểm chèn `quote_pending` sau `transcript_original.json`, `pending_banner` ở `new_project_page.py:126`, `original_audio.wav` trong `data/`, cấm emoji + cấm hex ngoài `tokens.py` | — |

---

## PHỤ LỤC A — TÀI NGUYÊN CẦN CHUẨN BỊ TRƯỚC KHỞI CÔNG (chủ dự án tự điền)

> Điền vào các ô `______` bên dưới TRƯỚC khi ra lệnh khởi công. Agent triển
> khai đọc phụ lục này để lấy thông số thật; ô nào còn trống thuộc GĐ0 thì
> GĐ0 phải dừng lại hỏi. **TUYỆT ĐỐI không dán API key/secret thật vào file
> này nếu repo sẽ được chia sẻ** — chỉ ghi "đã có, nằm ở ______" (trình quản
> lý mật khẩu, file trên VPS...).

### A.1 Hạ tầng — domain, VPS, TLS (cần cho GĐ0)

- [ ] **Domain** (quyết định #9): `______` — mua tại: `______`
- [ ] DNS: A record `api.<domain>` (hoặc domain gốc) → IP VPS bên dưới
- [ ] **VPS hiện tại**: IP `14.225.192.101` — SSH user: `______`, port: `______`,
      cách đăng nhập (key/password, lưu ở đâu): `______`
- [ ] Cấu hình VPS (RAM/CPU/disk): `______` (server billing + SQLite cần
      ≥1GB RAM; hiện chạy control server Node zero-dependency)
- [ ] Mở port 80 + 443 (firewall/nhà cung cấp VPS) — Caddy tự lo Let's
      Encrypt khi domain đã trỏ đúng
- [ ] Node.js ≥ 20 trên VPS: có chưa? `______`

### A.2 Thanh toán — PayOS (cần trước GĐ3)

- [ ] Tài khoản merchant **payos.vn** đăng ký bằng hộ KD + TK MB (đã có sẵn
      hộ KD — quyết định #6). Trạng thái: `______`
- [ ] 3 key PayOS (Client ID / API Key / Checksum Key): đã có, lưu ở `______`
      (KHÔNG dán vào đây — sẽ đặt vào ENV server)
- [ ] Webhook sẽ khai trên PayOS: `https://<domain>/topup/webhook` (đòi HTTPS)
- [ ] Key **sandbox** riêng để test GĐ3 trước khi đổi production

### A.3 Email verify + chống bot (cần cho GĐ1)

- [ ] **Brevo** (hoặc SMTP tương đương, free 300 mail/ngày): tài khoản
      `______`, SMTP key lưu ở `______`
- [ ] Địa chỉ gửi: `no-reply@<domain>` — cấu hình **SPF + DKIM** trên DNS
      theo hướng dẫn Brevo (không làm → mail vào spam)
- [ ] **Cloudflare Turnstile** (free): Site Key `______`, Secret Key lưu ở
      `______`

### A.4 Google OAuth (cần cho GĐ1)

- [ ] Google Cloud project: `______`
- [ ] OAuth Client loại **Desktop app** (PKCE + loopback
      `http://127.0.0.1:<port>/callback` — KHÔNG cần client secret bảo mật):
      Client ID `______`
- [ ] OAuth consent screen: tên app "VoxDub Studio", logo, link privacy
      policy `https://<domain>/privacy` — trạng thái duyệt: `______`

### A.5 API key dịch PHÍA SERVER (cần cho GĐ4 — KHÁC key cá nhân!)

> Đây là key server trả tiền thay user khi billing bật — tách hẳn khỏi key
> cá nhân trong `.env` máy dev (2 key cá nhân đó ĐÃ LỘ, phải rotate ở GĐ0).

- [ ] OpenRouter key MỚI (nạp sẵn credit, chỉ dùng cho server): lưu ở `______`
- [ ] Gemini key MỚI (dự phòng + thumbnail nếu dùng Gemini image): lưu ở `______`
- [ ] Đặt hạn mức chi tiêu (spend limit) trên cả 2 tài khoản: `______` USD/tháng

### A.6 Bí mật server tự sinh (GĐ0/GĐ1 — sinh bằng `openssl rand -hex 32`)

- [ ] `ADMIN_TOKEN` mới (thay token đã lộ) — lưu ở `______`
- [ ] `JWT_SECRET` (access/refresh token) — lưu ở `______`
- [ ] `JOB_TOKEN_SECRET` (ký job_token) — lưu ở `______`
- Tất cả đặt qua ENV/systemd `Environment=`, KHÔNG commit vào repo nào.

### A.7 Backup & giám sát (cần cho GĐ1)

- [ ] Đích backup SQLite mỗi 6h (chọn 1: object storage S3-compatible /
      VPS thứ 2 / rsync về máy nhà): `______`
- [ ] Kênh nhận cảnh báo đối soát đêm + biên >50% (email/Telegram bot):
      `______`

### A.8 Pháp lý (GĐ0 soạn nháp, GĐ5 hoàn thiện)

- [ ] Privacy policy + điều khoản tiếng Việt (NĐ13/2023) — host tại
      `https://<domain>/privacy` — người soạn/duyệt: `______`
- [ ] Thông tin hộ KD dùng cho kê khai thuế theo doanh thu: `______`
- [ ] Người phụ trách xử lý yêu cầu xóa tài khoản/khiếu nại: `______`

---

## 16. Ghi chú cho agent triển khai

- Đọc thêm memory dự án: `overhaul-2026-08-translate-context.md` (bố cục
  workdir, các thay đổi gần nhất), `pre-release-audit-2026-08.md` (key lộ),
  `remote-kill-switch.md`, `vox-credit-plan.md` (con trỏ về file này).
- Chạy test: `py -m pytest tests/ -q` (Windows, Python 3.14). Build exe:
  `py scripts/build_exe.py`.
- Mọi giá trị tiền trong file này là **seed của bảng `config`** — code không
  hardcode số nào.
- Khi có mâu thuẫn giữa file này và code thực tế (repo có thể đã thay đổi
  sau 2026-08-05 — lần rà soát gần nhất): **khảo sát lại code trước, cập
  nhật file này VÀ ghi thêm dòng vào bảng 15C, rồi mới làm** — file này là
  hợp đồng sống.
- **Trước khi khởi công: đọc Phụ lục A** — ô nào cần cho giai đoạn sắp làm
  mà còn trống thì hỏi chủ dự án, đừng tự bịa.
- Chưa khởi công khi chủ dự án chưa ra lệnh. Khi ra lệnh: bắt đầu từ Giai
  đoạn 0, việc đầu tiên là hỏi tên domain (quyết định #9 / Phụ lục A.1).
