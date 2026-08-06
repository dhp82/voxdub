# VoxDub Studio — Lồng tiếng video sang tiếng Việt tự động

Ứng dụng desktop (Windows) tự động lồng tiếng video **YouTube / TikTok / Douyin / Bilibili / file trên máy** sang **tiếng Việt**, giữ nguyên nhạc nền và hiệu ứng âm thanh gốc. Kèm phụ đề, che mờ chữ Trung trên hình, và trình chỉnh sửa từng câu.

**Miễn phí 100%** với cấu hình mặc định: nhận dạng giọng nói chạy trên máy (Whisper), giọng đọc tiếng Việt chạy trên máy (VieNeu — hàng chục giọng nam/nữ ba miền, thêm được giọng riêng từ đoạn ghi âm), dịch bằng AI qua API key miễn phí (OpenRouter/Gemini).

```
Link/File video → Tải về → Tách âm thanh → Tách nhạc nền
                → Nghe lời thoại gốc (Whisper)
                → Dịch sang tiếng Việt (AI)
                → Tạo giọng đọc (VieNeu ~1s/câu trên CPU)
                → Khớp thời gian → Trộn với nhạc nền
                → Phụ đề + che chữ gốc → Xuất video hoàn chỉnh
```

---

## 1. Cài đặt (làm 1 lần)

### Bước 1 — Cài Python 3.10+

Tải tại <https://www.python.org/downloads/> — khi cài **nhớ tick "Add Python to PATH"**.

### Bước 2 — Cài ffmpeg (bản "full")

1. Tải bản **full** tại <https://www.gyan.dev/ffmpeg/builds/> (file `ffmpeg-release-full.7z`)
2. Giải nén, chép thư mục `bin` vào PATH (hoặc chép `ffmpeg.exe` + `ffprobe.exe` vào `C:\Windows`)
3. Kiểm tra: mở Command Prompt gõ `ffmpeg -version` — hiện version là được

> Cần bản **full** vì có libass để ghi phụ đề vào video. Bản "essentials" cũng có, nhưng full chắc chắn nhất.

### Bước 3 — Cài ứng dụng

Mở Command Prompt trong thư mục dự án:

```bash
pip install -r requirements.txt
playwright install chromium      # chỉ cần nếu tải video Douyin
copy .env.example .env           # tạo file cấu hình (chỉnh sau trong Tab Cài đặt)
```

### Bước 4 — Chạy

```bash
python -m autodub_gui
```

Vậy là xong. Mọi cài đặt còn lại (API dịch, giọng đọc, phụ đề…) đều chỉnh trong **Tab Cài đặt** của app — không cần sửa file gì.

---

## 2. Cài AI dịch (API key miễn phí — 2 phút)

Bước dịch cần một AI. Cách nhanh và nhẹ máy nhất là dùng API key miễn phí — không cài thêm gì, không tốn RAM/dung lượng:

1. Lấy key **miễn phí** ở một trong hai nơi:
   - **OpenRouter**: <https://openrouter.ai/keys> (khuyến nghị — nhiều model miễn phí)
   - **Google Gemini**: <https://aistudio.google.com/apikey>
2. Mở **Tab Cài đặt → thẻ Kết nối**, chọn nơi dịch tương ứng, dán key, bấm **Lưu cài đặt**

Từ giờ pipeline tự dịch, chạy một mạch từ link đến video hoàn chỉnh — không cần thao tác gì thêm.

> App cũng hỗ trợ OpenAI / Anthropic / DeepSeek / máy chủ tự chọn — cùng chỗ trong thẻ Kết nối.

<details>
<summary><b>Nếu không muốn dùng API key — dịch tay hoàn toàn</b></summary>

Bỏ tick **"Bật dịch tự động"** trong Tab Cài đặt. Khi chạy tới bước dịch, app sẽ dừng và hiện hướng dẫn:

