"""Dịch qua các dịch vụ dùng chuẩn OpenAI ``/chat/completions``.

Một module lo cho tất cả: OpenRouter, OpenAI, Anthropic, DeepSeek và mục
"Khác" (bạn tự điền địa chỉ, mô hình, API Key). Cả bốn nơi có sẵn đều nói
cùng một giao thức nên chỉ khác nhau ở địa chỉ mặc định và vài dòng tiêu đề
HTTP; Gemini có đường đi riêng trong :mod:`autodub.text.translate_gemini`.

Mọi lỗi đều ném :class:`~autodub.text.translate_common.TranslateError`; lớp
pipeline bắt lại và chuyển sang luồng dịch tay nên một lần hết hạn mức không
bao giờ làm mất công đã nghe và chép lời.
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

logger = setup_logging("autodub.translate_openai")

#: Tên hiển thị và nơi lấy API Key của từng dịch vụ.
PROVIDERS: dict[str, dict[str, str]] = {
    "openrouter": {"label": "OpenRouter", "keys": "openrouter.ai/keys"},
    "openai": {"label": "OpenAI", "keys": "platform.openai.com/api-keys"},
    "anthropic": {"label": "Anthropic", "keys": "console.anthropic.com/settings/keys"},
    "deepseek": {"label": "DeepSeek", "keys": "platform.deepseek.com/api_keys"},
    "custom": {"label": "Dịch vụ khác", "keys": "trang quản trị của dịch vụ đó"},
}

_DEFAULT_TIMEOUT = 180.0


def label_of(engine: str) -> str:
    return PROVIDERS.get(engine, {}).get("label", engine)


def _headers(engine: str, api_key: str) -> dict[str, str]:
    """Tiêu đề HTTP cho một dịch vụ.

    Tất cả đều nhận ``Authorization: Bearer``. Anthropic thêm ``x-api-key`` +
    ``anthropic-version`` vì lớp tương thích của họ chấp nhận cả hai cách, và
    OpenRouter khuyến nghị khai báo nguồn gọi để xếp hạng ứng dụng.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if engine == "anthropic":
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    elif engine == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/voxdub-studio"
        headers["X-Title"] = "VoxDub Studio"
    return headers


# ------------------------------------------------------------ gọi mô hình -- #

