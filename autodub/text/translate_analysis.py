"""Pass 0 "hiểu video" — phân tích transcript nguồn TRƯỚC khi dịch.

Vấn đề: các batch dịch chỉ thấy 3 câu ngữ cảnh liền trước, nên model không
biết video nói về gì, ai đang nói với ai, thuật ngữ nào lặp lại — xưng hô
trôi dạt và thuật ngữ mỗi batch dịch một kiểu.

Giải pháp: MỘT request duy nhất gửi toàn bộ (hoặc mẫu đại diện của)
transcript nguồn cho engine cloud, nhận về:

- tóm tắt nội dung + chủ đề (domain)
- quy ước xưng hô phù hợp cả video
- glossary các thuật ngữ/tên riêng lặp lại (gốc = dịch)
- ghi chú văn phong

Kết quả được bơm vào ``build_user_context_block`` qua một bản sao Settings —
mục nào người dùng đã điền tay trong tab Cài đặt thì GIỮ NGUYÊN (tay > máy).
Cache tại ``data/video_context.json`` nên chạy tiếp không tốn thêm request.

Mọi lỗi ở đây đều non-fatal: phân tích hỏng thì dịch như cũ.
"""
from __future__ import annotations

import dataclasses
import json
import os

from autodub.utils import save_json_atomic, setup_logging

logger = setup_logging("autodub.translate_analysis")

# Transcript quá dài thì lấy mẫu đầu–giữa–cuối: đủ nắm mạch nội dung và
# nhân vật mà không vượt cửa sổ ngữ cảnh / tốn token vô ích.
_MAX_SAMPLE_SEGMENTS = 240

_ANALYSIS_PROMPT = """You are preparing CONTEXT for translating a {source_lang} video transcript to Vietnamese (video dubbing).
Read the transcript lines below and produce a compact analysis as STRICT JSON (no markdown fences, no commentary):

{{
  "summary": "<2-4 sentences in Vietnamese: what the video is about, who is speaking, to whom>",
  "domain": "<topic/domain in Vietnamese, e.g. 'review công nghệ', 'vlog nấu ăn', 'phim ngắn'>",
  "pronouns": "<the most natural Vietnamese pronoun convention for THIS video, e.g. 'mình – các bạn' for a creator addressing viewers, 'tôi – bạn' for formal>",
  "glossary": ["<source term> = <fixed Vietnamese translation>", ...],
  "style_notes": "<1-2 sentences in Vietnamese about tone/register to keep>"
}}

Glossary rules: include ONLY terms that actually recur (person/brand/product names, domain jargon) and would otherwise be translated inconsistently. Keep Latin brand names as-is. Pinyin for Chinese person names. Max 15 entries.

TRANSCRIPT LINES:
{lines}"""


def _sample_lines(segments: list[dict]) -> str:
    """Các dòng nguồn gửi cho model (mẫu đầu–giữa–cuối nếu quá dài)."""
    texts = [str(s.get("text", "")).strip() for s in segments]
    texts = [t for t in texts if t]
    if len(texts) > _MAX_SAMPLE_SEGMENTS:
        third = _MAX_SAMPLE_SEGMENTS // 3
        mid = len(texts) // 2
        texts = (texts[:third]
                 + ["..."]
                 + texts[mid - third // 2:mid + third // 2]
                 + ["..."]
                 + texts[-third:])
    return "\n".join(texts)


# Ép mô hình sinh đúng lược đồ — hết cảnh một dấu ngoặc kép trong phần tóm
# tắt làm hỏng cả khối JSON (không chết chương trình nhưng phí một lượt gọi).
_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "domain": {"type": "string"},
        "pronouns": {"type": "string"},
        "glossary": {"type": "array", "items": {"type": "string"}},
        "style_notes": {"type": "string"},
    },
    "required": ["summary", "domain", "pronouns", "glossary", "style_notes"],
    "additionalProperties": False,
}

_SYSTEM = "You analyze video transcripts and return strict JSON."


def _call_engine(prompt: str, engine: str, settings) -> str | None:
    """Một lượt phân tích qua nơi dịch đang chọn."""
    if not settings.translate_configured(engine):
        return None
    if engine == "gemini":
        from autodub.text.translate_gemini import _generate
        return _generate(
            settings.google_api_key, settings.gemini_translate_model,
            system=_SYSTEM, user=prompt, max_retries=2)
    from autodub.text.translate_openai import chat
    return chat(settings, engine, system=_SYSTEM, user=prompt,
                max_retries=2, schema=_ANALYSIS_SCHEMA)


def _parse_analysis(content: str) -> dict | None:
    """JSON phân tích (chịu được khối ```json và chữ thừa quanh JSON)."""
    from autodub.text.translate_common import repair_json, strip_fences

    text = strip_fences(content)
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        try:
            data = json.loads(repair_json(text))
        except (ValueError, TypeError):
            return None
    return data if isinstance(data, dict) else None


def analyze_transcript(
    segments: list[dict], source_lang: str, engine: str, settings,
    cache_path: str | None = None,
) -> dict | None:
    """Chạy lượt 0; trả về dict phân tích hoặc None (bỏ qua, không gây lỗi)."""
    if not settings.translate_analysis:
        return None

    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                cached = json.load(f)
            logger.info("Dùng lại phân tích ngữ cảnh video từ lần chạy trước")
            return cached
        except (json.JSONDecodeError, OSError):
            pass

    prompt = _ANALYSIS_PROMPT.format(
        source_lang=source_lang, lines=_sample_lines(segments))
    try:
        content = _call_engine(prompt, engine, settings)
    except Exception as e:
        logger.warning(f"Phân tích ngữ cảnh video lỗi ({e}) — dịch như cũ")
        return None
    if not content:
        return None
    data = _parse_analysis(content)
    if not data:
        logger.warning("Phân tích ngữ cảnh trả về JSON không đọc được — bỏ qua")
        return None

    logger.info(f"Ngữ cảnh video: {str(data.get('summary', ''))[:100]}...")
    if cache_path:
        try:
            save_json_atomic(data, cache_path)
        except OSError:
            pass
    return data


def apply_analysis(settings, analysis: dict | None):
    """Bản sao Settings với ngữ cảnh phân tích bơm vào các ô còn TRỐNG.

    Người dùng đã điền tay mục nào trong tab Cài đặt thì mục đó thắng.
    Trả về chính ``settings`` khi không có gì để bơm.
    """
    if not analysis:
        return settings
    updates: dict = {}
    if not settings.translate_domain and analysis.get("domain"):
        updates["translate_domain"] = str(analysis["domain"]).strip()
    if not settings.translate_context and analysis.get("summary"):
        updates["translate_context"] = str(analysis["summary"]).strip()
    if not settings.translate_pronouns and analysis.get("pronouns"):
        updates["translate_pronouns"] = str(analysis["pronouns"]).strip()
    if not settings.translate_glossary and analysis.get("glossary"):
        items = analysis["glossary"]
        if isinstance(items, list):
            updates["translate_glossary"] = "\n".join(
                str(x).strip() for x in items[:15] if str(x).strip())
    if not settings.translate_style_notes and analysis.get("style_notes"):
        updates["translate_style_notes"] = str(analysis["style_notes"]).strip()
    if not updates:
        return settings
    logger.info(f"Bơm ngữ cảnh tự phân tích vào prompt dịch: "
                f"{', '.join(k.replace('translate_', '') for k in updates)}")
    return dataclasses.replace(settings, **updates)
