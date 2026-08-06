"""Dịch lời thoại bằng Google Gemini (thư viện ``google-genai``).

Gemini là nơi dịch duy nhất không nói chuẩn OpenAI nên có module riêng; phần
đọc JSON và ghép bản dịch vẫn dùng chung
:mod:`autodub.text.translate_common`, còn lời nhắc dịch dùng chung
:func:`autodub.text.translate_hint.build_translation_prompt`.

Đầu ra được ép theo ``response_schema`` — mô hình chỉ được trả
``{id, text_vi}`` cho mỗi câu (không lặp lại các trường nguồn), nhờ vậy số
token đầu ra giảm khoảng 4 lần.

Ba cái bẫy của dòng Gemini 2.5 đều được xử lý ở đây, vì cả ba đều biểu hiện
giống hệt "API Key đúng mà gọi không được":

1. Các model 2.5 mặc định BẬT suy nghĩ. Suy nghĩ tiêu tốn chính hạn mức
   ``max_output_tokens``, nên trần thấp làm câu trả lời rỗng trong khi API
   vẫn báo thành công. Ở đây suy nghĩ luôn bị tắt cho việc dịch (dịch là việc
   máy móc), và nếu model từ chối tham số đó thì trần token được nâng lên.
2. ``response.text`` trả về None khi phản hồi bị chặn hoặc bị cắt — cần đọc
   ``finish_reason`` mới biết nguyên nhân thật.
3. API Key dán từ trình duyệt hay dính dấu nháy hoặc khoảng trắng.
"""
from __future__ import annotations

import json
import time

from autodub.languages import TargetLang
from autodub.progress import ProgressReporter
from autodub.text.translate_common import (
    RateLimited,
    TranslateError,
    contains_cjk,
    merge_translations,
    parse_response_segments,
)
from autodub.text.translate_hint import (
    build_translation_prompt,
    context_note,
    context_payload,
    effective_cps,
    payload_segment,
)
from autodub.utils import setup_logging

logger = setup_logging("autodub.translate_gemini")

# Khoảng cách tối thiểu giữa hai lần BẮT ĐẦU gửi lô — các model flash bản
# miễn phí bị giới hạn số request mỗi phút (~15), nên các lần bắt đầu được
# giãn ra; các request vẫn chồng lấn khi đang bay, đó là chỗ nhanh lên.
_BATCH_SLEEP = 4.0

# Trần token đầu ra. Một lô 40 câu tiếng Việt hiếm khi quá 8k token, nhưng
# đặt rộng thì không bao giờ bị cắt giữa chừng (đây là TRẦN, không phải mức
# tiêu thụ — đặt cao không tốn thêm tiền).
_MAX_OUTPUT_TOKENS = 32768

_HTTP_TIMEOUT_MS = 180_000

_clients: dict = {}


def clean_key(api_key: str) -> str:
    """Bỏ khoảng trắng và dấu nháy dính theo khi dán API Key."""
    return (api_key or "").strip().strip("'\"").strip()


def _client(api_key: str):
    """Client google-genai dùng lại theo từng API Key."""
    api_key = clean_key(api_key)
    if api_key not in _clients:
        from google import genai
        from google.genai import types
        _clients[api_key] = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=_HTTP_TIMEOUT_MS))
    return _clients[api_key]


# ------------------------------------------------------------ gọi mô hình -- #

def _status_code(exc) -> int:
    """Mã HTTP của một google.genai APIError (0 nếu không rõ)."""
    return int(getattr(exc, "code", 0) or getattr(exc, "status_code", 0) or 0)


def _build_config(types, system: str, schema, max_output_tokens: int,
                  no_thinking: bool):
    kwargs = dict(
        system_instruction=system,
        temperature=0.3,
        max_output_tokens=max_output_tokens,
    )
    if schema is not None:
        kwargs["response_mime_type"] = "application/json"
        kwargs["response_schema"] = schema
    if no_thinking:
        # Dịch là việc máy móc — suy nghĩ chỉ tốn thêm token và thời gian, mà
        # token suy nghĩ lại ăn vào chính trần đầu ra.
        kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    return types.GenerateContentConfig(**kwargs)


