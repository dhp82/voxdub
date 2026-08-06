"""Danh mục giọng đọc VieNeu — nguồn dữ liệu DUY NHẤT cho mọi nơi chọn giọng.

Ứng dụng chỉ còn một bộ giọng (VieNeu), nên người dùng không chọn "nam/nữ"
nữa mà chọn thẳng TÊN giọng. Giới tính, vùng miền và quốc gia chỉ còn là bộ
lọc để tìm giọng cho nhanh.

Hai nguồn giọng được gộp lại:

1. **Giọng có sẵn** — 14 giọng đóng kèm model VieNeu v3-Turbo. Bảng
   :data:`BUILTIN` giữ đầy đủ thông tin (giới tính, vùng miền, phong cách);
   nếu bản cài đặt sinh ra ``models/vieneu/voices.json`` với giọng lạ thì
   những giọng đó cũng được nạp thêm (đọc thông tin từ nhãn).
2. **Giọng bạn tự thêm** — ``models/vieneu/custom_voices.json``, do
   :mod:`autodub.speech.tts.vieneu_worker` ghi ra ở chế độ học giọng.

Module này KHÔNG nạp model và không phụ thuộc venv riêng của VieNeu, nên
giao diện gọi thoải mái.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

# --- Từ vựng dùng chung cho bộ lọc ----------------------------------------

GENDERS: tuple[tuple[str, str], ...] = (
    ("Nam", "male"),
    ("Nữ", "female"),
)

REGIONS: tuple[tuple[str, str], ...] = (
    ("Miền Bắc", "bac"),
    ("Miền Trung", "trung"),
    ("Miền Nam", "nam"),
)

COUNTRIES: tuple[tuple[str, str], ...] = (
    ("Việt Nam", "vn"),
    ("Mỹ", "us"),
    ("Anh", "uk"),
    ("Nhật Bản", "jp"),
    ("Hàn Quốc", "kr"),
    ("Trung Quốc", "cn"),
    ("Khác", "other"),
)

STYLES: tuple[tuple[str, str], ...] = (
    ("Tự nhiên", "tu_nhien"),
    ("Tin tức", "tin_tuc"),
    ("Kể chuyện", "doc_truyen"),
)

_GENDER_LABEL = dict((k, v) for v, k in GENDERS)
_REGION_LABEL = dict((k, v) for v, k in REGIONS)
_COUNTRY_LABEL = dict((k, v) for v, k in COUNTRIES)
_STYLE_LABEL = dict((k, v) for v, k in STYLES)

# Nhãn giọng của VieNeu viết vùng miền bằng tiếng Việt có dấu.
_REGION_FROM_TEXT = {"bắc": "bac", "trung": "trung", "nam": "nam"}
_STYLE_FROM_TEXT = {
    "tin tức": "tin_tuc",
    "kể chuyện": "doc_truyen",
    "tự nhiên": "tu_nhien",
}


@dataclass(frozen=True)
class Voice:
    """Một giọng đọc: tên là định danh, phần còn lại chỉ để lọc và hiển thị."""

    name: str
    gender: str = ""          # "male" | "female" | ""
    region: str = ""          # "bac" | "trung" | "nam" | ""
    country: str = "vn"       # khóa trong COUNTRIES
    style: str = "tu_nhien"   # khóa trong STYLES
    description: str = ""
    #: "builtin" (đóng kèm model) | "library" (thư mục voices/) | "custom"
    #: (bạn tự học từ một đoạn ghi âm).
    source: str = "builtin"

    @property
    def custom(self) -> bool:
        """Giọng này do người dùng tự học hay không."""
        return self.source == "custom"

    @property
    def label(self) -> str:
        """Dòng chữ hiện trong ô chọn giọng."""
        parts = [
            _GENDER_LABEL.get(self.gender, ""),
            _REGION_LABEL.get(self.region, ""),
            _COUNTRY_LABEL.get(self.country, "") if self.country != "vn" else "",
            _STYLE_LABEL.get(self.style, ""),
        ]
        detail = " · ".join(p for p in parts if p)
        prefix = "Giọng bạn thêm · " if self.custom else ""
        return f"{self.name} — {prefix}{detail}" if detail else self.name

    def matches(self, gender: str = "", region: str = "",
                country: str = "", style: str = "", query: str = "") -> bool:
        """Giọng này có lọt qua bộ lọc đang chọn không (ô rỗng = không lọc)."""
        if gender and self.gender != gender:
            return False
        if region and self.region != region:
            return False
        if country and self.country != country:
            return False
        if style and self.style != style:
            return False
        if query and query.strip().lower() not in self.label.lower():
            return False
        return True


# --- 14 giọng đóng kèm model VieNeu v3-Turbo -------------------------------
# Khớp với assets/voices_v3_turbo.json của gói vieneu.
BUILTIN: tuple[Voice, ...] = (
    Voice("Minh Đức", "male", "bac", style="tin_tuc"),
    Voice("Phạm Tuyên", "male", "bac", style="tu_nhien"),
    Voice("Thanh Bình", "male", "bac", style="doc_truyen"),
    Voice("Thái Sơn", "male", "nam", style="doc_truyen"),
    Voice("Xuân Vĩnh", "male", "nam", style="tu_nhien"),
    Voice("Minh Triết", "male", "nam", style="tin_tuc"),
    Voice("Quang Sơn", "male", "trung", style="tu_nhien"),
    Voice("Trúc Ly", "female", "bac", style="tu_nhien"),
    Voice("Mai Anh", "female", "bac", style="tin_tuc"),
    Voice("Ngọc Linh", "female", "bac", style="doc_truyen"),
    Voice("Đoan Trang", "female", "bac", style="tu_nhien"),
    Voice("Thục Đoan", "female", "nam", style="doc_truyen"),
    Voice("Thùy Dung", "female", "nam", style="tin_tuc"),
    Voice("Ngọc Trân", "female", "trung", style="tu_nhien"),
)

#: Giọng dùng khi tệp cấu hình chưa chọn gì.
DEFAULT_VOICE = "Phạm Tuyên"


def _from_label(name: str, label: str) -> Voice:
    """Dựng một giọng từ nhãn kiểu «Nam · Bắc · Phong cách tin tức»."""
    text = (label or "").lower()
    gender = "female" if "nữ" in text else ("male" if "nam ·" in text
                                            or text.startswith("nam") else "")
    region = ""
    for word, key in _REGION_FROM_TEXT.items():
        # Vùng miền là mảnh GIỮA hai dấu chấm giữa, tránh nhầm với giới tính.
        if f"· {word}" in text:
            region = key
            break
    style = "tu_nhien"
    for word, key in _STYLE_FROM_TEXT.items():
        if word in text:
            style = key
            break
    return Voice(name, gender, region, style=style, description=label)


def _builtin_voices(model_dir: str) -> list[Voice]:
    """Giọng có sẵn: bảng tĩnh, cộng thêm giọng lạ tìm thấy trong voices.json."""
    voices = list(BUILTIN)
    known = {v.name for v in voices}
    path = os.path.join(model_dir or "", "voices.json")
    if not path or not os.path.isfile(path):
        return voices
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, ValueError):
        return voices
    if not isinstance(entries, list):
        return voices
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if name and name not in known:
            voices.append(_from_label(name, str(entry.get("label", ""))))
            known.add(name)
    return voices


def _custom_voices(custom_path: str) -> list[Voice]:
    """Giọng người dùng tự học từ đoạn ghi âm."""
    if not custom_path or not os.path.isfile(custom_path):
        return []
    try:
        with open(custom_path, encoding="utf-8") as f:
            presets = json.load(f).get("presets", {})
    except (OSError, ValueError):
        return []
    voices: list[Voice] = []
    for name, entry in (presets or {}).items():
        if not isinstance(entry, dict) or not str(name).strip():
            continue
        voices.append(Voice(
            name=str(name),
            gender=str(entry.get("gender", "")),
            region=str(entry.get("region", "")),
            country=str(entry.get("country", "") or "vn"),
            style=str(entry.get("style", "") or "tu_nhien"),
            description=str(entry.get("description", "")),
            source=str(entry.get("source", "") or "custom"),
        ))
    return voices


def catalog(settings) -> list[Voice]:
    """Toàn bộ giọng dùng được: giọng tự thêm đứng trước, rồi giọng có sẵn."""
    custom = _custom_voices(settings.vieneu_custom_voices_path())
    builtin = _builtin_voices(settings.vieneu_model_dir_path())
    taken = {v.name for v in custom}
    return custom + [v for v in builtin if v.name not in taken]


def resolve(settings, name: str | None = None) -> str:
    """Tên giọng dùng thật cho một lần chạy.

    Thứ tự ưu tiên: tên gọi truyền vào → giọng mặc định trong cấu hình →
    giọng đầu tiên của danh mục → :data:`DEFAULT_VOICE`. Luôn trả về một tên
    có trong danh mục để worker không chết vì «unknown voice».
    """
    voices = catalog(settings)
    names = {v.name for v in voices}
    for candidate in (name, getattr(settings, "vieneu_voice", "")):
        candidate = (candidate or "").strip()
        if candidate in names:
            return candidate
    if DEFAULT_VOICE in names:
        return DEFAULT_VOICE
    return voices[0].name if voices else DEFAULT_VOICE
