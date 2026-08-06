# VoxDub Studio — Auto Video Dubbing (Vietnamese)

Desktop app (Windows) tự động lồng tiếng video sang tiếng Việt, giữ nguyên nhạc nền và hiệu ứng gốc.

## Pipeline

```
Input (Link/File) → Download → Vocal Separation (Demucs) → ASR (Whisper/Paraformer)
                  → Translate → TTS (VieNeu) → Retime → Mix + Subtitle → Export MP4
```

## Tech Stack

- **Language:** Python 3.10+
- **GUI:** PySide6 (Qt)
- **ASR:** faster-whisper, sherpa-onnx (Paraformer)
- **Translation:** Gemini (via OpenRouter), Google Gemini, OpenAI, Anthropic, DeepSeek, custom endpoint
- **TTS:** VieNeu (local CPU, ~1s/câu, voice cloning từ đoạn ghi âm)
- **Audio:** pydub, Demucs (vocal separation), soundfile
- **Video:** ffmpeg (libass for subtitles), yt-dlp (downloads)
- **Subtitle:** ASS karaoke format, SRT
- **Browser:** Playwright (Douyin downloads)

## Directory Structure

```
autodub/           # Core library (~43 .py files)
  content/         # Metadata/thumbnail generation (Gemini)
  media/           # Audio, video, download, subtitle, retime, timing, vocal separation
  speech/          # ASR transcriber; tts/ — VieNeu engine + voice catalog + worker
  text/            # Translation engines, SRT, ASS karaoke, Vietnamese text processing
  pipeline.py      # Main orchestration pipeline
  batch.py         # Batch processing
  config.py        # Configuration (.env loader)
  editor.py        # Sentence editor
  workdir.py       # Working directory management
  progress.py      # Progress tracking
autodub_gui/       # PySide6 GUI (~67 .py files)
  app.py           # MainWindow — sidebar + pages
  shell.py         # Sidebar/header shell
  pages/           # home, new_project, batch, download, editor, projects, settings, help
  ui/              # Shared widgets: buttons, inputs, cards, modal, toast…
  video/           # Video player + timeline
  workers.py       # Background worker threads
  theme.py         # Dark theme QSS
  tokens.py        # Design tokens — the ONLY file allowed to contain hex colors
  fonts.py         # Font management
control_server/    # Node.js remote control server (VPS-side)
scripts/           # build_exe.py + setup scripts (setup_vieneu.py, setup_paraformer.py, setup_douyin.py)
tests/             # pytest tests
fonts/             # TTF font files for subtitles
models/            # ML models (gitignored): paraformer-zh, vieneu
voices/            # VieNeu preset voices (.wav)
output/            # Pipeline outputs (gitignored)
downloads/         # Downloaded source videos (gitignored)
```

## Key Conventions

- Vietnamese is the target language always; source is typically Chinese (Douyin) or English
- Config via `.env` file (copied from `.env.example`), editable in Settings page
- TTS is VieNeu-only; voice via `VIENEU_VOICE`, style via `VIENEU_STYLE`
- ASR engines: `whisper` (default, faster-whisper), `paraformer` (Chinese-optimized)
- Translate engines: `openrouter` (Gemini, mặc định), `gemini`, `openai`, `anthropic`, `deepseek`, `custom`
- Side venvs: `.venv-vieneu` (embedded Python, TTS), `.venv-asr` (3.10, Paraformer); `.venv-gpu` được dò để mượn CUDA torch cho Demucs/Whisper

## Run Commands

```bash
py -m autodub_gui                  # Launch GUI
py -m pytest tests/ -q             # Run tests
py scripts/setup_vieneu.py         # Setup VieNeu TTS (one-time)
py -m autodub.pipeline <url>       # CLI pipeline (rare)
```

## Build

- Never run PyInstaller directly — use `py scripts/build_exe.py` (generates `autodub_gui/_embedded.py`, runs `autodub.spec`, assembles the distributable)
- Output exe: `VoxDub.exe` (onedir)

## Important Notes

- Never commit `.env` — it contains API keys (gitignored)
- `linkdouyin.txt` at project root contains 172 Douyin video URLs for batch processing
- The project is NOT a git repository — no `git` commands available
- Windows only; requires ffmpeg (full build with libass) on PATH
- `python` is NOT on PATH on this machine — always use the `py` launcher
