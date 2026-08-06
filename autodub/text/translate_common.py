"""Phần dùng chung của mọi nơi dịch: kiểu lỗi, đọc JSON và ghép bản dịch.

Nơi dịch nào cũng nhận về một khối JSON do mô hình sinh ra, nên phần "đọc cho
bằng được rồi ghép vào đúng câu" là giống hệt nhau. Gom về đây để Gemini và
các dịch vụ tương thích OpenAI dùng chung một cách xử lý — sửa một chỗ là mọi
nơi cùng đúng.
"""
from __future__ import annotations

import json
import re

from autodub.utils import setup_logging

logger = setup_logging("autodub.translate")

# Chữ Hán (kể cả phần mở rộng A) — bản dịch còn ký tự này là dịch chưa xong.
_CJK_RE = re.compile(r"[㐀-䶿一-鿿]")

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class TranslateError(Exception):
    """Nơi dịch không trả về kết quả dùng được."""


class RateLimited(TranslateError):
    """Hết hạn mức (HTTP 429) sau khi đã thử lại.

    KHÔNG được chia đôi lô khi gặp lỗi này: chia đôi làm số request tăng gấp
    đôi, đúng thứ mà giới hạn tốc độ đang chặn.
    """


def contains_cjk(text: str) -> bool:
    """Chuỗi này còn sót chữ Hán hay không."""
    return bool(_CJK_RE.search(str(text or "")))


def strip_fences(text: str) -> str:
    """Bỏ khối ```json ... ``` mà một số mô hình vẫn bọc quanh câu trả lời."""
    text = str(text or "").strip()
    text = _FENCE_RE.sub("", text)
    return text.strip()


def _slice_to_payload(text: str) -> str:
    """Cắt lấy phần từ dấu mở ngoặc đầu tiên tới dấu đóng cuối cùng.

    Mô hình hay chèn thêm một câu dẫn ("Here is the JSON:") hoặc một dòng kết.
    Phần JSON thật luôn nằm giữa cặp ngoặc ngoài cùng.
    """
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        return text
    start = min(starts)
    end = max(text.rfind("}"), text.rfind("]"))
    return text[start:end + 1] if end > start else text[start:]


def repair_json(text: str) -> str:
    """Vá một khối JSON bị cắt giữa chừng để còn đọc được phần đã có.

    Câu trả lời chạm trần token bị đứt ngang: có thể đứt giữa một chuỗi, và
    chắc chắn thiếu các dấu đóng ngoặc. Hàm này đóng nốt chúng theo đúng thứ
    tự đã mở. Câu cuối cùng bị đứt sẽ hỏng, nhưng phần trước đó vẫn cứu được.
    """
    text = _slice_to_payload(strip_fences(text)).rstrip()
    if not text:
        return text

    stack: list[str] = []
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()

    if in_string:
        text += '"'
    # Bỏ phần đuôi dở dang: dấu phẩy treo, một khóa chưa có giá trị, hoặc
    # một khóa bị đứt trước cả dấu hai chấm (vd ``{"id": 78, "text_``).
    text = re.sub(r",\s*$", "", text)
    text = re.sub(r',\s*"[^"]*"\s*:?\s*$', "", text)
    text = re.sub(r'\{\s*"[^"]*"\s*:?\s*$', "{", text)
    return text + "".join(reversed(stack))


def parse_response_segments(content: str) -> list[dict]:
    """Đọc câu trả lời của mô hình thành danh sách câu.

    Chấp nhận cả ``{"segments": [...]}`` lẫn một mảng trần, có hay không có
    khối ```json bọc ngoài. Hỏng hoàn toàn thì ném :class:`TranslateError`
    kèm một mẩu nội dung để người dùng còn biết chuyện gì xảy ra.
    """
    raw = strip_fences(content)
    for candidate in (raw, _slice_to_payload(raw), repair_json(raw)):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            data = data.get("segments", data.get("data", []))
        if isinstance(data, list):
            return [s for s in data if isinstance(s, dict)]
    raise TranslateError(
        "Không đọc được kết quả dịch (JSON hỏng): "
        + raw[:200].replace("\n", " ")
    )


def merge_translations(batch: list[dict], returned: list[dict],
                       text_field: str) -> list[dict]:
    """Ghép bản dịch trả về vào đúng câu gốc, theo ``id``.

    Trả về danh sách BẢN SAO của ``batch`` đã có thêm ``text_field``; câu gốc
    không bị đụng tới, nên một lô hỏng giữa chừng không làm bẩn dữ liệu.
    Thiếu câu nào thì ném lỗi để lớp trên chia đôi lô rồi thử lại.
    """
    from autodub.text.translate_hint import ensure_terminal_punct

    by_id: dict = {}
    for item in returned:
        seg_id = item.get("id")
        text = str(item.get(text_field, "") or "").strip()
        if seg_id is None or not text:
            continue
        try:
            by_id[int(seg_id)] = text
        except (TypeError, ValueError):
            by_id[str(seg_id)] = text

    # Mô hình bỏ mất id nhưng trả đúng số câu, đúng thứ tự — chấp nhận và
    # ghép theo vị trí, còn hơn ném đi cả một lô đã dịch xong.
    if not by_id and len(returned) == len(batch):
        by_id = {int(seg.get("id")): str(item.get(text_field, "") or "").strip()
                 for seg, item in zip(batch, returned)
                 if str(item.get(text_field, "") or "").strip()}

    merged: list[dict] = []
    missing: list = []
    for seg in batch:
        seg_id = seg.get("id")
        text = by_id.get(seg_id)
        if text is None:
            try:
                text = by_id.get(int(seg_id))
            except (TypeError, ValueError):
                text = None
        if not text:
            missing.append(seg_id)
            continue
        merged.append({**seg, text_field: ensure_terminal_punct(text)})

    if missing:
        raise TranslateError(
            f"Bản dịch thiếu {len(missing)} câu (id: {missing[:10]}"
            f"{'...' if len(missing) > 10 else ''})"
        )
    return merged
