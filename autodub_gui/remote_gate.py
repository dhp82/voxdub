"""Remote kill-switch — kiểm tra server điều khiển lúc khởi động app.

Tự chứa: chỉ dùng stdlib + requests + autodub_gui._embedded (giá trị nhúng
lúc build). Gỡ tính năng = xóa file này + các dòng gọi nó trong app.py.

URL kill-switch được CỐ ĐỊNH ngay trong file này (mã hóa base64 để không lộ
khi quét chuỗi trong exe). Thứ tự ưu tiên:

    1. URL nhúng lúc build (autodub_gui/_embedded.py) — nếu có
    2. URL cố định trong file này (_DEFAULT_URL_B64)
    3. Biến môi trường REMOTE_CONTROL_URL — CHỈ khi chạy từ source (dev);
       bản exe (frozen) bỏ qua .env nên người dùng không ghi đè/tắt được.

Nguyên tắc fail-open: chỉ chặn khi server trả lời RÕ RÀNG enabled=false.
Mất mạng, server sập, URL sai, JSON hỏng → cho chạy bình thường.

Server đối ứng: xem control_server/README.md.
"""
from __future__ import annotations

import base64
import os
import sys
import time
from dataclasses import dataclass

from autodub_gui._embedded import REMOTE_CONTROL_URL as _EMBEDDED_URL

ENV_KEY = "REMOTE_CONTROL_URL"
TIMEOUT_S = 5
RETRIES = 2          # mạng chập chờn: thử lại 1 lần trước khi fail-open
RETRY_DELAY_S = 1.0

_DEFAULT_URL_B64 = "aHR0cHM6Ly92b3hkdWItYXBpLmR1Y2tkbnMub3JnL3N0YXR1cw=="

DEFAULT_BLOCK_MESSAGE = (
    "Ứng dụng hiện đang tạm dừng hoạt động.\n"
    "Vui lòng liên hệ quản trị viên để biết thêm chi tiết."
)


@dataclass
class GateResult:
    allowed: bool
    message: str = ""


def _default_url() -> str:
    try:
        return base64.b64decode(_DEFAULT_URL_B64).decode("utf-8").strip()
    except Exception:
        return ""


def resolve_gate_url() -> str:
    """URL kill-switch theo thứ tự ưu tiên nhúng-build → cố định → env(dev)."""
    url = _EMBEDDED_URL.strip() or _default_url()
    if not url and not getattr(sys, "frozen", False):
        # Chế độ dev (chạy từ source) mới cho phép cấu hình qua .env.
        url = os.environ.get(ENV_KEY, "").strip()
    return url


def check_remote_gate(url: str | None = None) -> GateResult:
    """Hỏi server xem app có được phép chạy không.

    Trả về ``GateResult(allowed=False, message=...)`` CHỈ KHI server trả
    JSON hợp lệ với ``enabled`` là false. Mọi lỗi khác → allowed=True.
    Thử lại ``RETRIES`` lần cho lỗi mạng thoáng qua.
    """
    if url is None:
        url = resolve_gate_url()
    url = (url or "").strip()
    if not url:
        # Không cấu hình → tính năng coi như tắt.
        return GateResult(allowed=True)

    # Import lười: requests (+urllib3) tốn ~200ms lúc khởi động — hàm này
    # chỉ chạy trên thread nền nên để nó tự trả giá import ở đó.
    import requests

    data = None
    for attempt in range(RETRIES):
        try:
            resp = requests.get(url, timeout=TIMEOUT_S)
            resp.raise_for_status()
            data = resp.json()
            break
        except (requests.RequestException, ValueError):
            if attempt < RETRIES - 1:
                time.sleep(RETRY_DELAY_S)

    if not isinstance(data, dict) or data.get("enabled", True):
        return GateResult(allowed=True)  # fail-open

    message = str(data.get("message") or "").strip() or DEFAULT_BLOCK_MESSAGE
    return GateResult(allowed=False, message=message)
