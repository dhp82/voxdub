"""DeepSeek translation provider using its OpenAI-compatible API."""
from __future__ import annotations
from dataclasses import replace
from autodub.text import translate_openrouter as _compatible
from autodub.text.translate_common import TranslateError

def translate_segments(segments, target, source_lang, settings, reporter=None, checkpoint_path=None):
    if not settings.deepseek_api_key.strip():
        raise TranslateError("DEEPSEEK_API_KEY is missing. Configure it in Translation settings.")
    effective = replace(settings, openrouter_api_key=settings.deepseek_api_key,
                        openrouter_model=settings.deepseek_model,
                        openrouter_base_url=settings.deepseek_base_url)
    return _compatible.translate_segments(segments, target, source_lang,
                                           effective, reporter, checkpoint_path)
