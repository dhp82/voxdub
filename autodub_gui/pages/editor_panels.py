"""Các bảng bên phải của Trình chỉnh sửa.

Bảng danh sách phụ đề là phần được dùng nhiều nhất nên được tối ưu riêng:
khi dự án có nhiều câu, chỉ những câu đang nhìn thấy mới được dựng widget.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget,
)

from autodub_gui import dub_constants as consts
from autodub_gui import icons, tokens
from autodub_gui.formatting import (
    format_duration, format_hours, format_size, format_timecode,
)
from autodub_gui.ui.buttons import GhostButton, IconButton, PrimaryButton
from autodub_gui.ui.collapsible import CollapsibleSection
from autodub_gui.ui.inputs import LabeledCombo, LabeledSlider, SearchBox
from autodub_gui.ui.labels import ElidedLabel
from autodub_gui.ui.progress import ThinProgressBar
from autodub_gui.ui.style import clear_background

EDIT_DEBOUNCE_MS = 800
_ROW_ICON = 24
_TEXT_MIN_H = 46
_ROW_PADDING = 8


class _GrowingTextEdit(QPlainTextEdit):
    """Ô nhập tự cao lên theo nội dung — chữ dài mấy cũng không bị cắt.

    QPlainTextEdit mặc định giữ chiều cao cố định và cuộn bên trong; trong
    danh sách câu thoại điều đó đồng nghĩa với chữ bị cắt (thanh cuộn đã tắt).
    Ô này đo số dòng thật sau khi xuống dòng và nới chiều cao theo.
    """

    height_changed = Signal()

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.document().documentLayout().documentSizeChanged.connect(
            lambda _s: self._fit())
        self._fit()

    def _fit(self) -> None:
        # documentSize() của QPlainTextDocumentLayout trả chiều cao theo SỐ
        # DÒNG (đã tính cả xuống dòng tự động), không phải điểm ảnh.
        lines = max(1, int(self.document().size().height()))
        height = int(lines * self.fontMetrics().lineSpacing()
                     + 2 * self.document().documentMargin()
                     + 2 * self.frameWidth() + 2)
        height = max(_TEXT_MIN_H, height)
        if height != self.minimumHeight():
            self.setMinimumHeight(height)
            self.setMaximumHeight(height)
            self.height_changed.emit()

    def resizeEvent(self, event) -> None:  # noqa: N802 — theo quy ước của Qt
        super().resizeEvent(event)
        # Đổi bề rộng làm chữ xuống dòng khác đi — đo lại chiều cao.
        self._fit()


class SegmentRow(QWidget):
    """Một câu thoại: mốc thời gian, lời đọc, phụ đề riêng và hàng nút.

    Ô phụ đề riêng chỉ hiện khi người dùng bật chế độ tách phụ đề, hoặc khi
    câu này vốn đã có phụ đề khác lời đọc. Để trống ô đó nghĩa là phụ đề dùng
    y hệt lời đọc — đúng như phần lớn trường hợp.
    """

    text_edited = Signal(int, str)
    subtitle_edited = Signal(int, str)
    play_requested = Signal(int)
    resynth_requested = Signal(int)
    split_requested = Signal(int)
    merge_requested = Signal(int)
    delete_requested = Signal(int)
    height_changed = Signal(int)         # id câu — dòng cần được đo lại

    def __init__(self, segment: dict, text_field: str,
                 show_subtitle: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        from autodub.text.srt import SUBTITLE_FIELD, has_subtitle_override

        self._id = int(segment.get("id", 0))
        self._text_field = text_field
        root = QVBoxLayout(self)
        root.setContentsMargins(_ROW_PADDING, _ROW_PADDING,
                                _ROW_PADDING, _ROW_PADDING)
        root.setSpacing(tokens.SP_1)

        head = QHBoxLayout()
        head.setSpacing(tokens.SP_2)
        self.time_label = QLabel(self._time_text(segment))
        self.time_label.setStyleSheet(
            f"color: {tokens.ACCENT_BLUE}; font-size: {tokens.FS_META}px; "
            f"font-family: {tokens.FONT_MONO}; background: transparent;")
        head.addWidget(self.time_label)
        head.addStretch()
        self.index_label = QLabel(f"Câu {self._id}")
        self.index_label.setStyleSheet(
            f"color: {tokens.TEXT_MUTED}; font-size: {tokens.FS_BADGE}px; "
            f"background: transparent;")
        head.addWidget(self.index_label)
        root.addLayout(head)

        self.editor = self._text_box(str(segment.get(text_field, "")),
                                     tokens.TEXT_PRIMARY)
        self.editor.textChanged.connect(
            lambda: self.text_edited.emit(self._id,
                                          self.editor.toPlainText()))
        root.addWidget(self.editor)

        self.sub_caption = QLabel("Phụ đề riêng")
        self.sub_caption.setStyleSheet(
            f"color: {tokens.TEXT_MUTED}; font-size: {tokens.FS_BADGE}px; "
            f"background: transparent;")
        self.sub_editor = self._text_box(
            str(segment.get(SUBTITLE_FIELD, "") or ""), tokens.TEXT_SECONDARY)
        self.sub_editor.setPlaceholderText("Để trống là dùng y hệt lời đọc")
        self.sub_editor.textChanged.connect(
            lambda: self.subtitle_edited.emit(self._id,
                                              self.sub_editor.toPlainText()))
        root.addWidget(self.sub_caption)
        root.addWidget(self.sub_editor)
        self.set_subtitle_visible(
            show_subtitle or has_subtitle_override(segment, text_field))

        root.addLayout(self._build_actions())
        # Ô chữ cao lên (gõ thêm dòng) thì báo cho danh sách nới dòng theo.
        self.editor.height_changed.connect(
            lambda: self.height_changed.emit(self._id))
        self.sub_editor.height_changed.connect(
            lambda: self.height_changed.emit(self._id))

    @staticmethod
    def _text_box(text: str, color: str) -> "_GrowingTextEdit":
        box = _GrowingTextEdit(text)
        box.setStyleSheet(
            f"QPlainTextEdit {{ background: transparent; border: none; "
            f"color: {color}; font-size: {tokens.FS_BODY}px; padding: 0; }}")
        return box

    def set_subtitle_visible(self, visible: bool) -> None:
        self.sub_caption.setVisible(visible)
        self.sub_editor.setVisible(visible)

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(tokens.SP_1)
        specs = (
            (icons.play(tokens.SUCCESS), "Nghe câu này", self.play_requested),
            (icons.reload(tokens.ACCENT_BLUE), "Đọc lại câu này",
             self.resynth_requested),
            (icons.scissors(tokens.TEXT_SECONDARY), "Tách câu này làm đôi",
             self.split_requested),
            (icons.merge(tokens.TEXT_SECONDARY), "Gộp với câu bên dưới",
             self.merge_requested),
            (icons.trash(tokens.DANGER), "Xóa câu này", self.delete_requested),
        )
        for icon, tip, signal in specs:
            button = IconButton(icon, tip, size=_ROW_ICON)
            button.clicked.connect(lambda _c=False, s=signal: s.emit(self._id))
            row.addWidget(button)
        row.addStretch()
        return row

    @staticmethod
    def _time_text(segment: dict) -> str:
        return (f"{format_timecode(segment.get('start', 0))}  →  "
                f"{format_timecode(segment.get('end', 0))}")

    def segment_id(self) -> int:
        return self._id

    def set_text(self, text: str) -> None:
        if text != self.editor.toPlainText():
            self.editor.blockSignals(True)
            self.editor.setPlainText(text)
            self.editor.blockSignals(False)

    def set_times(self, segment: dict) -> None:
        self.time_label.setText(self._time_text(segment))

    def set_active(self, active: bool) -> None:
        """Tô sáng câu đang được đọc."""
        self.setStyleSheet(
            f"background: {tokens.BG_SELECTED_SOFT}; border-radius: 8px;"
            if active else "background: transparent;")


class SubtitleListPanel(QWidget):
    """Danh sách câu thoại có tìm kiếm và các nút thao tác từng câu."""

    text_edited = Signal(int, str)
    subtitle_edited = Signal(int, str)
    segment_selected = Signal(int)
    play_requested = Signal(int)
    resynth_requested = Signal(int)
    split_requested = Signal(int)
    merge_requested = Signal(int)
    delete_requested = Signal(int)
    add_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._segments: list[dict] = []
        self._filtered: list[dict] = []
        self._rows: dict[int, SegmentRow] = {}
        self._items: dict[int, QListWidgetItem] = {}
        self._text_field = "text_vi"
        self._active = -1
        self._split_mode = False
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(tokens.SP_2)

        head = QHBoxLayout()
        head.setSpacing(tokens.SP_2)
        title = QLabel("Lời thoại và phụ đề")
        title.setObjectName("cardTitle")
        head.addWidget(title)
        head.addStretch()
        self.count_label = QLabel("")
        self.count_label.setStyleSheet(
            f"color: {tokens.TEXT_MUTED}; font-size: {tokens.FS_META}px; "
            f"background: transparent;")
        head.addWidget(self.count_label)
        root.addLayout(head)

        self.search = SearchBox("Tìm trong lời thoại")
        self.search.search_changed.connect(self._apply_filter)
        root.addWidget(self.search)

        self.chk_split = QCheckBox("Phụ đề viết riêng, khác lời đọc")
        self.chk_split.setToolTip(
            "Bật khi bạn muốn chữ trên màn hình khác với chữ được đọc lên. "
            "Sửa phụ đề riêng chỉ cần ghi lại phụ đề vào video, không phải "
            "đọc lại giọng.")
        self.chk_split.toggled.connect(self._on_split_toggled)
        root.addWidget(self.chk_split)

        self.list = QListWidget()
        self.list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setUniformItemSizes(False)
        self.list.currentRowChanged.connect(self._on_row_changed)
        root.addWidget(self.list, 1)

        add_button = GhostButton("Thêm câu mới")
        add_button.clicked.connect(self.add_requested.emit)
        root.addWidget(add_button)

    # -- Dữ liệu -------------------------------------------------------
    def set_segments(self, segments: list[dict],
                     text_field: str = "text_vi") -> None:
        """Dựng lại toàn bộ danh sách."""
        self._segments = segments
        self._text_field = text_field
        self._apply_filter(self.search.text())

    def _apply_filter(self, query: str = "") -> None:
        text = (query or "").strip().lower()
        self._filtered = [s for s in self._segments
                          if not text
                          or text in str(s.get(self._text_field, "")).lower()]
        self._rebuild()

    def _on_split_toggled(self, checked: bool) -> None:
        self._split_mode = checked
        for row in self._rows.values():
            row.set_subtitle_visible(checked)
        # Chiều cao dòng đổi khi ô phụ đề hiện ra — dựng lại để không bị cắt.
        self._rebuild()

    def _rebuild(self) -> None:
        self.list.clear()
        self._rows.clear()
        self._items.clear()
        for segment in self._filtered:
            row = SegmentRow(segment, self._text_field, self._split_mode)
            row.text_edited.connect(self.text_edited.emit)
            row.subtitle_edited.connect(self.subtitle_edited.emit)
            row.play_requested.connect(self.play_requested.emit)
            row.resynth_requested.connect(self.resynth_requested.emit)
            row.split_requested.connect(self.split_requested.emit)
            row.merge_requested.connect(self.merge_requested.emit)
            row.delete_requested.connect(self.delete_requested.emit)
            row.height_changed.connect(self._on_row_height_changed)
            item = QListWidgetItem(self.list)
            item.setSizeHint(QSize(0, row.sizeHint().height()))
            item.setData(Qt.ItemDataRole.UserRole, row.segment_id())
            self.list.setItemWidget(item, row)
            self._rows[row.segment_id()] = row
            self._items[row.segment_id()] = item
        total = len(self._segments)
        shown = len(self._filtered)
        self.count_label.setText(
            f"{total} câu" if shown == total
            else f"{shown} trên {total} câu")

    def _on_row_height_changed(self, seg_id: int) -> None:
        """Nới dòng danh sách theo chiều cao mới của ô chữ — không cắt chữ."""
        row = self._rows.get(seg_id)
        item = self._items.get(seg_id)
        if row is None or item is None:
            return
        height = row.sizeHint().height()
        if item.sizeHint().height() != height:
            item.setSizeHint(QSize(0, height))

    def _on_row_changed(self, index: int) -> None:
        item = self.list.item(index)
        if item is not None:
            self.segment_selected.emit(int(item.data(Qt.ItemDataRole.UserRole)))

    def highlight(self, seg_id: int) -> None:
        """Tô sáng câu đang được đọc và cuộn tới nếu nó nằm ngoài tầm nhìn."""
        if seg_id == self._active:
            return
        previous = self._rows.get(self._active)
        if previous is not None:
            previous.set_active(False)
        self._active = seg_id
        row = self._rows.get(seg_id)
        if row is None:
            return
        row.set_active(True)
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == seg_id:
                if not self._is_visible(index):
                    self.list.scrollToItem(
                        item, QAbstractItemView.ScrollHint.PositionAtCenter)
                break

    def _is_visible(self, index: int) -> bool:
        """Dòng này có đang nằm trong vùng nhìn thấy không, tránh cuộn giật."""
        item = self.list.item(index)
        rect = self.list.visualItemRect(item)
        return self.list.viewport().rect().intersects(rect)

    def refresh_times(self, segments: list[dict]) -> None:
        """Cập nhật mốc thời gian sau khi người dùng kéo trên dải thời gian."""
        for segment in segments:
            row = self._rows.get(int(segment.get("id", 0)))
            if row is not None:
                row.set_times(segment)

    def selected_id(self) -> int:
        item = self.list.currentItem()
        return int(item.data(Qt.ItemDataRole.UserRole)) if item else -1

    def focus_search(self) -> None:
        self.search.focus()


class OverviewPanel(QScrollArea):
    """Thông tin chung của dự án và các nút mở nhanh."""

    open_folder = Signal()
    open_subtitle = Signal()
    open_youtube = Signal()
    open_other = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        holder = QWidget()
        clear_background(holder)
        self._layout = QVBoxLayout(holder)
        self._layout.setContentsMargins(0, 0, tokens.SP_2, 0)
        self._layout.setSpacing(tokens.SP_3)

        self._rows: dict[str, ElidedLabel] = {}
        info = CollapsibleSection("Thông tin dự án", expanded=True)
        for key, label in (("title", "Tên dự án"), ("path", "Thư mục"),
                           ("language", "Ngôn ngữ gốc"), ("voice", "Giọng đọc"),
                           ("segments", "Số câu thoại"),
                           ("duration", "Thời lượng"),
                           ("processing", "Thời gian đã xử lý"),
                           ("size", "Dung lượng")):
            info.add_layout(self._info_row(key, label))
        self._layout.addWidget(info)

        self.quality = CollapsibleSection("Chất lượng bản lồng tiếng",
                                          expanded=True)
        self.quality_label = QLabel("Chưa có số liệu.")
        self.quality_label.setWordWrap(True)
        self.quality_label.setStyleSheet(
            f"color: {tokens.TEXT_SECONDARY}; font-size: {tokens.FS_META}px; "
            f"background: transparent;")
        self.quality.add_widget(self.quality_label)
        self._layout.addWidget(self.quality)

        actions = CollapsibleSection("Mở nhanh", expanded=True)
        for text, signal in (("Mở thư mục dự án", self.open_folder),
                             ("Mở tệp phụ đề", self.open_subtitle),
                             ("Mở thư mục tiêu đề và mô tả", self.open_youtube),
                             ("Mở thư mục dự án khác…", self.open_other)):
            row = QHBoxLayout()
            button = GhostButton(text)
            button.clicked.connect(signal.emit)
            row.addWidget(button)
            row.addStretch()
            actions.add_layout(row)
        self._layout.addWidget(actions)
        self._layout.addStretch()
        self.setWidget(holder)

    def _info_row(self, key: str, label: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(tokens.SP_2)
        name = QLabel(label)
        name.setStyleSheet(
            f"color: {tokens.TEXT_MUTED}; font-size: {tokens.FS_META}px; "
            f"background: transparent;")
        name.setMinimumWidth(130)
        value = ElidedLabel("—")
        value.setStyleSheet(
            f"color: {tokens.TEXT_PRIMARY}; font-size: {tokens.FS_META}px; "
            f"background: transparent;")
        row.addWidget(name)
        row.addWidget(value, 1)
        self._rows[key] = value
        return row

    def set_project(self, project, segments: int, quality: dict) -> None:
        """Đổ thông tin của một dự án vào bảng."""
        values = {
            "title": project.title,
            "path": project.work_dir,
            "language": project.source_lang or "không rõ",
            "voice": project.voice or "không rõ",
            "segments": str(segments),
            "duration": format_duration(project.duration_s),
            "processing": format_hours(project.processing_s),
            "size": format_size(project.size_bytes),
        }
        for key, text in values.items():
            self._rows[key].setText(text)
        self.quality_label.setText(_quality_text(quality))

    def set_voice(self, name: str) -> None:
        """Cập nhật riêng dòng giọng đọc sau khi đã phân giải được tên thật."""
        if name:
            self._rows["voice"].setText(name)


def _quality_text(quality: dict) -> str:
    """Diễn giải bản đánh giá chất lượng bằng lời thường."""
    summary = (quality or {}).get("summary") or {}
    if not summary:
        return "Chưa có số liệu chất lượng cho dự án này."
    total = summary.get("segments_total", "—")
    ok = summary.get("segments_ok", "—")
    overlapped = summary.get("segments_overlapped", 0)
    lines = [f"{ok} trên {total} câu khớp thời lượng."]
    if overlapped:
        lines.append(
            f"Còn {overlapped} câu bị chồng sang câu sau. Hãy giảm Tốc độ "
            "video hoặc tăng Tốc độ giọng đọc rồi xuất lại.")
    else:
        lines.append("Không còn câu nào bị chồng tiếng.")
    hint = (quality or {}).get("hint")
    if hint:
        lines.append(str(hint))
    return " ".join(lines)


class AudioPanel(CollapsibleSection):
    """Tinh chỉnh âm thanh, lưu riêng cho từng dự án."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Âm thanh của dự án này", expanded=True, parent=parent)
        self.postprocess = QCheckBox("Làm đều độ lớn giọng đọc")
        self.postprocess.setToolTip(
            "Cân bằng để câu nào cũng nghe rõ như nhau.")
        self.loudness = LabeledSlider(
            "Độ lớn giọng đọc", -24.0, -10.0, 0.5,
            "Càng gần 0 thì giọng càng to.", " dB", decimals=1)
        self.duck = LabeledSlider(
            "Giảm nhạc nền khi có lời", -24.0, 0.0, 0.5,
            "Nhạc nền tự nhỏ đi bấy nhiêu mỗi khi có lời thoại.",
            " dB", decimals=1)
        self.soft_timing = QCheckBox("Tự căn lại thời điểm từng câu")
        self.drift = LabeledSlider(
            "Cho phép lệch tối đa", 0.0, 5.0, 0.1,
            "Mỗi câu được dịch đi nhiều nhất bấy nhiêu giây.",
            " giây", decimals=1)
        for widget in (self.postprocess, self.loudness, self.duck,
                       self.soft_timing, self.drift):
            self.add_widget(widget)
        for widget in (self.loudness, self.duck, self.drift):
            widget.changed.connect(lambda _v: self.changed.emit())
        for box in (self.postprocess, self.soft_timing):
            box.toggled.connect(lambda _c: self.changed.emit())

    def load(self, opts: dict, settings) -> None:
        """Nạp từ tùy chọn của dự án, thiếu thì lấy theo cài đặt chung."""
        self.postprocess.setChecked(
            bool(opts.get("voice_postprocess", settings.voice_postprocess)))
        self.loudness.set_value(
            float(opts.get("voice_target_lufs", settings.voice_target_lufs)))
        self.duck.set_value(
            float(opts.get("bg_duck_voice_db", settings.bg_duck_voice_db)))
        self.soft_timing.setChecked(
            bool(opts.get("soft_timing_fit", settings.soft_timing_fit)))
        self.drift.set_value(
            float(opts.get("timing_max_drift_s", settings.timing_max_drift_s)))

    def values(self) -> dict:
        return {
            "voice_postprocess": self.postprocess.isChecked(),
            "voice_target_lufs": self.loudness.value(),
            "bg_duck_voice_db": self.duck.value(),
            "soft_timing_fit": self.soft_timing.isChecked(),
            "timing_max_drift_s": self.drift.value(),
        }


