"""Translation provider and context configuration page."""
from __future__ import annotations

from PySide6.QtWidgets import QWidget

from autodub_gui.pages import settings_fields as spec
from autodub_gui.pages.settings_panels import ConnectionChecks
from autodub_gui.pages.tool_page_base import ToolPage


class TranslateToolPage(ToolPage):
    """Ngữ cảnh dịch và trạng thái kết nối tới máy chủ."""

    TAB = spec.TAB_TRANSLATE
    TITLE = "Dịch thuật"
    SUBTITLE = ("Configure Gemini, OpenRouter, DeepSeek or VoxDub. API keys are masked; "
                "model and Base URL accept custom values.")
    EXPANDED = {"Provider", "Common parameters"}
    SAVE_LABEL = "Lưu cấu hình dịch"
    SAVED_TOAST = "Đã lưu cấu hình dịch."

    def extra_panels(self) -> list[QWidget]:
        self.checks_panel = ConnectionChecks()
        return [self.checks_panel]

    def cleanup(self) -> None:
        panel = getattr(self, "checks_panel", None)
        if panel is not None and hasattr(panel, "cleanup"):
            panel.cleanup()