1. Mở file `TRANSLATE_PENDING.txt` trong thư mục kết quả (có nút mở sẵn trong app)
2. Copy đoạn prompt trong đó, dán vào ChatGPT/Gemini web, dán thêm nội dung `transcript_original.json`, gửi
3. Lưu kết quả AI trả về thành `transcript_vi.json` cùng thư mục
4. Về app bấm **"Đã dịch xong, tiếp tục"**

</details>

---

## 3. Hướng dẫn dùng từng Tab

### Tab Lồng tiếng — làm 1 video

1. Dán **link video** (hoặc chọn **file trên máy**)
2. Chọn **ngôn ngữ gốc** của video (tiếng Trung, tiếng Anh…)
3. Chọn **giọng đọc** nam/nữ
4. Tùy chọn:
   - **Nhạc nền**: `Demucs` (tách giọng khỏi nhạc, chất lượng cao — mặc định) / `Duck` (giảm nhỏ audio gốc, nhanh) / tắt
   - **Phụ đề**: không / phụ đề rời (bật tắt được trong trình phát) / **ghi vào video** (luôn hiện, đăng TikTok tốt)
   - **Phụ đề & che chữ…**: mở khung xem trước trên chính khung hình video — **kéo thả dòng phụ đề** để đặt vị trí, chỉnh cỡ chữ/font/màu/viền thấy ngay kết quả, và **kéo chuột khoanh vùng** chữ Trung cần làm mờ. Một nút, chỉnh hết.
5. Bấm **Bắt đầu lồng tiếng** — theo dõi tiến trình bên phải
6. Xong: mở video, mở thư mục, hoặc bấm **"Chỉnh sửa từng câu"** để tinh chỉnh

> **Resume**: nếu chạy dở (mất mạng, tắt app…), chọn *"Resume thư mục đã chạy dở"* và trỏ vào thư mục kết quả — mọi bước đã xong đều được dùng lại, không tốn thời gian chạy lại.

### Tab Batch — làm nhiều video một lượt

Dán link vào ô, **mỗi dòng một video**:

```
https://youtu.be/abc123
https://youtu.be/def456 | nữ
https://www.douyin.com/video/789 | nam
# dòng bắt đầu bằng # là ghi chú, được bỏ qua
```

- Giọng đọc, ngôn ngữ gốc… chọn một lần cho cả loạt; muốn video nào giọng khác thì thêm `| nam` hoặc `| nữ` cuối dòng
- **Tiến độ tự lưu** (`batch_state.json`): tắt app mở lại, dán lại danh sách cũ — video đã xong tự bỏ qua, chỉ chạy phần còn thiếu
- Tick *"Chạy lại cả video đã xong"* nếu muốn làm lại từ đầu

### Tab Download — chỉ tải video, không lồng tiếng

Dán nhiều link, bấm tải. Hỗ trợ cookies trình duyệt cho video cần đăng nhập.

### Tab Chỉnh sửa — sửa từng câu sau khi lồng

1. **Bước 1**: mở thư mục kết quả (từ Tab Lồng tiếng bấm "Chỉnh sửa từng câu" là vào thẳng)
2. **Bước 2**: bảng liệt kê từng câu — bản gốc và bản dịch cạnh nhau
   - Bấm **▶** để nghe giọng đọc của câu đó
   - **Nhấp đôi** vào ô "Bản dịch" để sửa; sửa thoải mái nhiều câu
   - Bấm **"Lưu tất cả + đọc lại"** một lần — app lưu mọi câu đã sửa và chỉ đọc lại đúng những câu đó (nhanh)
   - Bấm **"Hoàn tác"** nếu muốn bỏ thay đổi chưa lưu
3. **Bước 3**: bấm **"Xuất lại video"** — trộn audio + phụ đề + che chữ, ra video mới. Nút **"Phụ đề & che chữ…"** ngay cạnh cho phép đổi kiểu chữ/vị trí/vùng che trước khi xuất.

### Tab Cài đặt

Mọi cấu hình một chỗ, lưu là áp dụng ngay:

