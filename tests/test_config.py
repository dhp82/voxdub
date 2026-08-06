import os

import pytest

from autodub.config import ConfigError, Settings
from autodub.utils import app_root


def test_settings_load_env_vars(monkeypatch):
    monkeypatch.setattr("autodub.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("WHISPER_MODEL", "large-v3")
    monkeypatch.setenv("DEFAULT_SOURCE_LANG", "en-US")
    monkeypatch.setenv("AUDIO_SAMPLE_RATE", "16000")
    monkeypatch.setenv("OUTPUT_DIR", "./output")

    settings = Settings.load()

    assert settings.whisper_model == "large-v3"
    assert settings.default_source_lang == "en-US"
    assert settings.audio_sample_rate == 16000
    # Relative output dirs anchor at the app root (exe folder when frozen).
    assert settings.output_dir == os.path.join(app_root(), "output")


def test_settings_defaults(monkeypatch):
    """When optional env vars are not set, Settings should use defaults."""
    # Prevent load_dotenv from loading .env file values
    monkeypatch.setattr("autodub.config.load_dotenv", lambda *a, **kw: None)

    for var in ("WHISPER_MODEL", "DEFAULT_SOURCE_LANG", "QUALITY_PRESET",
                "AUDIO_SAMPLE_RATE", "OUTPUT_DIR", "VIDEO_URL"):
        monkeypatch.delenv(var, raising=False)

    settings = Settings.load()

    # Preset "balanced" mặc định → whisper "auto" (large-v3 GPU / medium CPU)
    assert settings.quality_preset == "balanced"
    assert settings.whisper_model == "auto"
    assert settings.default_source_lang == "zh-CN"
    assert settings.audio_sample_rate == 16000
    assert settings.output_dir == os.path.join(app_root(), "output")
    assert settings.video_url == ""


def test_quality_presets(monkeypatch):
    monkeypatch.setattr("autodub.config.load_dotenv", lambda *a, **kw: None)
    for var in ("WHISPER_MODEL", "HQ_BACKGROUND",
                "TRANSLATE_ANALYSIS", "TRANSLATE_REVIEW"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("QUALITY_PRESET", "fast")
    s = Settings.load()
    assert s.whisper_model == "medium"
    assert s.hq_background is False
    assert s.translate_analysis is False
    monkeypatch.setenv("QUALITY_PRESET", "quality")
    s = Settings.load()
    assert s.whisper_model == "auto"
    assert s.hq_background is True
    # Explicit env var beats the preset
    monkeypatch.setenv("WHISPER_MODEL", "small")
    assert Settings.load().whisper_model == "small"


def test_resolved_whisper_model():
    s = Settings(whisper_model="auto")
    assert s.resolved_whisper_model(cuda_available=True) == "large-v3"
    assert s.resolved_whisper_model(cuda_available=False) == "medium"
    s2 = Settings(whisper_model="small")
    assert s2.resolved_whisper_model(cuda_available=True) == "small"


def test_timing_settings_clamped(monkeypatch):
    monkeypatch.setattr("autodub.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("TIMING_MAX_ATEMPO", "2.0")   # trần cứng 1.3
    monkeypatch.setenv("TIMING_MAX_DRIFT_S", "99")
    monkeypatch.setenv("BG_DUCK_VOICE_DB", "5")      # duck không được dương
    s = Settings.load()
    assert s.timing_max_atempo == 1.3
    assert s.timing_max_drift_s == 5.0
    assert s.bg_duck_voice_db == 0.0


def test_settings_require_raises_on_missing():
    settings = Settings(openrouter_api_key="")
    with pytest.raises(ConfigError, match="OPENROUTER_API_KEY"):
        settings.require("openrouter_api_key")


def test_settings_require_passes_when_set():
    Settings(openrouter_api_key="k").require("openrouter_api_key")


def test_subtitle_style_from_settings(monkeypatch):
    monkeypatch.setattr("autodub.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("SUBTITLE_POSITION", "middle")
    monkeypatch.setenv("SUBTITLE_FONT_SIZE", "30")
    monkeypatch.setenv("SUBTITLE_COLOR", "#FFFF00")
    style = Settings.load().subtitle_style()
    assert style["position"] == "middle"
    assert style["font_size"] == 30
    assert style["color"] == "#FFFF00"


def test_subtitle_position_typo_falls_back(monkeypatch):
    monkeypatch.setattr("autodub.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("SUBTITLE_POSITION", "sideways")
    assert Settings.load().subtitle_position == "bottom"


def test_translate_configured_rejects_placeholder_key():
    """API Key trong tệp mẫu phải bị coi là CHƯA điền."""
    base = dict(translate_engine="openrouter",
                openrouter_url="https://x/v1/chat/completions",
                openrouter_model="m")
    assert not Settings(**base, openrouter_api_key="").translate_configured()
    assert not Settings(**base,
                        openrouter_api_key="sk_your_key_here").translate_configured()
    assert Settings(**base, openrouter_api_key="sk-abc123").translate_configured()


def test_translate_configured_needs_url_and_model():
    """Thiếu địa chỉ hoặc tên mô hình cũng là chưa đủ để chạy."""
    assert not Settings(translate_engine="deepseek",
                        deepseek_api_key="k", deepseek_url="",
                        deepseek_model="m").translate_configured()
    assert not Settings(translate_engine="deepseek", deepseek_api_key="k",
                        deepseek_url="https://x", deepseek_model="").translate_configured()


def test_translate_configured_off_when_disabled():
    assert not Settings(translate_enabled=False, translate_engine="gemini",
                        google_api_key="k",
                        gemini_translate_model="m").translate_configured()


def test_gemini_needs_no_url():
    """Gemini đi qua thư viện riêng nên không cần địa chỉ máy chủ."""
    s = Settings(translate_engine="gemini", google_api_key="k",
                 gemini_translate_model="gemini-flash-latest")
    assert s.translate_configured()
    key, url, model = s.translate_credentials()
    assert (key, url, model) == ("k", "", "gemini-flash-latest")


def test_unknown_engine_falls_back_to_openrouter(monkeypatch):
    monkeypatch.setattr("autodub.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("TRANSLATE_ENGINE", "khong-co-that")
    assert Settings.load().translate_engine == "openrouter"


def test_provider_defaults(monkeypatch):
    """Mỗi nơi dịch có sẵn địa chỉ và mô hình mặc định dùng được ngay."""
    monkeypatch.setattr("autodub.config.load_dotenv", lambda *a, **kw: None)
    for name in ("OPENROUTER", "OPENAI", "ANTHROPIC", "DEEPSEEK"):
        for suffix in ("URL", "MODEL", "API_KEY"):
            monkeypatch.delenv(f"{name}_{suffix}", raising=False)
    s = Settings.load()
    for engine in ("openrouter", "openai", "anthropic", "deepseek"):
        _key, url, model = s.translate_credentials(engine)
        assert url.startswith("https://"), engine
        assert model, engine


def test_vi_output_dir_default_and_override():
    assert Settings(output_dir="./out").vi_output_dir().replace("\\", "/") == "./out/VN"
    assert Settings(vietnamese_output_dir="D:/VN").vi_output_dir() == "D:/VN"


def test_translate_defaults(monkeypatch):
    monkeypatch.setattr("autodub.config.load_dotenv", lambda *a, **kw: None)
    for var in ("TRANSLATE_ENGINE", "TRANSLATE_ENABLED",
                "TRANSLATE_BATCH_SIZE"):
        monkeypatch.delenv(var, raising=False)
    s = Settings.load()
    assert s.translate_engine == "openrouter"
    assert s.translate_enabled is True
    assert s.translate_batch_size == 40


def test_translate_enabled_off(monkeypatch):
    monkeypatch.setattr("autodub.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("TRANSLATE_ENABLED", "false")
    assert Settings.load().translate_enabled is False


def test_speed_defaults(monkeypatch):
    monkeypatch.setattr("autodub.config.load_dotenv", lambda *a, **kw: None)
    for var in ("VIDEO_SPEED", "VOICE_SPEED", "TRANSLATE_CPS_BUDGET"):
        monkeypatch.delenv(var, raising=False)
    s = Settings.load()
    assert s.video_speed == 1.0
    assert s.voice_speed == 1.0
    assert s.translate_cps_budget == 12.5


def test_speed_env_overrides(monkeypatch):
    monkeypatch.setattr("autodub.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("VIDEO_SPEED", "0.82")
    monkeypatch.setenv("VOICE_SPEED", "1.2")
    monkeypatch.setenv("TRANSLATE_CPS_BUDGET", "11")
    s = Settings.load()
    assert s.video_speed == 0.82
    assert s.voice_speed == 1.2
    assert s.translate_cps_budget == 11.0


def test_video_speed_clamped(monkeypatch):
    monkeypatch.setattr("autodub.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("VIDEO_SPEED", "0.1")
    assert Settings.load().video_speed == 0.5
    monkeypatch.setenv("VIDEO_SPEED", "1.5")   # never speeds UP the video
    assert Settings.load().video_speed == 1.0


def test_env_float_typo_falls_back(monkeypatch):
    """A typo in a float .env var must not crash GUI import (env_float)."""
    monkeypatch.setattr("autodub.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("VIDEO_SPEED", "fast")
    monkeypatch.setenv("TRANSLATE_CPS_BUDGET", "twelve")
    s = Settings.load()
    assert s.video_speed == 1.0
    assert s.translate_cps_budget == 12.5


def test_voice_speed_clamped(monkeypatch):
    monkeypatch.setattr("autodub.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("VOICE_SPEED", "5.0")
    assert Settings.load().voice_speed == 2.0
    monkeypatch.setenv("VOICE_SPEED", "0.1")
    assert Settings.load().voice_speed == 0.5


def test_whisper_beam_size_default_and_clamp(monkeypatch):
    monkeypatch.setattr("autodub.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.delenv("WHISPER_BEAM_SIZE", raising=False)
    assert Settings.load().whisper_beam_size == 5   # mặc định = thư viện
    monkeypatch.setenv("WHISPER_BEAM_SIZE", "1")
    assert Settings.load().whisper_beam_size == 1
    monkeypatch.setenv("WHISPER_BEAM_SIZE", "99")
    assert Settings.load().whisper_beam_size == 10
    monkeypatch.setenv("WHISPER_BEAM_SIZE", "0")
    assert Settings.load().whisper_beam_size == 1


def test_vieneu_workers_env_wins_over_governor(monkeypatch):
    monkeypatch.setattr("autodub.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("VIENEU_MAX_WORKERS", "8")
    assert Settings.load().vieneu_max_workers == 8


def test_vieneu_workers_adaptive_by_ram(monkeypatch):
    import autodub.config as config
    monkeypatch.setattr("autodub.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.delenv("VIENEU_MAX_WORKERS", raising=False)
    monkeypatch.setattr("autodub.config.os.cpu_count", lambda: 16)

    monkeypatch.setattr("autodub.sysinfo.available_ram_gb", lambda: 12.0)
    config._governor_logged = True   # đã log rồi — test không cần log
    assert Settings.load().vieneu_max_workers == 3

    monkeypatch.setattr("autodub.sysinfo.available_ram_gb", lambda: 7.0)
    assert Settings.load().vieneu_max_workers == 2

    monkeypatch.setattr("autodub.sysinfo.available_ram_gb", lambda: 3.0)
    assert Settings.load().vieneu_max_workers == 1

    # Không đọc được RAM → giữ mặc định cũ (3)
    monkeypatch.setattr("autodub.sysinfo.available_ram_gb", lambda: None)
    assert Settings.load().vieneu_max_workers == 3


def test_vieneu_workers_capped_by_cores(monkeypatch):
    import autodub.config as config
    monkeypatch.setattr("autodub.config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.delenv("VIENEU_MAX_WORKERS", raising=False)
    monkeypatch.setattr("autodub.sysinfo.available_ram_gb", lambda: 32.0)
    config._governor_logged = True
    # 4 nhân → tối đa 2 tiến trình dù RAM dư
    monkeypatch.setattr("autodub.config.os.cpu_count", lambda: 4)
    assert Settings.load().vieneu_max_workers == 2
