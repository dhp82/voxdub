"""Trang Cài đặt: sáu thẻ dạng viên thuốc, ba nút ở chân trang.

Các ô nhập được dựng tự động từ bảng khai báo trong `settings_fields.py`, nên
thêm một mục mới chỉ cần khai báo một dòng là nó tự có mặt, tự nạp giá trị và
tự được lưu. Riêng thẻ Giọng đọc là thư viện giọng riêng, xem
`pages/voice_library.py`.
"""
from __future__ import annotations

import json
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QDoubleSpinBox, QHBoxLayout, QLabel, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget,
)

from autodub_gui import icons, tokens
from autodub_gui.ui.style import clear_background
from autodub_gui.env_store import (
    bool_to_env, env_bool, env_to_multiline, multiline_to_env, read_env,
    write_env,
)
from autodub_gui.pages import BasePage
from autodub_gui.pages import settings_fields as spec
from autodub_gui.pages.settings_panels import (
    ConnectionChecks, DiskUsagePanel, MaintenancePanel,
)
from autodub_gui.pages.voice_library import VoiceLibraryTab
from autodub_gui.ui.buttons import GhostButton, PrimaryButton
from autodub_gui.ui.collapsible import CollapsibleSection
from autodub_gui.ui.inputs import (
    LabeledCombo, LabeledLineEdit, LabeledSlider, LabeledWidget,
)
from autodub_gui.ui.modal import ConfirmDialog
from autodub_gui.ui.pill_tabs import PillTabBar
from autodub_gui.ui.toast import TOASTS

_PAGE_MARGIN = 24
_MULTILINE_H = 76

#: Tệp nhớ thẻ Cài đặt mở lần trước — chỉ nhớ THẺ, không nhớ nhóm nào đang
#: mở, để lần sau mở ra trang vẫn gọn gàng chứ không bung hết như lần trước.
_UI_STATE_FILE = "settings_ui.json"

# Những nhóm được mở sẵn khi vào trang: chỉ phần cốt lõi hoặc phải điền
# trước khi dùng. Các nhóm còn lại gập lại, bấm tiêu đề là mở.
_EXPANDED_GROUPS: dict[str, set[str]] = {
    spec.TAB_BASIC: {"Chất lượng tổng thể", "Nghe và chép lời video gốc"},
    spec.TAB_SUBTITLE: {"Mặc định"},
    spec.TAB_PERF: {"Hiệu năng"},
    spec.TAB_API: {"Dịch tự động"},
    spec.TAB_ADVANCED: set(),
}

# Nơi dịch đang chọn → nhóm chứa API Key của nơi đó. Nhóm này được mở sẵn
# vì đây là thứ người dùng BẮT BUỘC phải điền để dịch tự động chạy được.
_ENGINE_GROUP: dict[str, str] = {
    "gemini": "Google Gemini",
    "openrouter": "OpenRouter",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "deepseek": "DeepSeek",
    "custom": "Dịch vụ khác",
}


