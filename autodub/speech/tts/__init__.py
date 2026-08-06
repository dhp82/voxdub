"""Bộ tạo giọng đọc tiếng Việt — chỉ còn VieNeu.

``get_synthesizer(target, settings, voice)`` trả về bộ tạo giọng cho MỘT tên
giọng cụ thể (xem :mod:`autodub.speech.tts.voices`). Không còn khái niệm
"engine" hay "giọng nam / giọng nữ": mọi nơi trong ứng dụng đều truyền thẳng
tên giọng, giới tính chỉ là bộ lọc trong giao diện.
"""
from __future__ import annotations

from autodub.config import ConfigError, Settings
from autodub.languages import TargetLang
from autodub.speech.tts import voices as voice_catalog
from autodub.speech.tts.base import Synthesizer, TTSResult
from autodub.utils import setup_logging

logger = setup_logging("autodub.tts")

__all__ = ["Synthesizer", "TTSResult", "get_synthesizer", "SynthCache",
           "NOT_INSTALLED_HINT"]

NOT_INSTALLED_HINT = (
    "Chưa cài bộ giọng VieNeu. Chạy một lần: py scripts/setup_vieneu.py"
)


class SynthCache:
    """Dùng lại một bộ tạo giọng cho mỗi TÊN GIỌNG xuyên suốt nhiều video.

    Chạy hàng loạt N video mà tạo mới mỗi lần thì phải nạp model N lần. Bộ nhớ
    đệm này phát lại đúng phiên bản đã khởi động sẵn cho cùng một giọng, và
    đóng tất cả ở một chỗ khi cả lô chạy xong.
    """

    def __init__(self):
        self._cache: dict[str, Synthesizer] = {}

    def get(self, target: TargetLang, settings: Settings,
            voice: str | None = None) -> Synthesizer:
        name = voice_catalog.resolve(settings, voice)
        synth = self._cache.get(name)
        if synth is None:
            synth = get_synthesizer(target, settings, name)
            self._cache[name] = synth
        return synth

    def close(self) -> None:
        # Đóng từng bộ một: một lỗi khi đóng không được làm rò rỉ các bộ khác.
        for synth in self._cache.values():
            close = getattr(synth, "close", None)
            if close is not None:
                try:
                    close()
                except Exception as e:
                    logger.warning(f"Lỗi khi đóng bộ tạo giọng ({e}) — bỏ qua")
        self._cache.clear()


def get_synthesizer(
    target: TargetLang,
    settings: Settings,
    voice: str | None = None,
) -> Synthesizer:
    """Bộ tạo giọng VieNeu cho tên giọng đang chọn."""
    if not settings.vieneu_configured():
        raise ConfigError(NOT_INSTALLED_HINT)

    from autodub.speech.tts.vieneu_vi import VieNeuSynthesizer

    voice_name = voice_catalog.resolve(settings, voice)
    workers = min(settings.parallel_workers, settings.vieneu_max_workers)
    logger.info(f"Dùng giọng VieNeu «{voice_name}» ({workers} luồng, CPU)")
    return VieNeuSynthesizer(settings, voice_name=voice_name,
                             num_workers=workers)