def _reply_text(resp) -> str:
    """Phần chữ của một phản hồi, kèm chẩn đoán khi phản hồi rỗng.

    ``resp.text`` là None (hoặc ném lỗi) khi model không sinh ra phần chữ
    nào: bị chặn vì an toàn, chạm trần token, hoặc dùng hết trần cho suy
    nghĩ. Đọc ``finish_reason`` mới nói được nguyên nhân thật cho người dùng.
    """
    try:
        text = (resp.text or "").strip()
    except Exception:      # SDK ném khi phản hồi không có phần chữ nào
        text = ""
    if text:
        return text

    block = getattr(getattr(resp, "prompt_feedback", None), "block_reason", None)
    if block:
        raise TranslateError(
            f"Gemini từ chối nội dung này ({block}). Thử đổi sang mô hình "
            "khác hoặc dịch bằng nơi khác trong Cài đặt.")

    candidates = getattr(resp, "candidates", None) or []
    reason = str(getattr(candidates[0], "finish_reason", "")) if candidates else ""
    if "MAX_TOKENS" in reason:
        raise TranslateError(
            "Gemini chạm trần token đầu ra nên trả về câu trả lời rỗng — "
            "giảm «Số câu mỗi lượt gửi» trong Cài đặt rồi thử lại.")
    if "SAFETY" in reason or "RECITATION" in reason:
        raise TranslateError(
            f"Gemini chặn câu trả lời ({reason}). Thử mô hình khác hoặc đổi "
            "nơi dịch trong Cài đặt.")
    raise TranslateError(
        "Gemini trả về nội dung rỗng"
        + (f" (lý do: {reason})" if reason else "")
        + ". Thường là do mô hình đang chọn không tồn tại hoặc tài khoản chưa "
          "bật quyền dùng mô hình đó.")


def _generate(api_key: str, model: str, system: str, user: str,
              max_retries: int, schema=None,
              max_output_tokens: int = _MAX_OUTPUT_TOKENS) -> str:
    """Một lượt generate_content có thử lại. Trả về phần chữ trả lời.

    429 → chờ tăng dần (không bao giờ chia lô); 5xx → chờ ngắn hơn;
    400/403/404 → báo lỗi ngay kèm việc cần làm.
    """
    from google.genai import errors, types

    api_key = clean_key(api_key)
    if not api_key:
        raise TranslateError(
            "Chưa có API Key Gemini. Lấy miễn phí tại "
            "aistudio.google.com/apikey rồi dán vào trang Cài đặt.")
    model = (model or "").strip()
    if not model:
        raise TranslateError("Chưa chọn mô hình Gemini trong trang Cài đặt.")

    client = _client(api_key)
    no_thinking = True
    last_error = ""
    attempt = 0
    while attempt < max(1, max_retries):
        config = _build_config(types, system, schema, max_output_tokens,
                               no_thinking)
        try:
            resp = client.models.generate_content(
                model=model, contents=user, config=config)
            return _reply_text(resp)
        except errors.APIError as e:
            code = _status_code(e)
            msg = str(e)
            if code == 429:
                if attempt == max_retries - 1:
                    raise RateLimited(
                        "Gemini hết lượt miễn phí (HTTP 429). Đợi vài phút rồi "
                        "thử lại, hoặc chọn nơi dịch khác trong Cài đặt."
                    ) from e
                wait = min(2 ** attempt * 5, 60)
                logger.warning(f"Gemini đang giới hạn tốc độ — chờ {wait}s rồi "
                               f"thử lại (lần {attempt + 1}/{max_retries})")
                time.sleep(wait)
            elif code == 404:
                raise TranslateError(
                    f"Gemini không dùng được mô hình «{model}» (mô hình cũ đã "
                    "ngừng phục vụ, hoặc gõ sai tên). "
                    + _suggest_models(api_key)
                ) from e
            elif code in (401, 403):
                raise TranslateError(
                    "API Key Gemini không hợp lệ hoặc chưa có quyền dùng "
                    "mô hình này. Lấy mã miễn phí tại "
                    "aistudio.google.com/apikey."
                ) from e
            elif code == 400:
                # Dòng Gemini 3 BẮT BUỘC phải suy nghĩ nên từ chối
                # thinking_config=0, nhưng lời báo lỗi chỉ nói chung chung
                # "invalid argument". Vì vậy cứ gặp 400 khi còn đang tắt suy
                # nghĩ là bỏ tham số đó ra rồi thử lại, không dò chữ trong
                # thông báo — dò chữ là cách hỏng thầm lặng mỗi khi Google
                # đổi câu chữ. Trần token phải nới ra vì phần suy nghĩ ăn vào
                # chính trần đó.
                if no_thinking:
                    logger.info(f"Mô hình «{model}» không cho tắt suy nghĩ — "
                                "gọi lại với chế độ mặc định")
                    no_thinking = False
                    max_output_tokens = max(max_output_tokens, 8192) * 2
                    continue
                raise TranslateError(
                    f"Gemini từ chối yêu cầu: {msg[:200]}") from e
            else:                       # 5xx hoặc không rõ
                last_error = f"HTTP {code}: {msg[:120]}"
                if attempt == max_retries - 1:
                    break
                wait = min(2 ** attempt * 3, 30)
                logger.warning(f"Gemini lỗi máy chủ, thử lại sau {wait}s: "
                               f"{last_error}")
                time.sleep(wait)
        except TranslateError:
            raise
        except Exception as e:
            # Thư viện cũng có thể từ chối thinking_config ngay ở lớp kiểm
            # tra tham số, trước khi có bất kỳ lần gọi mạng nào.
            if no_thinking and "thinking" in str(e).lower():
                no_thinking = False
                max_output_tokens = max(max_output_tokens, 8192) * 2
                continue
            last_error = f"{type(e).__name__}: {e}"
            if attempt == max_retries - 1:
                break
            time.sleep(min(2 ** attempt * 3, 30))
        attempt += 1

    raise TranslateError(f"Không gọi được Gemini sau {max_retries} lần — "
                         f"{last_error}")