class SettingsPage(BasePage):
    """Toàn bộ tùy chỉnh của ứng dụng."""

    saved = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._widgets: dict[str, QWidget] = {}
        self._secret_fields: list[QWidget] = []
        self._sections: dict[tuple[str, str], CollapsibleSection] = {}
        self._snapshot: dict[str, str] = {}
        self._dirty = False
        self._build()
        self.reload()
        self._restore_last_tab()

    # -- Dựng giao diện ------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(_PAGE_MARGIN, tokens.SP_2,
                                _PAGE_MARGIN, tokens.SP_4)
        root.setSpacing(tokens.SP_3)

        # Thanh tab viên thuốc + chồng trang, thay cho QTabWidget cũ.
        tab_icons = {
            spec.TAB_BASIC: icons.sliders,
            spec.TAB_VOICE: icons.mic,
            spec.TAB_SUBTITLE: icons.captions,
            spec.TAB_PERF: icons.star,
            spec.TAB_API: icons.globe,
            spec.TAB_ADVANCED: icons.gear,
        }
        self.tabbar = PillTabBar()
        self._stack = QStackedWidget()
        clear_background(self._stack)
        for name in spec.TABS:
            make_icon = tab_icons.get(name)
            self.tabbar.add_tab(
                name, make_icon(tokens.TEXT_SECONDARY) if make_icon else None)
            self._stack.addWidget(self._build_tab(name))
        self.tabbar.changed.connect(self._stack.setCurrentIndex)
        self.tabbar.changed.connect(self._remember_tab)
        bar_row = QHBoxLayout()
        bar_row.addWidget(self.tabbar)
        bar_row.addStretch()
        root.addLayout(bar_row)
        root.addWidget(self._stack, 1)
        root.addLayout(self._build_footer())

    def _build_tab(self, tab: str) -> QWidget:
        """Một thẻ: vùng cuộn chứa các nhóm mục."""
        if tab == spec.TAB_VOICE:
            # Thẻ Giọng đọc là thư viện giọng riêng, tự lo cuộn bên trong.
            self.voice_panel = VoiceLibraryTab(self._current_settings)
            self.voice_panel.changed.connect(self._mark_dirty)
            return self.voice_panel
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        holder = QWidget()
        clear_background(holder)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(tokens.SP_2, tokens.SP_3,
                                  tokens.SP_4, tokens.SP_4)
        layout.setSpacing(tokens.SP_4)

        for group in spec.groups_of(tab):
            layout.addWidget(self._build_group(tab, group))
        for extra in self._extra_panels(tab):
            layout.addWidget(extra)
        layout.addStretch()
        scroll.setWidget(holder)
        return scroll

    def _build_group(self, tab: str, group: str) -> QWidget:
        # Mở sẵn hay gập lại tùy độ quan trọng — trang Cài đặt mở ra phải
        # gọn, không phải một tờ khai dài bung hết mọi mục.
        expanded = group in _EXPANDED_GROUPS.get(tab, set())
        section = CollapsibleSection(group, expanded=expanded)
        self._sections[(tab, group)] = section
        for item in spec.fields_of(tab):
            if item.group != group:
                continue
            widget = self._build_field(item)
            self._widgets[item.key] = widget
            section.add_widget(widget)
        return section

    def _build_field(self, item: spec.Field) -> QWidget:
        """Dựng đúng loại ô nhập cho một mục đã khai báo."""
        builders = {
            spec.COMBO: self._build_combo,
            spec.FONT: self._build_font_combo,
            spec.CHECK: self._build_check,
            spec.SLIDER: self._build_slider,
            spec.NUMBER: self._build_number,
            spec.SECRET: self._build_secret,
            spec.MULTILINE: self._build_multiline,
            spec.FOLDER: self._build_folder,
            spec.FILE: self._build_file,
            spec.COLOR: self._build_color,
        }
        return builders.get(item.kind, self._build_text)(item)

    def _build_combo(self, item: spec.Field) -> QWidget:
        widget = LabeledCombo(item.label, item.options, item.hint)
        widget.changed.connect(self._mark_dirty)
        return widget

    def _build_font_combo(self, item: spec.Field) -> QWidget:
        """Ô chọn phông, chỉ lấy phông trong thư mục của dự án."""
        from autodub_gui.fonts import font_choices, fonts_dir, has_app_fonts

        widget = LabeledCombo(item.label, font_choices(), item.hint)
        widget.changed.connect(self._mark_dirty)
        holder = QWidget()
        clear_background(holder)     # ăn theo nền của nhóm chứa nó
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(tokens.SP_2)
        layout.addWidget(widget)

        row = QHBoxLayout()
        button = GhostButton("Mở thư mục phông chữ")
        button.clicked.connect(lambda: self._open_path(fonts_dir()))
        row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)

        note = QLabel(
            "Thả tệp phông chữ đuôi .ttf vào thư mục này rồi mở lại cửa sổ — "
            "phông mới sẽ xuất hiện trong danh sách."
            if has_app_fonts() else
            "Thư mục phông chữ đang trống. Hãy thả ít nhất một tệp .ttf vào "
            "đó rồi mở lại cửa sổ, nếu không phụ đề sẽ không hiện đúng chữ "
            "tiếng Việt trên máy khác.")
        note.setWordWrap(True)
        note.setStyleSheet(
            f"color: {tokens.TEXT_MUTED if has_app_fonts() else tokens.WARNING}; "
            f"font-size: {tokens.FS_META}px; background: transparent;")
        layout.addWidget(note)
        holder.field = widget      # để phần nạp và lưu tìm lại được ô chọn
        return holder

    def _build_check(self, item: spec.Field) -> QWidget:
        box = QCheckBox(item.label)
        box.setToolTip(item.hint)
        box.toggled.connect(self._mark_dirty)
        return box

    def _build_slider(self, item: spec.Field) -> QWidget:
        widget = LabeledSlider(item.label, item.minimum, item.maximum,
                               item.step, item.hint, item.suffix,
                               decimals=item.decimals)
        widget.changed.connect(self._mark_dirty)
        return widget

    def _build_number(self, item: spec.Field) -> QWidget:
        spin = QDoubleSpinBox()
        spin.setRange(item.minimum, item.maximum)
        spin.setSingleStep(item.step)
        spin.setDecimals(item.decimals)
        spin.setSuffix(item.suffix)
        spin.valueChanged.connect(self._mark_dirty)
        return LabeledWidget(item.label, spin, item.hint)

    def _build_text(self, item: spec.Field) -> QWidget:
        widget = LabeledLineEdit(item.label, item.placeholder, item.hint)
        widget.changed.connect(self._mark_dirty)
        return widget

    def _build_secret(self, item: spec.Field) -> QWidget:
        widget = LabeledLineEdit(item.label, item.placeholder, item.hint,
                                 password=True)
        widget.changed.connect(self._mark_dirty)
        self._secret_fields.append(widget)
        return widget

    def _build_multiline(self, item: spec.Field) -> QWidget:
        from PySide6.QtWidgets import QPlainTextEdit

        edit = QPlainTextEdit()
        edit.setPlaceholderText(item.placeholder)
        edit.setFixedHeight(_MULTILINE_H)
        edit.textChanged.connect(self._mark_dirty)
        return LabeledWidget(item.label, edit, item.hint)

    def _build_folder(self, item: spec.Field) -> QWidget:
        from autodub_gui.ui.inputs import FilePicker

        widget = FilePicker(item.label, item.placeholder, item.hint,
                            directory=True)
        widget.changed.connect(lambda _t: self._mark_dirty())
        return widget

    def _build_file(self, item: spec.Field) -> QWidget:
        from autodub_gui.ui.inputs import FilePicker

        widget = FilePicker(item.label, item.placeholder, item.hint,
                            name_filter="Âm thanh (*.wav *.mp3 *.m4a *.flac)")
        widget.changed.connect(lambda _t: self._mark_dirty())
        return widget

    def _build_color(self, item: spec.Field) -> QWidget:
        button = GhostButton(item.default)
        button.setToolTip(item.hint)
        button.clicked.connect(lambda: self._pick_color(button))
        self._paint_color(button, item.default)
        return LabeledWidget(item.label, button, item.hint)

    def _extra_panels(self, tab: str) -> list[QWidget]:
        """Những phần không phải ô nhập đơn giản, dựng riêng cho từng thẻ."""
        if tab == spec.TAB_API:
            self.checks_panel = ConnectionChecks(self._current_values)
            engine = self._widget_of("TRANSLATE_ENGINE")
            if engine is not None:
                engine.changed.connect(
                    lambda: self.checks_panel.select_engine(
                        engine.current_key()))
                engine.changed.connect(self._sync_engine_group)
            return [self.checks_panel]
        if tab == spec.TAB_ADVANCED:
            self.maintenance = MaintenancePanel(self._current_settings)
            self.disk_panel = DiskUsagePanel(self._current_settings)
            return [self.maintenance, self.disk_panel]
        return []

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(tokens.SP_2)
        self.chk_secrets = QCheckBox("Hiện API Key")
        self.chk_secrets.setToolTip(
            "Bật để nhìn thấy API Key đang lưu, dùng khi cần kiểm tra lại.")
        self.chk_secrets.toggled.connect(self._toggle_secrets)
        self.btn_defaults = GhostButton("Khôi phục mặc định")
        self.btn_defaults.clicked.connect(self._restore_defaults)
        self.btn_cancel = GhostButton("Hủy")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel_changes)
        self.btn_save = PrimaryButton("Lưu cài đặt")
        self.btn_save.clicked.connect(self.save)
        row.addWidget(self.chk_secrets)
        row.addWidget(self.btn_defaults)
        row.addStretch()
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_save)
        return row

    # -- Đọc và ghi giá trị của một ô ----------------------------------
    def _widget_of(self, key: str) -> QWidget | None:
        widget = self._widgets.get(key)
        return getattr(widget, "field", widget) if widget is not None else None

    def _get_value(self, item: spec.Field) -> str:
        widget = self._widget_of(item.key)
        if widget is None:
            return item.default
        if item.kind in (spec.COMBO, spec.FONT):
            return widget.current_key()
        if item.kind == spec.CHECK:
            return bool_to_env(widget.isChecked())
        if item.kind == spec.SLIDER:
            return f"{widget.value():.{item.decimals}f}"
        if item.kind == spec.NUMBER:
            return f"{widget.widget.value():.{item.decimals}f}"
        if item.kind == spec.MULTILINE:
            return multiline_to_env(widget.widget.toPlainText())
        if item.kind == spec.COLOR:
            return widget.widget.text().strip()
        return widget.text()

    def _set_value(self, item: spec.Field, raw: str) -> None:
        widget = self._widget_of(item.key)
        if widget is None:
            return
        if item.kind in (spec.COMBO, spec.FONT):
            widget.set_key(raw or item.default)
        elif item.kind == spec.CHECK:
            widget.setChecked(env_bool(raw, env_bool(item.default)))
        elif item.kind == spec.SLIDER:
            widget.set_value(_to_float(raw, item.default))
        elif item.kind == spec.NUMBER:
            widget.widget.setValue(_to_float(raw, item.default))
        elif item.kind == spec.MULTILINE:
            widget.widget.setPlainText(env_to_multiline(raw))
        elif item.kind == spec.COLOR:
            self._paint_color(widget.widget, raw or item.default)
        else:
            widget.set_text(raw)

    # -- Nạp, lưu, hủy -------------------------------------------------
    def reload(self) -> None:
        """Đọc lại toàn bộ giá trị từ tệp cấu hình."""
        from autodub.keystore import resolve

        env = read_env()
        for item in spec.FIELDS:
            raw = env.get(item.key, item.default)
            if item.kind == spec.SECRET:
                # API Key có thể đã được cất vào kho khóa hệ điều hành.
                raw = resolve(item.key, raw)
            self._set_value(item, raw)
        if hasattr(self, "voice_panel"):
            self.voice_panel.load(env)
        if hasattr(self, "checks_panel"):
            self.checks_panel.select_engine(env.get("TRANSLATE_ENGINE", ""))
        self._sync_engine_group()
        self._snapshot = self._collect()
        self._set_dirty(False)

    def _sync_engine_group(self) -> None:
        """Mở sẵn nhóm API Key của nơi dịch đang chọn, gập các nhóm kia.

        Người dùng đổi nơi dịch thì thứ họ cần điền tiếp theo là mã của nơi
        đó — mở đúng nhóm ấy ra, còn mã của các nơi khác thì gập lại cho gọn.
        """
        engine = self._widget_of("TRANSLATE_ENGINE")
        if engine is None:
            return
        wanted = _ENGINE_GROUP.get(engine.current_key())
        for group in _ENGINE_GROUP.values():
            section = self._sections.get((spec.TAB_API, group))
            if section is not None:
                section.set_expanded(group == wanted)

    # -- Nhớ thẻ đang mở ------------------------------------------------
    def _ui_state_path(self) -> str:
        from autodub_gui.pages.new_project_page import cache_dir

        return os.path.join(cache_dir(), _UI_STATE_FILE)

    def _restore_last_tab(self) -> None:
        """Mở lại đúng thẻ người dùng xem lần trước, hỏng tệp thì bỏ qua."""
        try:
            with open(self._ui_state_path(), encoding="utf-8") as f:
                index = int(json.load(f).get("tab", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return
        if 0 <= index < self.tabbar.count():
            self.tabbar.set_current_index(index)

    def _remember_tab(self, index: int) -> None:
        try:
            with open(self._ui_state_path(), "w", encoding="utf-8") as f:
                json.dump({"tab": index}, f)
        except OSError:
            pass               # không ghi được thì thôi, không đáng báo lỗi

    def _collect(self) -> dict[str, str]:
        """Gom giá trị hiện tại của mọi mục."""
        values = {item.key: self._get_value(item) for item in spec.FIELDS}
        if hasattr(self, "voice_panel"):
            values.update(self.voice_panel.values())
        return values

    def _current_values(self) -> dict[str, str]:
        """Giá trị đang hiển thị, dùng cho các nút kiểm tra kết nối."""
        return self._collect()

    def _current_settings(self):
        """Cấu hình phản ánh đúng form hiện tại, chưa cần bấm Lưu.

        Nghe thử giọng phải dùng ĐÚNG giọng và phong cách đang chọn trên màn
        hình, chứ không phải giá trị đã lưu lần trước.
        """
        from dataclasses import replace

        from autodub.config import Settings

        values = self._collect()
        settings = Settings.load(override=True)
        changes: dict = {}
        if values.get("VIENEU_VOICE"):
            changes["vieneu_voice"] = values["VIENEU_VOICE"]
        if values.get("VIENEU_STYLE"):
            changes["vieneu_style"] = values["VIENEU_STYLE"]
        return replace(settings, **changes) if changes else settings

    def save(self) -> None:
        """Ghi mọi thay đổi xuống tệp cấu hình."""
        values = self._collect()
        # Số việc chạy cùng lúc: 0 nghĩa là để ứng dụng tự chọn, ghi rỗng.
        for key in ("PARALLEL_WORKERS", "VIENEU_MAX_WORKERS"):
            if values.get(key, "").strip() in ("0", "0.0", ""):
                values[key] = ""
        values = self._stash_secrets(values)
        try:
            write_env(values)
        except OSError as e:
            ConfirmDialog.show_error(
                self, "Không lưu được cài đặt",
                "Ứng dụng không ghi được vào tệp cấu hình. Có thể tệp đang bị "
                "một chương trình khác mở, hoặc thư mục không cho ghi. Hãy "
                "đóng chương trình đó rồi bấm Lưu lại.", detail=str(e))
            return
        self._warn_missing_key(values)
        self._snapshot = self._collect()
        self._set_dirty(False)
        self.saved.emit()

    def _stash_secrets(self, values: dict[str, str]) -> dict[str, str]:
        """Cất API Key vào kho khóa hệ điều hành nếu máy hỗ trợ.

        Cất được thì tệp cấu hình chỉ còn dấu ``@keyring`` thay cho giá trị
        thật — chụp màn hình hay gửi thư mục ứng dụng cho ai cũng không lộ.
        Máy không có gói keyring thì giữ nguyên hành vi cũ: ghi thẳng .env.
        """
        from autodub.keystore import SENTINEL, available, delete_secret, \
            set_secret

        if not available():
            return values
        out = dict(values)
        for item in spec.FIELDS:
            if item.kind != spec.SECRET or item.key not in out:
                continue
            secret = out[item.key].strip()
            if not secret:
                delete_secret(item.key)     # xóa key là xóa cả trong kho khóa
                continue
            if set_secret(item.key, secret):
                out[item.key] = SENTINEL
        return out

    def _warn_missing_key(self, values: dict[str, str]) -> None:
        """Nhắc nhẹ khi chọn nơi dịch mà chưa điền API Key."""
        from autodub.text.translate_openai import PROVIDERS

        engine = values.get("TRANSLATE_ENGINE", "")
        key_name = ("GOOGLE_API_KEY" if engine == "gemini"
                    else f"{engine.upper()}_API_KEY")
        if not engine or values.get(key_name, "").strip():
            return
        label = ("Gemini" if engine == "gemini"
                 else PROVIDERS.get(engine, {}).get("label", engine))
        where = ("aistudio.google.com/apikey" if engine == "gemini"
                 else PROVIDERS.get(engine, {}).get("keys", ""))
        ConfirmDialog.ask(
            self, f"Chưa có API Key {label}",
            f"Bạn chọn dịch bằng {label} nhưng chưa điền API Key. Cài đặt "
            "vẫn được lưu, nhưng khi chạy tới bước dịch ứng dụng sẽ dừng lại "
            f"và hướng dẫn bạn dịch tay. Lấy mã tại {where}.",
            kind="warning", confirm_label="Đã hiểu", cancel_label="")

    def _cancel_changes(self) -> None:
        if not self._dirty:
            return
        confirmed, _ = ConfirmDialog.ask(
            self, "Bỏ thay đổi",
            "Mọi thay đổi bạn vừa chỉnh sẽ quay về giá trị đã lưu lần trước. "
            "Bạn có chắc không?",
            kind="warning", confirm_label="Bỏ thay đổi", cancel_label="Giữ lại")
        if not confirmed:
            return
        for item in spec.FIELDS:
            self._set_value(item, self._snapshot.get(item.key, item.default))
        self._set_dirty(False)
        TOASTS.info("Đã quay về giá trị đã lưu lần trước.")

    def _restore_defaults(self) -> None:
        confirmed, _ = ConfirmDialog.ask(
            self, "Khôi phục mặc định",
            "Toàn bộ cài đặt sẽ quay về giá trị ban đầu của ứng dụng. Mã kết "
            "nối bạn đã điền cũng bị xóa. Thay đổi chỉ được ghi xuống khi bạn "
            "bấm Lưu cài đặt.",
            kind="warning", confirm_label="Nạp giá trị mặc định")
        if not confirmed:
            return
        for item in spec.FIELDS:
            self._set_value(item, item.default)
        self._set_dirty(True)
        TOASTS.info("Đã nạp giá trị mặc định — bấm Lưu cài đặt để áp dụng.")

    # -- Theo dõi thay đổi ---------------------------------------------
    def _mark_dirty(self, *_args) -> None:
        self._set_dirty(True)

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        self.btn_cancel.setEnabled(dirty)
        self.btn_save.setText("Lưu cài đặt" + (" •" if dirty else ""))

    def has_unsaved_changes(self) -> bool:
        return self._dirty

    # -- Tiện ích ------------------------------------------------------
    def _toggle_secrets(self, shown: bool) -> None:
        from PySide6.QtWidgets import QLineEdit

        mode = (QLineEdit.EchoMode.Normal if shown
                else QLineEdit.EchoMode.Password)
        for widget in self._secret_fields:
            widget.edit.setEchoMode(mode)

    def _pick_color(self, button) -> None:
        from PySide6.QtGui import QColor

        current = QColor(button.text().strip()
                         or tokens.SUBTITLE_TEXT_DEFAULT)
        chosen = QColorDialog.getColor(current, self, "Chọn màu")
        if chosen.isValid():
            self._paint_color(button, chosen.name().upper())
            self._mark_dirty()

    @staticmethod
    def _paint_color(button, hex_color: str) -> None:
        """Tô nền nút theo màu đang chọn, chữ đen hay trắng tùy độ sáng."""
        from PySide6.QtGui import QColor

        button.setText(hex_color)
        color = QColor(hex_color)
        luminance = (0.299 * color.red() + 0.587 * color.green()
                     + 0.114 * color.blue())
        text_color = tokens.BG_APP if luminance > 140 else tokens.TEXT_ON_ACCENT
        button.setStyleSheet(
            f"QPushButton#ghost {{ background: {hex_color}; "
            f"color: {text_color}; font-weight: 600; }}")

    def _open_path(self, path: str) -> None:
        from autodub_gui.system_open import open_folder

        os.makedirs(path, exist_ok=True)
        ok, message = open_folder(path)
        if not ok:
            TOASTS.warn(message)

    def focus_display_name(self) -> None:
        """Mở thẻ Cơ bản và đưa con trỏ vào ô tên hiển thị."""
        self.tabbar.set_current_index(0)
        section = self._sections.get((spec.TAB_BASIC, "Hiển thị"))
        if section is not None:
            section.set_expanded(True)
        widget = self._widget_of("DISPLAY_NAME")
        if widget is not None:
            widget.edit.setFocus()
            widget.edit.selectAll()

    def open_tool(self, key: str) -> None:
        """Nhảy tới thẻ tương ứng với mục công cụ trên thanh bên."""
        tab = {
            "voice": spec.TAB_VOICE,
            "translate": spec.TAB_API,
            "subtitle": spec.TAB_SUBTITLE,
        }.get(key)
        if tab is not None:
            self.tabbar.set_current_index(spec.TABS.index(tab))

    # -- Vòng đời ------------------------------------------------------
    def cleanup(self) -> None:
        for name in ("voice_panel", "checks_panel", "disk_panel"):
            panel = getattr(self, name, None)
            if panel is not None and hasattr(panel, "cleanup"):
                panel.cleanup()



def _to_float(raw: str, default: str) -> float:
    """Đọc số từ tệp cấu hình, hỏng thì dùng giá trị mặc định."""
    for candidate in (raw, default):
        try:
            return float(str(candidate).strip())
        except (TypeError, ValueError):
            continue
    return 0.0
