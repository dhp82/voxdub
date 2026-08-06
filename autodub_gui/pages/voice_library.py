"""Thẻ "Giọng đọc" trong Cài đặt: thư viện giọng dạng lưới thẻ.

Bên trái là ô tìm kiếm, các hàng chip lọc và lưới thẻ giọng; bên phải là cột
tóm tắt: giọng đang chọn, phong cách đọc, các giọng dùng gần đây và phần quản
lý (học thêm giọng mới — tái dùng nguyên khối từ `VoiceSettingsPanel`).

Giữ nguyên hợp đồng của `voice_panel` với trang Cài đặt: `load(env)`,
`values()` và tín hiệu `changed`. Thay đổi chỉ được ghi xuống khi bấm Lưu
cài đặt ở chân trang, giống mọi thẻ khác.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout,
    QWidget,
)

from autodub_gui import tokens
from autodub_gui.pages.settings_panels import VoiceSettingsPanel
from autodub_gui.ui.avatar import InitialAvatar
from autodub_gui.ui.buttons import GhostButton
from autodub_gui.ui.cards import Card
from autodub_gui.ui.chips import ChipRow
from autodub_gui.ui.inputs import LabeledCombo, SearchBox, polish_combo
from autodub_gui.ui.style import clear_background
from autodub_gui.ui.voice_card import VoiceCard
from autodub_gui.voice_preview import VoicePreview

_LEFT_STRETCH = 7
_RAIL_STRETCH = 3
_RECENT_MAX = 3
_CARD_MIN_W = 300           # một thẻ giọng cần chừng này mới đủ chỗ cho nút
_GRID_COLS_WIDE = 3
_GRID_COLS_NARROW = 2
_RAIL_MIN_W = 264

_SORT_OPTIONS = (
    ("Tên A → Z", "name"),
    ("Giọng bạn thêm lên đầu", "custom"),
)


def _muted_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(
        f"color: {tokens.TEXT_MUTED}; font-size: {tokens.FS_META}px; "
        f"background: transparent;")
    return label


class VoiceLibraryTab(QWidget):
    """Toàn bộ thẻ Giọng đọc: lưới thẻ giọng + cột tóm tắt bên phải."""

    changed = Signal()

    def __init__(self, settings_provider, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings_provider = settings_provider
        self._preview = VoicePreview(self)
        self._preview.finished.connect(
            lambda _ok: self._set_preview_enabled(True))
        self._voices: list = []
        self._current = ""
        self._recent: list[str] = []
        self._columns = _GRID_COLS_WIDE
        self._cards: list[VoiceCard] = []
        clear_background(self)
        self._build()
        self._reload_voices()

    # -- Dựng giao diện ------------------------------------------------
    def _build(self) -> None:
        from autodub.speech.tts.voices import COUNTRIES, GENDERS, STYLES

        root = QHBoxLayout(self)
        root.setContentsMargins(tokens.SP_2, tokens.SP_3, 0, tokens.SP_2)
        root.setSpacing(tokens.SP_4)
        root.addWidget(self._build_left(COUNTRIES, GENDERS), _LEFT_STRETCH)
        root.addWidget(self._build_rail(STYLES), _RAIL_STRETCH)

        # Ctrl+K đưa con trỏ vào ô tìm kiếm, chỉ khi đang ở thẻ này.
        shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self.search.focus)

    def _build_left(self, countries, genders) -> QWidget:
        left = QWidget()
        clear_background(left)
        col = QVBoxLayout(left)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(tokens.SP_3)

        row = QHBoxLayout()
        row.setSpacing(tokens.SP_2)
        self.search = SearchBox("Tìm giọng theo tên (Ctrl+K)")
        self.search.search_changed.connect(lambda _t: self._apply_filters())
        row.addWidget(self.search, 1)
        self._sort = QComboBox()
        self._sort.setToolTip("Thứ tự sắp xếp của lưới giọng bên dưới.")
        for label, key in _SORT_OPTIONS:
            self._sort.addItem(label, key)
        polish_combo(self._sort)
        self._sort.currentIndexChanged.connect(
            lambda _i: self._apply_filters())
        row.addWidget(self._sort)
        col.addLayout(row)

        for key, label, options in (
            ("country", "Quốc gia", countries),
            ("gender", "Giới tính", genders),
        ):
            chip_row = ChipRow(label, list(options))
            chip_row.selection_changed.connect(
                lambda _k: self._apply_filters())
            setattr(self, f"_chips_{key}", chip_row)
            col.addWidget(chip_row)

        self._count = _muted_label("")
        col.addWidget(self._count)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        clear_background(holder)
        holder_col = QVBoxLayout(holder)
        holder_col.setContentsMargins(0, 0, tokens.SP_2, 0)
        holder_col.setSpacing(tokens.SP_3)
        self._grid = QGridLayout()
        self._grid.setSpacing(tokens.SP_3)
        holder_col.addLayout(self._grid)
        self._empty = _muted_label(
            "Không có giọng nào khớp bộ lọc. Hãy nới bớt bộ lọc, hoặc học "
            "thêm giọng mới ở cột bên phải.")
        self._empty.setVisible(False)
        holder_col.addWidget(self._empty)
        holder_col.addStretch()
        scroll.setWidget(holder)
        col.addWidget(scroll, 1)

        self._status = _muted_label("")
        col.addWidget(self._status)
        self._preview.status_changed.connect(self._status.setText)
        col.addWidget(_muted_label(
            "Thay đổi sẽ được lưu khi bấm Lưu cài đặt ở cuối trang."))
        return left

    def _build_rail(self, styles) -> QWidget:
        rail = QWidget()
        clear_background(rail)
        rail.setMinimumWidth(_RAIL_MIN_W)
        col = QVBoxLayout(rail)
        col.setContentsMargins(0, 0, tokens.SP_2, 0)
        col.setSpacing(tokens.SP_3)

        # Giọng hiện tại
        current = Card(padding=tokens.SP_4)
        current.add_header("Giọng hiện tại")
        row = QHBoxLayout()
        row.setSpacing(tokens.SP_3)
        self._rail_avatar = InitialAvatar("", 44)
        row.addWidget(self._rail_avatar)
        name_col = QVBoxLayout()
        name_col.setSpacing(2)
        self._rail_name = QLabel("")
        self._rail_name.setStyleSheet(
            f"color: {tokens.TEXT_PRIMARY}; "
            f"font-size: {tokens.FS_CARD_TITLE}px; font-weight: 600; "
            f"background: transparent;")
        name_col.addWidget(self._rail_name)
        self._rail_tags = _muted_label("")
        name_col.addWidget(self._rail_tags)
        row.addLayout(name_col, 1)
        current.body.addLayout(row)
        play_row = QHBoxLayout()
        self._rail_play = GhostButton("Nghe thử")
        # Tab-focus như nút trên thẻ giọng: bị khóa lúc đang phát mà còn giữ
        # focus thì cột phải sẽ tự cuộn theo focus mới.
        self._rail_play.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._rail_play.clicked.connect(lambda: self._play(self._current))
        play_row.addWidget(self._rail_play)
        play_row.addStretch()
        current.body.addLayout(play_row)
        col.addWidget(current)

        # Phong cách đọc — chính là khóa VIENEU_STYLE trước đây ở thẻ Âm thanh
        style_card = Card(padding=tokens.SP_4)
        style_card.add_header("Phong cách đọc")
        self._style = LabeledCombo(
            "Cách nhấn nhá chung", list(styles),
            "Áp dụng cho mọi giọng khi đọc lời thoại.")
        self._style.changed.connect(lambda: self.changed.emit())
        style_card.body.addWidget(self._style)
        col.addWidget(style_card)

        # Dùng gần đây
        recent_card = Card(padding=tokens.SP_4)
        recent_card.add_header("Dùng gần đây")
        self._recent_box = QVBoxLayout()
        self._recent_box.setSpacing(tokens.SP_1)
        recent_card.body.addLayout(self._recent_box)
        col.addWidget(recent_card)

        # Quản lý: tái dùng nguyên khối học giọng của VoiceSettingsPanel;
        # ô chọn giọng bên trong được ẩn đi vì việc chọn đã làm bằng thẻ.
        manage_card = Card(padding=tokens.SP_4)
        self._panel = VoiceSettingsPanel()
        self._panel.set_title("Quản lý giọng")
        # Cột phải hẹp: bỏ khoảng thụt trái mặc định của mục gập, không thì
        # nút và ghi chú bị lệch sang phải một quãng bằng bề ngang mũi tên.
        self._panel.set_content_indent(0)
        self._panel.picker.setVisible(False)
        self._panel.changed.connect(self._on_panel_changed)
        manage_card.body.addWidget(self._panel)
        col.addWidget(manage_card)
        col.addStretch()

        outer = QScrollArea()
        outer.setWidgetResizable(True)
        outer.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.setWidget(rail)
        return outer

    # -- Hợp đồng với trang Cài đặt --------------------------------------
    def load(self, env: dict[str, str]) -> None:
        """Nạp lại giá trị từ tệp cấu hình."""
        from autodub.speech.tts.voices import DEFAULT_VOICE

        self._panel.load(env)
        self._current = (env.get("VIENEU_VOICE") or "").strip() or DEFAULT_VOICE
        self._recent = [name.strip() for name
                        in (env.get("VOICE_RECENT") or "").split(",")
                        if name.strip()][:_RECENT_MAX]
        self._style.set_key(env.get("VIENEU_STYLE") or "tu_nhien")
        self._reload_voices()

    def values(self) -> dict[str, str]:
        from autodub.speech.tts.voices import DEFAULT_VOICE

        return {
            "VIENEU_VOICE": self._current or DEFAULT_VOICE,
            "VIENEU_STYLE": self._style.current_key() or "tu_nhien",
            "VOICE_RECENT": ",".join(self._recent),
        }

    def cleanup(self) -> None:
        self._preview.cleanup()

    # -- Dữ liệu ---------------------------------------------------------
    def _reload_voices(self) -> None:
        from autodub.config import Settings
        from autodub.speech.tts import voices as catalog

        try:
            self._voices = list(catalog.catalog(Settings.load()))
        except Exception:  # noqa: BLE001 — thiếu tệp thì dùng bộ đóng kèm
            self._voices = list(catalog.BUILTIN)
        self._apply_filters()
        self._update_rail()

    def _voice_of(self, name: str):
        return next((v for v in self._voices if v.name == name), None)

    def _apply_filters(self) -> None:
        matched = [v for v in self._voices if v.matches(
            country=self._chips_country.current_key(),
            gender=self._chips_gender.current_key(),
            query=self.search.text())]
        # Giọng đang chọn luôn còn trong lưới, kể cả khi bộ lọc loại nó —
        # không thì đổi một chip là lựa chọn của người dùng biến mất khỏi
        # tầm mắt mà họ không hề biết (cùng quy tắc với VoicePicker).
        if self._current and all(v.name != self._current for v in matched):
            keep = self._voice_of(self._current)
            if keep is not None:
                matched = [keep, *matched]
        if self._sort.currentData() == "custom":
            matched.sort(key=lambda v: (not v.custom, v.name.lower()))
        else:
            matched.sort(key=lambda v: v.name.lower())

        total = len(self._voices)
        self._count.setText(
            f"{total} giọng có sẵn" if len(matched) == total
            else f"{len(matched)} trên {total} giọng")
        self._empty.setVisible(not matched)
        self._rebuild_grid(matched)

    def _rebuild_grid(self, matched: list) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cards = []
        enabled = self._preview.available() and not self._preview.is_busy()
        for i, voice in enumerate(matched):
            card = VoiceCard(voice)
            card.set_selected(voice.name == self._current)
            card.selected.connect(self._on_select)
            card.preview_requested.connect(self._play)
            card.set_preview_enabled(enabled)
            self._grid.addWidget(card, i // self._columns, i % self._columns)
            self._cards.append(card)
        for column in range(_GRID_COLS_WIDE):
            self._grid.setColumnStretch(
                column, 1 if column < self._columns else 0)

    # -- Chọn giọng -------------------------------------------------------
    def _on_select(self, name: str) -> None:
        if name == self._current:
            return
        self._current = name
        self._push_recent(name)
        self._panel.picker.set_voice(name)
        for card in self._cards:
            card.set_selected(card.voice.name == name)
        self._update_rail()
        self.changed.emit()

    def _push_recent(self, name: str) -> None:
        self._recent = [name, *(n for n in self._recent if n != name)]
        self._recent = self._recent[:_RECENT_MAX]

    def _on_panel_changed(self) -> None:
        """Vừa học xong một giọng mới: đọc lại danh mục và chọn giọng đó."""
        name = self._panel.picker.voice()
        if name:
            self._current = name
            self._push_recent(name)
        self._reload_voices()
        self.changed.emit()

    def _update_rail(self) -> None:
        self._rail_avatar.set_name(self._current)
        self._rail_name.setText(self._current or "—")
        voice = self._voice_of(self._current)
        self._rail_tags.setText(self._tag_text(voice) if voice else "")

        while self._recent_box.count():
            item = self._recent_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not self._recent:
            self._recent_box.addWidget(_muted_label(
                "Giọng bạn chọn sẽ được ghi nhớ ở đây."))
            return
        for name in self._recent:
            button = GhostButton(name)
            button.setToolTip(f"Chọn lại giọng «{name}».")
            button.clicked.connect(
                lambda _c=False, n=name: self._on_select(n))
            self._recent_box.addWidget(button)

    @staticmethod
    def _tag_text(voice) -> str:
        from autodub.speech.tts.voices import COUNTRIES, GENDERS, STYLES

        gender = {key: label for label, key in GENDERS}.get(voice.gender, "")
        country = {key: label for label, key in COUNTRIES}.get(
            voice.country, "")
        style = {key: label for label, key in STYLES}.get(voice.style, "")
        return " · ".join(p for p in (gender, country, style) if p)

    # -- Nghe thử ----------------------------------------------------------
    def _play(self, voice: str) -> None:
        if not voice or self._preview.is_busy():
            return
        try:
            settings = self._settings_provider()
        except Exception as e:  # noqa: BLE001 — báo lên giao diện
            self._status.setText(f"Không đọc được cấu hình: {e}")
            return
        self._set_preview_enabled(False)
        self._preview.play(settings, voice)

    def _set_preview_enabled(self, enabled: bool) -> None:
        enabled = enabled and self._preview.available()
        self._rail_play.setEnabled(enabled)
        for card in self._cards:
            card.set_preview_enabled(enabled)

    # -- Vòng đời ----------------------------------------------------------
    def resizeEvent(self, event) -> None:  # noqa: N802 — theo quy ước của Qt
        super().resizeEvent(event)
        # Số cột tính theo bề ngang THẬT của cột trái: mỗi thẻ cần đủ chỗ
        # cho tên + huy hiệu + nút Nghe thử, không thì thẻ bị cắt mép phải.
        left_w = self.width() * _LEFT_STRETCH // (_LEFT_STRETCH + _RAIL_STRETCH)
        columns = (_GRID_COLS_WIDE
                   if left_w >= _CARD_MIN_W * _GRID_COLS_WIDE
                   else _GRID_COLS_NARROW)
        if columns != self._columns:
            self._columns = columns
            self._apply_filters()