def check_model(api_key: str, model: str) -> tuple[bool, str]:
    """Thử gọi một câu ngắn để kiểm tra cấu hình. Trả về (ổn, lời nhắn).

    Trần token đặt rộng rãi: đặt sát (kiểu 10 token) thì model dòng 2.5 trả
    về rỗng và phép thử báo hỏng dù API Key hoàn toàn đúng.
    """
    try:
        text = _generate(api_key, model,
                         system="Reply with the single word: ok",
                         user="ok?", max_retries=1, max_output_tokens=512)
        return True, f"Gọi được mô hình «{model}», trả lời: {text.strip()[:40]}"
    except TranslateError as e:
        return False, str(e)
    except Exception as e:      # thiếu thư viện, API Key sai định dạng...
        return False, f"{type(e).__name__}: {e}"


def _suggest_models(api_key: str, limit: int = 6) -> str:
    """Câu gợi ý kèm các mô hình mà chính API Key này đang dùng được.

    Google ngừng phục vụ mô hình cũ khá thường xuyên, nên báo "sai tên mô
    hình" không thôi là bỏ mặc người dùng. Hỏi thẳng máy chủ xem tài khoản
    này có gì rồi đọc tên ra thì họ sửa được ngay.
    """
    names = [n for n in list_models(api_key)
             if n.startswith("gemini") and "flash" in n
             and not any(x in n for x in ("image", "tts", "computer", "omni"))]
    if not names:
        return "Mở trang Cài đặt, thẻ Kết nối để chọn mô hình khác."
    return ("Mô hình API Key này đang dùng được: "
            + ", ".join(names[:limit]) + ".")


def list_models(api_key: str) -> list[str]:
    """Các mô hình sinh nội dung mà API Key này dùng được (rỗng nếu lỗi)."""
    try:
        client = _client(api_key)
        names = []
        for m in client.models.list():
            actions = getattr(m, "supported_actions", None) or []
            if actions and "generateContent" not in actions:
                continue
            name = str(getattr(m, "name", "")).removeprefix("models/")
            if name:
                names.append(name)
        return sorted(names)
    except Exception:
        return []


# --------------------------------------------------------------- dịch ----- #

def _response_schema(text_field: str):
    """Lược đồ ép đúng ``{"segments": [{"id", <text_field>}, ...]}``."""
    from google.genai import types

    return types.Schema(
        type=types.Type.OBJECT,
        required=["segments"],
        properties={
            "segments": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    required=["id", text_field],
                    properties={
                        "id": types.Schema(type=types.Type.INTEGER),
                        text_field: types.Schema(type=types.Type.STRING),
                    },
                ),
            )
        },
    )


def translate_batch(batch: list[dict], target: TargetLang, source_lang: str,
                    settings, context: list[dict] | None = None) -> list[dict]:
    """Dịch một lô câu; trả về chính lô đó đã có ``target.text_field``."""
    cps = effective_cps(settings)
    payload_segments = [payload_segment(seg, cps) for seg in batch]
    system_prompt = build_translation_prompt(target, source_lang,
                                             cps_budget=cps, settings=settings)
    ctx_part = ""
    if context:
        ctx_part = (context_note(target) + '"context": '
                    + json.dumps(context, ensure_ascii=False) + "\n\n")
    user_prompt = (
        'Translate these segments. Return ONLY JSON of the form '
        f'{{"segments": [...]}} with the same length, order and ids, each '
        f'segment as {{"id": ..., "{target.text_field}": "..."}}.\n\n'
        + ctx_part
        + json.dumps(payload_segments, ensure_ascii=False)
    )

    content = _generate(
        settings.google_api_key, settings.gemini_translate_model,
        system_prompt, user_prompt, settings.translate_max_retries,
        schema=_response_schema(target.text_field),
    )
    returned = parse_response_segments(content)
    merged = merge_translations(batch, returned, target.text_field)
    return _fix_cjk_leftovers(merged, target, source_lang, settings)