| Thẻ | Nội dung |
|---|---|
| Cơ bản | Model Whisper (`medium` cân bằng; `large-v3` chính xác nhất nhưng chậm), thư mục xuất, ngôn ngữ gốc mặc định |
| Giọng đọc | Thư viện giọng VieNeu: lọc theo giới tính/vùng miền/phong cách, **Nghe thử**, thêm giọng riêng từ đoạn ghi âm |
| Phụ đề | Chỉnh trực quan bằng nút **"Phụ đề & che chữ…"** ở bước Lồng tiếng/Chỉnh sửa; lựa chọn lưu tự động |
| Hiệu suất | Số luồng xử lý song song, chất lượng tách nhạc nền |
| Kết nối | Nơi dịch (OpenRouter/Gemini/OpenAI/Anthropic/DeepSeek/tự chọn) + API key, Google Gemini (tiêu đề/mô tả, tuỳ chọn) |
| Nâng cao | Hệ số chậm giọng, tốc độ đọc tối đa, căn thời gian |

---

## 4. Kết quả nằm ở đâu?

Mỗi lần chạy tạo một thư mục `output/VN/<thời-gian>_vi/`:

```
├── dubbed_video.mp4                ← VIDEO HOÀN CHỈNH (cái bạn cần)
├── transcript_vi.json/.srt         ← bản dịch + file phụ đề rời
├── transcript_original.json/.srt   ← văn bản nhận dạng từ video gốc
├── audio_vi_full.wav               ← audio lồng tiếng hoàn chỉnh
├── segments/…                      ← giọng đọc từng câu (cache)
├── timing_guide.json               ← câu nào lệch thời gian cần chỉnh tay
├── thumbnail_prompts.txt           ← prompt tạo thumbnail (dán vào AI ảnh)
└── youtube_metadata.json           ← tiêu đề/mô tả/hashtag (nếu có key Gemini)
```

Mọi bước đều **cache theo file** — xoá file nào thì bước đó chạy lại, còn lại giữ nguyên.

---

## 5. Câu hỏi thường gặp

**Chạy lần đầu rất lâu?** — Lần đầu Whisper và Demucs phải tải model về (vài GB, một lần duy nhất). Các lần sau nhanh hơn nhiều.

**Máy không có GPU có chạy được không?** — Được: Whisper chạy CPU tốt, dịch qua API không tốn tài nguyên máy, giọng đọc VieNeu được thiết kế cho CPU (~1 giây/câu). Có GPU thì Whisper và Demucs tự dùng, nhanh hơn.

**Phụ đề burn bị lỗi "No such filter: subtitles"?** — ffmpeg của bạn thiếu libass. Cài bản **full** từ gyan.dev (bước 1.2), hoặc tạm dùng phụ đề chế độ "rời".

**Giọng đọc bị nhanh/chậm so với hình?** — App tự nén giọng tối đa 1.5x cho khớp. Câu nào vẫn lệch sẽ liệt kê trong `timing_guide.json`; vào Tab Chỉnh sửa viết lại câu đó ngắn gọn hơn rồi xuất lại.

**Dịch xong thấy câu nào chưa ưng?** — Tab Chỉnh sửa → sửa → "Lưu tất cả + đọc lại" → "Xuất lại video". Chỉ mất vài giây mỗi câu.

**Video Douyin không tải được?** — Chạy `playwright install chromium` và thử lại; một số video cần cookies đăng nhập (Tab Download có hỗ trợ).

---

## 5.5. VieNeu — giọng đọc tiếng Việt chạy local (miễn phí)

VieNeu là model TTS tiếng Việt chạy ngay trên CPU (~1 giây/câu), không cần GPU. Cài một lần:

```bash
py scripts/setup_vieneu.py
```

Lệnh trên tự làm hết: tạo môi trường riêng `.venv-vieneu` (không đụng môi trường chính), tải model và bộ giọng có sẵn, chạy thử. Sau đó chọn giọng trong **Tab Cài đặt → thẻ Giọng đọc** (có nút **Nghe thử** cho từng giọng).

