"""Cất API Key vào kho khóa của hệ điều hành (Windows Credential Manager).

Tệp ``.env`` là chữ thường, ai mở cũng đọc được — API Key nằm trong đó dễ lộ
khi người dùng chụp màn hình, sao lưu hay gửi thư mục ứng dụng cho người
khác. Khi máy có gói ``keyring``, ứng dụng cất giá trị thật vào kho khóa của
hệ điều hành và chỉ ghi dấu ``@keyring`` vào ``.env``.

Gói ``keyring`` là TÙY CHỌN: không có thì mọi hàm ở đây nói "không dùng
được" và API Key tiếp tục nằm trong ``.env`` như trước — không đổi hành vi.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("autodub.keystore")

#: Tên dịch vụ trong kho khóa của hệ điều hành.
_SERVICE = "VoxDub Studio"

#: Giá trị ghi vào .env thay cho API Key thật khi đã cất vào kho khóa.
SENTINEL = "@keyring"


def _keyring():
    """Trả về module keyring nếu máy có và kho khóa hoạt động, else None."""
    try:
        import keyring
        from keyring.errors import KeyringError  # noqa: F401 — kiểm tra gói đủ
    except ImportError:
        return None
    return keyring


def available() -> bool:
    """Máy này có dùng được kho khóa của hệ điều hành không."""
    return _keyring() is not None


def get_secret(key: str) -> str:
    """Đọc giá trị thật của một khóa từ kho khóa; trống nếu không có."""
    ring = _keyring()
    if ring is None:
        return ""
    try:
        return ring.get_password(_SERVICE, key) or ""
    except Exception:  # noqa: BLE001 — kho khóa hỏng thì coi như không có
        logger.warning("Không đọc được %s từ kho khóa hệ điều hành", key)
        return ""


def set_secret(key: str, value: str) -> bool:
    """Cất một giá trị vào kho khóa. Trả về True khi cất thành công."""
    ring = _keyring()
    if ring is None:
        return False
    try:
        if value:
            ring.set_password(_SERVICE, key, value)
        else:
            delete_secret(key)
        return True
    except Exception:  # noqa: BLE001 — không cất được thì để .env giữ như cũ
        logger.warning("Không ghi được %s vào kho khóa hệ điều hành", key)
        return False


def delete_secret(key: str) -> None:
    """Xóa một khóa khỏi kho khóa; khóa chưa có thì thôi."""
    ring = _keyring()
    if ring is None:
        return
    try:
        ring.delete_password(_SERVICE, key)
    except Exception:  # noqa: BLE001 — chưa có sẵn thì không cần xóa
        pass


def resolve(key: str, raw: str) -> str:
    """Đổi giá trị đọc từ ``.env`` thành giá trị thật.

    Giá trị thường thì trả nguyên; gặp dấu ``@keyring`` thì tra kho khóa.
    Dấu còn đó mà kho khóa không mở được (gỡ gói keyring, đổi máy) thì đành
    trả trống — nơi gọi sẽ báo thiếu API Key như bình thường.
    """
    raw = (raw or "").strip()
    if raw != SENTINEL:
        return raw
    return get_secret(key)
