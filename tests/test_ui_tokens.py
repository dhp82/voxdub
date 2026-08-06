"""Chặn việc viết mã màu trực tiếp trong mã nguồn giao diện.

Mọi mã hex chỉ được phép xuất hiện trong `autodub_gui/tokens.py`. Các tệp cũ
chưa chuyển đổi được liệt kê tạm trong `_LEGACY_ALLOWED`, và danh sách này
phải rỗng khi kết thúc Giai đoạn 8.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b")

_ROOT = Path(__file__).parent.parent
_GUI = _ROOT / "autodub_gui"

# Tệp duy nhất được phép chứa mã màu.
_TOKENS_FILE = "tokens.py"

# Danh sách miễn trừ tạm cho các tệp cũ. Đã dọn sạch ở Giai đoạn 8 nên nó
# rỗng; nếu ai đó thêm tên vào đây thì phải kèm lý do và kế hoạch dọn.
_LEGACY_ALLOWED = set()


def _gui_files() -> list[Path]:
    """Mọi tệp Python trong thư mục giao diện, bỏ qua thư mục biên dịch tạm."""
    return sorted(p for p in _GUI.rglob("*.py")
                  if "__pycache__" not in p.parts)


def test_no_hardcoded_hex_outside_tokens() -> None:
    """Không tệp giao diện nào ngoài tokens.py được chứa mã màu hex."""
    offenders: list[str] = []
    for path in _gui_files():
        rel = path.relative_to(_GUI).as_posix()
        if rel == _TOKENS_FILE or rel in _LEGACY_ALLOWED:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if _HEX_RE.search(line):
                offenders.append(f"autodub_gui/{rel}:{lineno}: {line.strip()}")
    if offenders:
        pytest.fail("Mã màu hex phải nằm trong tokens.py:\n" +
                    "\n".join(offenders))


def test_legacy_allowlist_has_no_stale_entries() -> None:
    """Mọi tên trong danh sách miễn trừ phải là tệp còn tồn tại."""
    missing = [name for name in _LEGACY_ALLOWED if not (_GUI / name).is_file()]
    assert not missing, (
        f"Xóa khỏi _LEGACY_ALLOWED những tệp không còn: {missing}")


def test_tokens_module_defines_core_palette() -> None:
    """tokens.py phải có đủ các tên màu nền tảng."""
    from autodub_gui import tokens

    required = (
        "BG_APP", "BG_SIDEBAR", "BG_MAIN", "BG_PANEL", "BG_INPUT",
        "BORDER_SUBTLE", "BORDER_DEFAULT", "BORDER_ACTIVE",
        "PRIMARY", "PRIMARY_HOVER", "PRIMARY_DARK",
        "TEXT_PRIMARY", "TEXT_SECONDARY", "TEXT_MUTED", "TEXT_DISABLED",
        "SUCCESS", "WARNING", "DANGER", "PROCESSING",
        "WAVEFORM", "PLAYHEAD", "RADIUS_MD", "SP_4", "FONT_STACK",
    )
    for name in required:
        assert hasattr(tokens, name), f"tokens.py thiếu {name}"


def test_no_pure_black_and_no_white_border() -> None:
    """Nền tối nhất không được là đen thuần; viền không được sáng quá mức."""
    from autodub_gui import tokens

    assert tokens.BG_APP.lower() != "#000000"
    assert tokens.BORDER_DEFAULT.lower() <= "#273449"


def test_theme_stylesheet_builds_from_tokens() -> None:
    """STYLESHEET phải dựng được và chứa màu nền lấy từ token."""
    from autodub_gui import theme, tokens

    assert theme.STYLESHEET.strip()
    assert tokens.BG_APP in theme.STYLESHEET
    assert tokens.PRIMARY in theme.STYLESHEET