def chat(settings, engine: str, system: str, user: str, max_retries: int,
         schema: dict | None = None, timeout: float = _DEFAULT_TIMEOUT) -> str:
    """Một lượt gọi ``/chat/completions`` có thử lại. Trả về phần chữ trả lời.

    429 → chờ tăng dần rồi thử lại (không bao giờ chia lô); 5xx → chờ ngắn
    hơn; 401/403/404/402 → báo lỗi ngay kèm việc cần làm.

    Có ``schema`` thì yêu cầu đầu ra JSON theo lược đồ chặt (``json_schema``),
    nhà cung cấp sẽ ép mô hình sinh đúng JSON hợp lệ. Dịch vụ nào không nhận
    tham số này (HTTP 400) sẽ tự động hạ xuống chế độ JSON thường một lần.
    """
    import requests

    api_key, url, model = settings.translate_credentials(engine)
    name = label_of(engine)
    if not api_key:
        raise TranslateError(
            f"Chưa có API Key {name}. Lấy tại "
            f"{PROVIDERS.get(engine, {}).get('keys', '')}.")
    if not url or not model:
        raise TranslateError(
            f"Chưa điền địa chỉ máy chủ hoặc tên mô hình cho {name} "
            "trong trang Cài đặt.")

    if schema is not None:
        response_format = {"type": "json_schema", "json_schema": {
            "name": "segments", "strict": True, "schema": schema}}
    else:
        response_format = {"type": "json_object"}
    payload = {
        "model": model,
        "temperature": 0.3,
        # Trần đầu ra rộng: một lô 40 câu hiếm khi quá 8k token, nhưng nhiều
        # nhà cung cấp đặt trần mặc định thấp — JSON bị cắt giữa chừng, lô
        # "thiếu câu" rồi chia đôi mãi. Đây là TRẦN, không phải mức tiêu thụ.
        "max_tokens": 16384,
        "response_format": response_format,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if engine == "openrouter":
        # Các model có "suy nghĩ" (Gemini 2.5, o-series...) tiêu token suy
        # nghĩ vào chính trần đầu ra — tắt đi cho việc dịch máy móc, khỏi bị
        # cắt JSON. Model không hỗ trợ thì OpenRouter tự bỏ qua trường này.
        payload["reasoning"] = {"enabled": False}
    headers = _headers(engine, api_key)

    last_error = ""
    attempt = 0
    while attempt < max(1, max_retries):
        try:
            resp = requests.post(url, json=payload, headers=headers,
                                 timeout=timeout)
        except requests.exceptions.RequestException as e:
            last_error = f"{type(e).__name__}: {e}"
        else:
            if resp.status_code == 200:
                return _read_reply(resp, name)
            if resp.status_code == 429:
                if attempt == max_retries - 1:
                    raise RateLimited(
                        f"{name} hết hạn mức (HTTP 429). Đợi vài phút rồi thử "
                        "lại, nạp thêm hạn mức, hoặc chọn nơi dịch khác trong "
                        "Cài đặt.")
                wait = min(2 ** attempt * 5, 60)
                logger.warning(f"{name} đang giới hạn tốc độ — chờ {wait}s rồi "
                               f"thử lại (lần {attempt + 1}/{max_retries})")
                time.sleep(wait)
                attempt += 1
                continue
            if resp.status_code in (401, 403):
                raise TranslateError(
                    f"API Key {name} không hợp lệ hoặc đã hết hạn. Lấy mã "
                    f"mới tại {PROVIDERS.get(engine, {}).get('keys', '')}.")
            if (resp.status_code == 400
                    and payload["response_format"]["type"] == "json_schema"):
                # Mô hình không hỗ trợ đầu ra theo lược đồ — hạ xuống JSON
                # thường rồi thử lại ngay, không tính là một lần thử.
                logger.warning(f"Mô hình «{model}» không nhận đầu ra theo lược "
                               "đồ — chuyển sang JSON thường")
                payload["response_format"] = {"type": "json_object"}
                continue
            if resp.status_code == 404:
                raise TranslateError(
                    f"{name} không có mô hình «{model}» (hoặc sai địa chỉ máy "
                    "chủ). Kiểm tra lại hai ô đó trong trang Cài đặt.")
            if resp.status_code == 402:
                raise TranslateError(
                    f"Tài khoản {name} đã hết hạn mức trả trước — nạp thêm "
                    "hoặc chọn mô hình miễn phí.")
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"

        if attempt == max_retries - 1:
            break
        wait = min(2 ** attempt * 3, 30)
        logger.warning(f"{name} lỗi, thử lại sau {wait}s: {last_error[:120]}")
        time.sleep(wait)
        attempt += 1

    raise TranslateError(f"Không gọi được {name} sau {max_retries} lần — "
                         f"{last_error}")


def _read_reply(resp, name: str) -> str:
    """Lấy phần chữ trong một phản hồi 200, kèm các lỗi thường gặp."""
    try:
        choice = resp.json()["choices"][0]
        content = choice["message"]["content"]
    except (ValueError, KeyError, IndexError) as e:
        raise TranslateError(
            f"{name} trả về phản hồi không đúng định dạng: {e}") from e
    if not str(content).strip():
        raise TranslateError(f"{name} trả về nội dung rỗng")
    if choice.get("finish_reason") == "length":
        # Bị cắt vì chạm trần token đầu ra ⇒ JSON chắc chắn hỏng. Không thử
        # lại nguyên trạng; báo lỗi để lớp trên chia đôi lô (đầu vào nhỏ hơn
        # thì đầu ra ngắn hơn và lọt dưới trần).
        raise TranslateError(
            f"{name} cắt phản hồi giữa chừng (chạm trần token đầu ra)")
    return str(content)


def check_model(settings, engine: str) -> tuple[bool, str]:
    """Thử gọi một câu ngắn để kiểm tra cấu hình. Trả về (ổn, lời nhắn)."""
    _key, url, model = settings.translate_credentials(engine)
    try:
        text = chat(settings, engine,
                    system="Reply with the single word: ok",
                    user="ok?", max_retries=1, timeout=30.0)
        return True, (f"Gọi được mô hình «{model}», trả lời: "
                      f"{text.strip()[:40]}")
    except TranslateError as e:
        return False, str(e)
    except Exception as e:  # thiếu thư viện, địa chỉ sai định dạng...
        return False, f"{type(e).__name__}: {e}"
    finally:
        del url


# --------------------------------------------------------------- dịch ----- #

def response_schema(text_field: str) -> dict:
    """Lược đồ ép đúng ``{"segments": [{"id", <text_field>}, ...]}``."""
    return {
        "type": "object",
        "properties": {
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        text_field: {"type": "string"},
                    },
                    "required": ["id", text_field],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["segments"],
        "additionalProperties": False,
    }


def translate_batch(batch: list[dict], target: TargetLang, source_lang: str,
                    settings, engine: str,
                    context: list[dict] | None = None) -> list[dict]:
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

    content = chat(settings, engine, system_prompt, user_prompt,
                   settings.translate_max_retries,
                   schema=response_schema(target.text_field))
    returned = parse_response_segments(content)
    merged = merge_translations(batch, returned, target.text_field)
    return _fix_cjk_leftovers(merged, target, source_lang, settings, engine)


def _fix_cjk_leftovers(merged: list[dict], target: TargetLang, source_lang: str,
                       settings, engine: str) -> list[dict]:
    """Dịch lại những câu còn sót chữ Hán.

    Chạy song song: với mô hình yếu, số câu sót có thể lên vài chục và dịch
    lại tuần tự sẽ biến 10 giây thành nhiều phút. Câu nào vẫn hỏng sau lượt
    này thì giữ nguyên bản lẫn lộn (giọng đọc sẽ bỏ qua ký tự lạ) thay vì làm
    hỏng cả lô.
    """
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
            content = chat(settings, engine, system_prompt, user_prompt,
                           max_retries=2,
                           schema=response_schema(target.text_field))
            returned = parse_response_segments(content)
            redone = merge_translations([seg], returned, target.text_field)[0]
            if not contains_cjk(redone[target.text_field]):
                return seg["id"], redone[target.text_field]
        except TranslateError as e:
            logger.warning(f"Dịch lại câu {seg['id']} lỗi ({str(e)[:60]}) — "
                           "giữ bản cũ")
        return seg["id"], None

    from concurrent.futures import ThreadPoolExecutor

    workers = min(max(1, int(settings.parallel_workers)), len(bad), 8)
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
                         source_lang: str, settings, engine: str,
                         context: list[dict] | None = None) -> list[dict]:
    """Dịch một lô, chia đôi khi mô hình nghẹn vì lô quá lớn.

    Trường hợp hết hạn mức được loại trừ: chia đôi làm số request tăng gấp
    đôi, đúng thứ đang bị chặn — lỗi đó được đẩy lên cho lớp trên xử lý.
    """
    try:
        return translate_batch(batch, target, source_lang, settings, engine,
                               context)
    except RateLimited:
        raise
    except TranslateError as e:
        if len(batch) == 1:
            raise
        mid = len(batch) // 2
        logger.warning(f"Lô {len(batch)} câu lỗi ({str(e)[:80]}) — chia đôi và "
                       "thử lại")
        left = _translate_resilient(batch[:mid], target, source_lang, settings,
                                    engine, context)
        right = _translate_resilient(batch[mid:], target, source_lang, settings,
                                     engine, context_payload(batch, mid))
        return left + right


