"""Cài đặt VieNeu-TTS — giọng đọc tiếng Việt chạy CPU (nhanh, không cần GPU).

Chạy 1 lần:  py scripts/setup_vieneu.py

Các bước đều resume-safe — chạy lại script sẽ bỏ qua phần đã xong:
  1. Tạo virtualenv .venv-vieneu
  2. pip install vieneu (ONNX Runtime — KHÔNG cài torch/GPU)
  3. Tải model VieNeu-TTS-v3-Turbo về models/vieneu (~300 MB)
  4. Ghi danh sách 14 giọng đọc (voices.json) cho GUI
  5. Render thử 1 câu (smoke test) → installed_ok.json
  6. Bật VIENEU_TTS_ENABLED=true trong .env
"""
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_DIR = os.path.join(PROJECT_ROOT, ".venv-vieneu")
VENV_PY = os.path.join(VENV_DIR, "Scripts" if os.name == "nt" else "bin",
                       "python.exe" if os.name == "nt" else "python")
MODEL_DIR = os.path.join(PROJECT_ROOT, "models", "vieneu")
MARKER = os.path.join(MODEL_DIR, "installed_ok.json")
VOICES_JSON = os.path.join(MODEL_DIR, "voices.json")


def log(msg: str) -> None:
    print(f"[setup-vieneu] {msg}", flush=True)


def step_venv() -> None:
    if os.path.isfile(VENV_PY):
        log("venv .venv-vieneu đã có — bỏ qua")
        return
    log("tạo virtualenv .venv-vieneu ...")
    subprocess.run([sys.executable, "-m", "venv", VENV_DIR], check=True)


def step_install() -> None:
    probe = subprocess.run([VENV_PY, "-c", "import vieneu"],
                           capture_output=True)
    if probe.returncode == 0:
        log("package vieneu đã cài — bỏ qua")
        return
    log("cài vieneu (ONNX, không cần GPU) ...")
    subprocess.run([VENV_PY, "-m", "pip", "install", "--quiet", "vieneu"],
                   check=True)


def step_model_and_voices() -> None:
    if os.path.isfile(VOICES_JSON) and os.path.isfile(MARKER):
        log("model + voices.json đã có — bỏ qua")
        return
    log("tải model VieNeu-TTS-v3-Turbo (~300 MB, lần đầu hơi lâu) ...")
    os.makedirs(MODEL_DIR, exist_ok=True)
    code = f"""
import json, os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["HF_HOME"] = {MODEL_DIR!r}
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
from vieneu import Vieneu
v = Vieneu(backend="onnx")
voices = v.list_preset_voices()
json.dump([{{"label": l, "name": n}} for l, n in voices],
          open({VOICES_JSON!r}, "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
# Smoke: render 1 câu có số để chắc chắn model đọc được
import soundfile as sf
audio = v.infer("xin chào, hai nghìn không trăm hai mươi sáu", voice=voices[0][1])
smoke = os.path.join({MODEL_DIR!r}, "smoke_test.wav")
sf.write(smoke, audio, v.sample_rate)
assert os.path.getsize(smoke) > 10000
json.dump({{"ok": True, "model": "VieNeu-TTS-v3-Turbo", "backend": "onnx",
           "sample_rate": v.sample_rate}},
          open({MARKER!r}, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("model OK,", len(voices), "giọng")
"""
    subprocess.run([VENV_PY, "-c", code], check=True)


def step_enable_env() -> None:
    env_path = os.path.join(PROJECT_ROOT, ".env")
    lines: list[str] = []
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith("VIENEU_TTS_ENABLED="):
            lines[i] = "VIENEU_TTS_ENABLED=true"
            found = True
            break
    if not found:
        lines.append("VIENEU_TTS_ENABLED=true")
    with open(env_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    log("đã bật VIENEU_TTS_ENABLED=true trong .env")


def main() -> None:
    log("Cài đặt VieNeu-TTS — giọng đọc tiếng Việt chạy CPU")
    log("Model: pnnbao-ump/VieNeu-TTS-v3-Turbo (kiểm tra license trên "
        "HuggingFace trước khi dùng thương mại)")
    step_venv()
    step_install()
    step_model_and_voices()
    step_enable_env()
    log("XONG — mở app, giọng đọc VieNeu được dùng tự động (14 giọng nam/nữ).")


if __name__ == "__main__":
    main()
