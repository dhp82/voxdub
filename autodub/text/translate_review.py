"""Review pass — soát và dịch lại các câu "nghi vấn" sau lượt dịch chính.

Tiêu chí nghi vấn (rẻ, thuần cục bộ — không tốn request để tìm):

- vượt ``max_chars`` của slot quá 25% → gần như chắc chắn tràn slot,
  timeline phải dồn trễ/nén vì đúng câu này
- còn ký tự CJK (lưới cuối — các engine đã tự vá nhưng vẫn có thể lọt)
- ngắn bất thường so với nguồn (< 25% ký tự nguồn) → thường là dịch sót ý

Các câu nghi vấn được dịch lại TỪNG CÂU (kèm 2 câu ngữ cảnh trước/sau) qua
đúng nơi dịch đã dùng; bản mới chỉ được nhận khi thật sự tốt hơn (hết CJK,
ngắn lại khi lý do là tràn ngân sách).

Mọi lỗi đều không gây hỏng: rà soát lỗi thì giữ nguyên bản dịch lượt đầu.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from autodub.languages import TargetLang
from autodub.text.translate_hint import (
    effective_cps,
    build_translation_prompt,
    ensure_terminal_punct,
    payload_segment,
)
from autodub.text.translate_common import (
    TranslateError,
    contains_cjk,
    merge_translations,
    parse_response_segments,
)
from autodub.utils import setup_logging

logger = setup_logging("autodub.translate_review")

_OVER_BUDGET_TOLERANCE = 1.25   # vượt max_chars 25% mới tính là tràn
_MIN_SOURCE_RATIO = 0.25        # dịch < 25% độ dài nguồn = nghi sót ý
_MAX_REVIEW_FRACTION = 0.35     # >35% số câu bị cờ ⇒ lỗi hệ thống, bỏ review


def _flag(seg: dict, text_field: str, cps: float) -> str | None:
    """Lý do nghi vấn của một câu, hoặc None nếu ổn."""
    text = str(seg.get(text_field, "")).strip()
    if not text:
        return None  # đã được các lớp trước xử lý (write_silence)
    if contains_cjk(text):
        return "cjk"
    budget = payload_segment(seg, cps).get("max_chars")
    if budget and len(text) > budget * _OVER_BUDGET_TOLERANCE:
        return "over_budget"
    src = str(seg.get("text", "")).strip()
    if src and len(text) < len(src) * _MIN_SOURCE_RATIO and len(src) > 20:
        return "too_short"
    return None


def _retry_prompt(seg: dict, reason: str, target: TargetLang,
                  neighbors: str, cps: float) -> str:
    reasons = {
        "cjk": "it still contains Chinese characters — the result must be "
               "pure Vietnamese (Latin script only)",
        "over_budget": "it is TOO LONG for its time slot — rephrase more "
                       "concisely (cut filler, shorter synonyms) while "
                       "keeping the full meaning; stay within max_chars",
        "too_short": "it looks like part of the meaning was DROPPED — "
                     "translate the FULL content of the source line",
    }
    return (
        f"One translated segment needs fixing because {reasons[reason]}.\n"
        f"Surrounding lines for context (do NOT translate them):\n{neighbors}\n\n"
        f'Current (bad) translation: '
        f'{json.dumps(str(seg.get(target.text_field, "")), ensure_ascii=False)}\n'
        f'Translate this ONE segment again. Return ONLY JSON: '
        f'{{"segments": [{{"id": ..., "{target.text_field}": "..."}}]}}\n\n'
        + json.dumps(payload_segment(seg, cps), ensure_ascii=False)
    )


def _accept(old: str, new: str, reason: str, budget: int | None) -> bool:
    """Bản dịch lại chỉ được nhận khi thật sự sửa được lý do bị cờ."""
    new = new.strip()
    if not new:
        return False
    if contains_cjk(new):
        return False
    if reason == "over_budget":
        if budget and len(new) > budget * _OVER_BUDGET_TOLERANCE:
            return False       # vẫn tràn — giữ bản cũ cho đỡ tốn một lượt TTS
        return len(new) < len(old)
    return True


def review_translations(
    segments: list[dict], target: TargetLang, source_lang: str,
    engine: str, settings,
) -> list[dict]:
    """Soát + dịch lại các câu nghi vấn. Trả về danh sách câu (có thể mới).

    Không sửa tại chỗ — trả về bản sao khi có thay đổi.
    """
    if not settings.translate_review or not settings.translate_configured(engine):
        return segments

    cps = effective_cps(settings)
    flagged = [(i, _flag(s, target.text_field, cps))
               for i, s in enumerate(segments)]
    flagged = [(i, r) for i, r in flagged if r]
    if not flagged:
        logger.info("Soát lại bản dịch: mọi câu đều đạt")
        return segments

    by_reason: dict[str, int] = {}
    for _, r in flagged:
        by_reason[r] = by_reason.get(r, 0) + 1
    # Nhãn tiếng Việt cho Nhật ký GUI (log kỹ thuật xem code/console).
    _LABELS = {"over_budget": "hơi dài so với chỗ trống",
               "cjk": "còn sót chữ Trung",
               "too_short": "nghi dịch sót ý"}
    breakdown = ", ".join(f"{v} câu {_LABELS.get(k, k)}"
                          for k, v in by_reason.items())

    if len(flagged) > len(segments) * _MAX_REVIEW_FRACTION:
        # Cờ tràn lan ⇒ vấn đề nằm ở prompt/budget chứ không phải từng câu —
        # dịch lại từng câu chỉ đốt quota. Ghi nhận và thôi.
        hint = ("video nói nhanh và dày — nếu nghe bị chồng tiếng, giảm "
                "Tốc độ video trong Cài đặt rồi chạy lại"
                if by_reason.get("over_budget", 0) >= len(flagged) * 0.6
                else "xem quality_report.json trong thư mục kết quả")
        logger.warning(
            f"Soát lại bản dịch: {len(flagged)}/{len(segments)} câu cần xem "
            f"({breakdown}) — nhiều quá nên giữ nguyên bản dịch, không sửa "
            f"từng câu. Gợi ý: {hint}.")
        return segments

    logger.info(f"Soát lại bản dịch: {len(flagged)} câu cần sửa "
                f"({breakdown}) — đang nhờ AI dịch lại các câu đó...")

    system_prompt = build_translation_prompt(target, source_lang,
                                             cps_budget=cps, settings=settings)

    def _neighbors(idx: int) -> str:
        rows = []
        for j in range(max(0, idx - 2), min(len(segments), idx + 3)):
            if j == idx:
                continue
            rows.append(f'  {segments[j].get("id")}: '
                        f'{str(segments[j].get("text", ""))[:80]}')
        return "\n".join(rows)

    def _one(item: tuple[int, str]) -> tuple[int, str | None]:
        idx, reason = item
        seg = segments[idx]
        user_prompt = _retry_prompt(seg, reason, target, _neighbors(idx), cps)
        try:
            if engine == "gemini":
                from autodub.text.translate_gemini import (_generate,
                                                           _response_schema)
                content = _generate(
                    settings.google_api_key, settings.gemini_translate_model,
                    system_prompt, user_prompt, max_retries=2,
                    schema=_response_schema(target.text_field))
            else:
                from autodub.text.translate_openai import (chat,
                                                           response_schema)
                content = chat(
                    settings, engine, system_prompt, user_prompt,
                    max_retries=2,
                    schema=response_schema(target.text_field))
            returned = parse_response_segments(content)
            redone = merge_translations([seg], returned, target.text_field)[0]
            new_text = redone[target.text_field]
            budget = payload_segment(seg, cps).get("max_chars")
            if _accept(str(seg.get(target.text_field, "")), new_text,
                       reason, budget):
                return idx, ensure_terminal_punct(new_text)
        except TranslateError as e:
            logger.warning(f"Soát lại câu {seg.get('id')} lỗi "
                           f"({str(e)[:60]}) — giữ bản cũ")
        except Exception as e:
            logger.warning(f"Soát lại câu {seg.get('id')} lỗi bất ngờ "
                           f"({type(e).__name__}: {str(e)[:60]}) — giữ bản cũ")
        return idx, None

    workers = min(max(1, int(settings.parallel_workers)), len(flagged), 4)
    fixed: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for idx, text in pool.map(_one, flagged):
            if text is not None:
                fixed[idx] = text

    if not fixed:
        logger.info("Soát lại bản dịch: bản dịch lại không tốt hơn — "
                    "giữ bản đầu")
        return segments
    logger.info(f"Soát lại bản dịch: đã sửa xong {len(fixed)}/{len(flagged)} câu")
    return [
        ({**s, target.text_field: fixed[i]} if i in fixed else s)
        for i, s in enumerate(segments)
    ]