def translate_segments(segments: list[dict], target: TargetLang,
                       source_lang: str, settings,
                       reporter: ProgressReporter | None = None,
                       engine: str | None = None) -> list[dict]:
    """Dịch toàn bộ câu, theo từng lô.

    Trả về danh sách mới đã có ``target.text_field``. Không ghi gì xuống đĩa —
    lớp gọi chỉ lưu khi cả lượt dịch thành công, nên hỏng giữa chừng không để
    lại thư mục dự án dở dang.
    """
    if not segments:
        raise TranslateError("Không có câu nào để dịch")

    engine = engine or settings.translate_engine
    name = label_of(engine)
    _key, _url, model = settings.translate_credentials(engine)

    batch_size = max(1, int(settings.translate_batch_size))
    total = len(segments)
    batches = [segments[i:i + batch_size] for i in range(0, total, batch_size)]
    # Các lô độc lập nhau — gửi song song thay vì chờ từng lô. Trần 8 để vẫn
    # nằm dưới giới hạn tốc độ của các gói miễn phí (~20 request/phút).
    workers = min(max(1, int(settings.parallel_workers)), len(batches), 8)
    logger.info(f"Đang dịch {total} câu bằng {name} ({model}, mỗi lượt "
                f"{batch_size} câu, {workers} lượt song song)")

    from concurrent.futures import ThreadPoolExecutor

    done_count = 0
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        # Chạy song song nên chưa có bản dịch của lô trước — kèm 3 câu GỐC
        # liền trước làm ngữ cảnh để không đứt mạch giữa hai lô.
        futures = [
            pool.submit(_translate_resilient, b, target, source_lang, settings,
                        engine, context_payload(segments, i * batch_size))
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
        # Hủy hoặc lỗi: không chờ các lô đang bay — trả điều khiển về ngay.
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        pool.shutdown(wait=True)

    return [seg for batch in results for seg in batch]