def _fix_cjk_leftovers(merged: list[dict], target: TargetLang,
                       source_lang: str, settings) -> list[dict]:
    """Dịch lại những câu còn sót chữ Hán (hiếm với Gemini nhưng vẫn phải chặn)."""
    bad = [s for s in merged if contains_cjk(s[target.text_field])]
    if not bad:
        return merged

    logger.warning(f"{len(bad)} câu dịch còn lẫn tiếng Trung — dịch lại song song")
    system_prompt = build_translation_prompt(target, source_lang,
                                             settings=settings)

    def _retry_one(seg: dict) -> tuple[int, str | None]:
        user_prompt = (
            'Your previous translation still contained Chinese characters: '
            f'{json.dumps(seg[target.text_field], ensure_ascii=False)}\n'
            f'Translate this ONE segment again. "{target.text_field}" must be '
            'pure Vietnamese (Latin script only). Return ONLY JSON: '
            f'{{"segments": [{{"id": ..., "{target.text_field}": "..."}}]}}\n\n'
            + json.dumps(payload_segment(seg), ensure_ascii=False)
        )
        try:
            content = _generate(
                settings.google_api_key, settings.gemini_translate_model,
                system_prompt, user_prompt, max_retries=2,
                schema=_response_schema(target.text_field),
            )
            returned = parse_response_segments(content)
            redone = merge_translations([seg], returned, target.text_field)[0]
            if not contains_cjk(redone[target.text_field]):
                return seg["id"], redone[target.text_field]
        except TranslateError as e:
            logger.warning(f"Dịch lại câu {seg['id']} lỗi ({str(e)[:60]}) — "
                           "giữ bản cũ")
        return seg["id"], None

    from concurrent.futures import ThreadPoolExecutor

    workers = min(max(1, int(settings.parallel_workers)), len(bad), 4)
    fixed: dict = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for seg_id, text in pool.map(_retry_one, bad):
            if text is not None:
                fixed[seg_id] = text

    still_bad = len(bad) - len(fixed)
    if still_bad:
        logger.warning(f"{still_bad} câu vẫn còn lẫn tiếng Trung sau khi dịch "
                       "lại — giữ nguyên (giọng đọc sẽ bỏ qua ký tự Trung)")
    return [
        {**s, target.text_field: fixed.get(s["id"], s[target.text_field])}
        for s in merged
    ]


def _translate_resilient(batch: list[dict], target: TargetLang,
                         source_lang: str, settings,
                         context: list[dict] | None = None) -> list[dict]:
    """Dịch một lô, chia đôi khi mô hình nghẹn vì lô quá lớn."""
    try:
        return translate_batch(batch, target, source_lang, settings, context)
    except RateLimited:
        raise
    except TranslateError as e:
        if len(batch) == 1:
            raise
        mid = len(batch) // 2
        logger.warning(f"Lô {len(batch)} câu lỗi ({str(e)[:80]}) — chia đôi và "
                       "thử lại")
        left = _translate_resilient(batch[:mid], target, source_lang, settings,
                                    context)
        right = _translate_resilient(batch[mid:], target, source_lang, settings,
                                     context_payload(batch, mid))
        return left + right


def translate_segments(segments: list[dict], target: TargetLang,
                       source_lang: str, settings,
                       reporter: ProgressReporter | None = None) -> list[dict]:
    """Dịch toàn bộ câu, theo từng lô, bằng Gemini."""
    if not segments:
        raise TranslateError("Không có câu nào để dịch")

    batch_size = max(1, int(settings.translate_batch_size))
    total = len(segments)
    batches = [segments[i:i + batch_size] for i in range(0, total, batch_size)]
    workers = min(max(1, int(settings.parallel_workers)), len(batches), 4)
    logger.info(f"Đang dịch {total} câu bằng Gemini "
                f"({settings.gemini_translate_model}, mỗi lượt {batch_size} "
                f"câu, {workers} lượt song song)")

    import threading
    from concurrent.futures import ThreadPoolExecutor

    pace_lock = threading.Lock()
    next_start = [0.0]

    def _paced(batch: list[dict], ctx: list[dict]) -> list[dict]:
        with pace_lock:
            wait = next_start[0] - time.monotonic()
            next_start[0] = max(next_start[0], time.monotonic()) + _BATCH_SLEEP
        if wait > 0:
            time.sleep(wait)
        return _translate_resilient(batch, target, source_lang, settings, ctx)

    done_count = 0
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [
            pool.submit(_paced, b, context_payload(segments, i * batch_size))
            for i, b in enumerate(batches)
        ]
        results: list[list[dict]] = []
        for i, fut in enumerate(futures):
            if reporter is not None:
                reporter.check_cancelled()
            results.append(fut.result())      # giữ nguyên thứ tự lô
            done_count += len(batches[i])
            logger.info(f"  Đã dịch {done_count}/{total} câu")
            if reporter is not None:
                reporter.emit("translate", "progress",
                              current=done_count, total=total)
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        pool.shutdown(wait=True)

    return [seg for batch in results for seg in batch]