class VoicePanel(CollapsibleSection):
    """Chọn giọng theo tên và đọc lại những câu đã sửa."""

    preview_requested = Signal(str)      # tên giọng
    resynth_all_requested = Signal()
    changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Giọng đọc", expanded=True, parent=parent)
        from autodub_gui.voice_picker import VoicePicker

        self.picker = VoicePicker("Giọng đọc của video này")
        self.picker.setToolTip(
            "Đây là giọng video này đang dùng. Đổi giọng ở đây rồi bấm "
            "«Lưu tất cả và đọc lại» để đọc lại toàn bộ bằng giọng mới — "
            "cài đặt chung không bị ảnh hưởng.")
        self.picker.changed.connect(self._on_voice_changed)
        self.picker.preview_requested.connect(self.preview_requested.emit)
        self.add_widget(self.picker)

        self.voice_hint = QLabel("")
        self.voice_hint.setWordWrap(True)
        self.voice_hint.setStyleSheet(
            f"color: {tokens.WARNING}; font-size: {tokens.FS_META}px; "
            f"background: transparent;")
        self.voice_hint.setVisible(False)
        self.add_widget(self.voice_hint)

        self.speed = LabeledSlider(
            "Tốc độ đọc", 0.5, 2.0, 0.05,
            "1.00 là tốc độ tự nhiên.", "x")
        self.speed.set_value(1.0)
        self.speed.changed.connect(lambda _v: self.changed.emit())
        self.add_widget(self.speed)

        row = QHBoxLayout()
        row.setSpacing(tokens.SP_2)
        self.btn_resynth = PrimaryButton("Lưu tất cả và đọc lại")
        self.btn_resynth.setToolTip(
            "Lưu mọi câu bạn đã sửa rồi tạo lại giọng đọc cho những câu đó.")
        self.btn_resynth.clicked.connect(self.resynth_all_requested.emit)
        row.addWidget(self.btn_resynth)
        row.addStretch()
        self.add_layout(row)

        self.progress = ThinProgressBar()
        self.progress.setVisible(False)
        self.add_widget(self.progress)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(
            f"color: {tokens.TEXT_MUTED}; font-size: {tokens.FS_META}px; "
            f"background: transparent;")
        self.add_widget(self.status)
        self._project_voice = ""

    def set_project_voice(self, name: str) -> None:
        """Ghi nhớ giọng video này đang dùng thật, để so khi người dùng đổi."""
        self._project_voice = name
        self.picker.set_voice(name)
        self._refresh_hint()

    def _on_voice_changed(self) -> None:
        self._refresh_hint()
        self.changed.emit()

    def _refresh_hint(self) -> None:
        """Đổi giọng mà chưa đọc lại thì video vẫn là giọng cũ — phải nói rõ."""
        changed = (self._project_voice
                   and self.picker.voice() != self._project_voice)
        if changed:
            self.voice_hint.setText(
                f"Video đang dùng giọng {self._project_voice}. Bấm «Lưu tất "
                f"cả và đọc lại» để chuyển hẳn sang giọng "
                f"{self.picker.voice()}.")
        self.voice_hint.setVisible(bool(changed))

    def mark_voice_applied(self) -> None:
        """Gọi sau khi đọc lại xong: giọng đang chọn đã thành giọng của video."""
        self._project_voice = self.picker.voice()
        self._refresh_hint()

    def has_pending_voice_change(self) -> bool:
        """Người dùng đã đổi giọng nhưng chưa đọc lại toàn bộ."""
        return bool(self._project_voice
                    and self.picker.voice() != self._project_voice)

    def project_voice(self) -> str:
        """Giọng đang nằm thật trong âm thanh của video."""
        return self._project_voice or self.picker.voice()

    def set_progress(self, done: int, total: int) -> None:
        self.progress.setVisible(total > 0)
        self.progress.setValue(int(done / total * 100) if total else 0)
        self.status.setText(f"Đang đọc lại câu {done} trên {total}"
                            if total else "")

    def finish_progress(self, message: str) -> None:
        self.progress.setVisible(False)
        self.status.setText(message)

    def values(self) -> dict:
        # KHÔNG trả về "voice": khóa đó trong render_opts luôn là giọng đã
        # nằm thật trong âm thanh, chỉ được cập nhật sau khi đọc lại thành
        # công (xem editor_export._on_resynth_done).
        return {"voice_speed": self.speed.value()}