**Dùng giọng của riêng bạn:** vào thẻ Giọng đọc → **Thêm giọng từ đoạn ghi âm** — chọn một file WAV 5–10 giây (rõ, không nhạc nền) và nhập nội dung câu nói; app tự học giọng và thêm vào thư viện.

**Lưu ý:** không dùng để giả mạo giọng người khác.

---

## 6. Tính năng đầy đủ

- Tải video: Douyin (Playwright), TikTok, YouTube, Bilibili, 1000+ trang qua yt-dlp
- Nhận dạng giọng nói: **Whisper chạy local** — miễn phí, không cần key (tiếng Trung, Anh…); tự dùng GPU khi có
- Dịch tự động qua **API key miễn phí** (OpenRouter/Gemini, thêm OpenAI/Anthropic/DeepSeek/tự chọn); tự fallback về dịch tay khi chưa có key
- Giọng đọc tiếng Việt: **VieNeu chạy local trên CPU** (miễn phí, ~1s/câu) — hàng chục giọng ba miền, thêm được giọng riêng từ đoạn ghi âm
- Giữ nhạc nền: Demucs tách giọng/nhạc (chạy GPU, song song với nhận dạng giọng nói), hoặc duck âm lượng gốc
- Tự khớp thời gian từng câu với video (atempo tối đa 1.5x)
- **Phụ đề** rời hoặc ghi thẳng vào video — tùy chỉnh vị trí, font, cỡ chữ, lề, viền, màu
- **Che mờ chữ Trung** trên hình — khoanh vùng bằng chuột
- **Chỉnh sửa từng câu**: nghe, sửa, đọc lại hàng loạt, xuất lại video — tái dùng cache
- **Batch** nhiều video, mỗi dòng một link, tiến độ lưu tự động, resume an toàn
- Resume mọi lần chạy dở — không bước nào phải làm lại
- Sinh tiêu đề/mô tả/hashtag YouTube + prompt thumbnail (Gemini, tuỳ chọn)

## Kiến trúc (cho dev)

```
autodub/                 # core pipeline (không phụ thuộc GUI)
├── pipeline.py          # DubPipeline — chạy đủ 8 bước, cache theo file
├── editor.py            # sửa câu / đọc lại / xuất lại (Segment Editor)
├── batch.py             # batch từ danh sách dòng, state crash-safe
├── config.py            # Settings từ .env, validate lười (ConfigError)
├── languages.py         # đích VI + map ngôn ngữ nguồn
├── progress.py          # ProgressEvent callback + cancel (threading.Event)
├── media/               # download, audio, video, subtitle/blur, Demucs
├── speech/              # Whisper/Paraformer ASR + TTS VieNeu
├── text/                # SRT, dịch (OpenRouter/Gemini/OpenAI…), TRANSLATE_PENDING.txt
└── content/             # metadata YouTube + thumbnail prompts (Gemini)

autodub_gui/             # GUI PySide6 (desktop, dark theme)
├── app.py               # MainWindow — sidebar + các trang
├── shell.py             # khung sidebar/header
├── workers.py           # QThread: dub / batch / download / lưu-đọc-lại / xuất-lại
├── pages/               # từng trang: home, new_project, batch, download, editor, projects, settings, help
├── ui/                  # widget dùng chung: buttons, inputs, cards, modal, toast…
├── video/               # trình phát video + timeline
├── voice_picker.py      # ô chọn giọng đọc (dùng ở pipeline + cài đặt)
├── style_dialog.py      # kiểu phụ đề + khoanh vùng che chữ trên frame
├── widgets.py           # StepTracker, LogPanel, Banner
├── theme.py             # QSS dark theme
└── tokens.py            # design tokens (file duy nhất chứa mã màu)
```

Chạy test: `python -m pytest -q`

## License

MIT.