class BackgroundPanel(CollapsibleSection):
    """Xử lý nhạc nền của dự án."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Nhạc nền", expanded=True, parent=parent)
        self.mode = LabeledCombo("Cách xử lý", consts.BG_MODES,
                                 "Cách xử lý âm thanh gốc của video")
        self.mode.changed.connect(self.changed.emit)
        self.duck = LabeledSlider(
            "Mức giảm tiếng gốc", -40.0, 0.0, 1.0,
            "Càng âm thì tiếng gốc càng nhỏ.", " dB", decimals=0)
        self.duck.set_value(-12.0)
        self.duck.changed.connect(lambda _v: self.changed.emit())
        self.add_widget(self.mode)
        self.add_widget(self.duck)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(
            f"color: {tokens.TEXT_MUTED}; font-size: {tokens.FS_META}px; "
            f"background: transparent;")
        self.add_widget(self.status)

    def set_separated(self, available: bool) -> None:
        """Cho biết dự án đã có bản nhạc nền tách sẵn hay chưa."""
        self.status.setText(
            "Dự án này đã có bản nhạc nền tách sẵn, xuất lại sẽ rất nhanh."
            if available else
            "Dự án này chưa tách nhạc nền. Chọn Tách giọng gốc sẽ mất thêm "
            "thời gian ở lần xuất tới.")

    def values(self) -> dict:
        return {"bg_mode": self.mode.current_key(),
                "bg_duck_db": self.duck.value()}


class ExportPanel(CollapsibleSection):
    """Chọn kiểu phụ đề rồi xuất video, hoặc chỉ ghi lại phụ đề."""

    export_requested = Signal()
    subtitles_requested = Signal()
    style_requested = Signal()
    changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Xuất video", expanded=True, parent=parent)
        from autodub.media.subtitle import PRESET_CHOICES

        self.subtitle = LabeledCombo("Kiểu phụ đề", consts.SUBTITLE_MODES,
                                     "Cách hiện phụ đề trên video kết quả")
        self.subtitle.changed.connect(self.changed.emit)
        self.add_widget(self.subtitle)

        self.preset = LabeledCombo(
            "Bộ kiểu chữ", PRESET_CHOICES,
            "Đổi bộ kiểu rồi bấm Ghi lại phụ đề là thấy ngay trên video.")
        self.preset.changed.connect(self.changed.emit)
        self.add_widget(self.preset)

        row = QHBoxLayout()
        style = GhostButton("Kiểu chữ và vùng che…")
        style.clicked.connect(self.style_requested.emit)
        row.addWidget(style)
        row.addStretch()
        self.add_layout(row)

        self.source_info = QLabel("")
        self.source_info.setWordWrap(True)
        self.source_info.setStyleSheet(
            f"color: {tokens.TEXT_MUTED}; font-size: {tokens.FS_META}px; "
            f"background: transparent;")
        self.add_widget(self.source_info)

        self.btn_subtitles = GhostButton("Ghi lại phụ đề vào video")
        self.btn_subtitles.setToolTip(
            "Chỉ vẽ lại chữ lên video, giữ nguyên giọng đọc đã có. Nhanh hơn "
            "nhiều so với xuất lại cả video, dùng khi bạn chỉ sửa chữ hoặc "
            "đổi kiểu chữ.")
        self.btn_subtitles.clicked.connect(self.subtitles_requested.emit)
        self.btn_export = PrimaryButton("Xuất video")
        self.btn_export.setToolTip(
            "Ghép lại cả âm thanh lẫn hình. Dùng khi bạn vừa đọc lại giọng "
            "hoặc đổi nhạc nền.")
        self.btn_export.clicked.connect(self.export_requested.emit)
        # Hai nút xếp DỌC: bảng bên phải có thể hẹp tới 280 điểm, đặt cạnh
        # nhau là nhãn dài («Ghi lại phụ đề vào video») bị ép cắt chữ.
        for button in (self.btn_export, self.btn_subtitles):
            row = QHBoxLayout()
            row.setSpacing(tokens.SP_2)
            row.addWidget(button)
            row.addStretch()
            self.add_layout(row)

        self.progress = ThinProgressBar()
        self.progress.setVisible(False)
        self.add_widget(self.progress)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(
            f"color: {tokens.TEXT_MUTED}; font-size: {tokens.FS_META}px; "
            f"background: transparent;")
        self.add_widget(self.status)

    def set_source_info(self, width: int, height: int, fps: float) -> None:
        """Hiện thông số video gốc; bản này giữ nguyên chứ chưa đổi được."""
        if width and height:
            self.source_info.setText(
                f"Video kết quả giữ nguyên thông số của video gốc: "
                f"{width} nhân {height} điểm ảnh, {fps:.0f} hình mỗi giây.")
        else:
            self.source_info.setText(
                "Video kết quả giữ nguyên độ phân giải và số hình mỗi giây "
                "của video gốc.")

    def set_running(self, running: bool, subtitles_only: bool = False) -> None:
        if subtitles_only:
            self.btn_subtitles.set_loading(running, "Đang ghi phụ đề")
            self.btn_export.setEnabled(not running)
        else:
            self.btn_export.set_loading(running, "Đang xuất video")
            self.btn_subtitles.setEnabled(not running)
        self.progress.setVisible(running)
        if running:
            self.progress.set_indeterminate(True)

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def values(self) -> dict:
        return {"subtitle_mode": self.subtitle.current_key(),
                "subtitle_preset": self.preset.current_key()}


class DirtyBanner(QWidget):
    """Nhắc việc còn phải làm sau khi sửa: đọc lại giọng, hay ghi lại phụ đề.

    Hai loại thay đổi có hai việc phải làm khác hẳn nhau, nên băng nhắc phải
    nói rõ từng loại — không thì người dùng bấm Xuất video mà chữ trên phim
    vẫn là chữ cũ, hoặc ngược lại, xuất lại cả video chỉ vì sửa một dấu phẩy.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(tokens.SP_3, tokens.SP_2,
                                  tokens.SP_3, tokens.SP_2)
        self.label = QLabel("")
        self.label.setWordWrap(True)
        self.label.setStyleSheet(
            f"color: {tokens.WARNING}; font-size: {tokens.FS_META}px; "
            f"background: transparent;")
        layout.addWidget(self.label)
        self.setStyleSheet(
            f"background: {tokens.WARNING_BG}; "
            f"border: 1px solid {tokens.WARNING}; border-radius: 8px;")
        self.setVisible(False)

    def set_count(self, voice_count: int, subtitle_count: int = 0) -> None:
        """Hiện số câu đã sửa và việc cần làm tiếp theo."""
        self.setVisible(bool(voice_count or subtitle_count))
        parts: list[str] = []
        if voice_count:
            parts.append(
                f"Đã sửa lời đọc của {voice_count} câu — bấm «Lưu tất cả và "
                "đọc lại» ở mục Giọng đọc, rồi bấm «Xuất video».")
        if subtitle_count:
            parts.append(
                f"Đã sửa phụ đề của {subtitle_count} câu — bấm «Ghi lại phụ "
                "đề vào video» ở mục Xuất video là xong, không cần đọc lại "
                "giọng.")
        self.label.setText(" ".join(parts))


def debounce_timer(parent: QWidget, callback) -> QTimer:
    """Bộ đếm giờ chờ người dùng gõ xong rồi mới lưu."""
    timer = QTimer(parent)
    timer.setSingleShot(True)
    timer.setInterval(EDIT_DEBOUNCE_MS)
    timer.timeout.connect(callback)
    return timer
