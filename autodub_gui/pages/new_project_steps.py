"""Sáu ô cấu hình của trang Tạo dự án.

Mỗi bước là một widget độc lập, tự giữ giá trị của mình và báo ra ngoài khi
có thay đổi. Trang cha chỉ lo chuyển qua lại giữa các bước và gom dữ liệu.
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from autodub_gui import dub_constants as consts
from autodub_gui import tokens
from autodub_gui.formatting import format_size
from autodub_gui.ui.buttons import GhostButton, SegmentedControl
from autodub_gui.ui.inputs import (
    LabeledCombo, LabeledLineEdit, LabeledSlider, LabeledWidget,
)
from autodub_gui.ui.labels import ElidedLabel
from autodub_gui.ui.style import clear_background

STEP_NAMES = ("Video", "Nhận dạng", "Dịch thuật", "Giọng đọc",
              "Phụ đề", "Xuất video")

VIDEO_FILTER = ("Video (*.mp4 *.mkv *.mov *.avi *.webm);;Tất cả tệp (*.*)")
_LARGE_FILE_BYTES = 4 * 1024 ** 3


class _StepPanel(QWidget):
    """Khung chung cho một bước: tiêu đề, mô tả ngắn rồi tới các ô nhập."""

    changed = Signal()

    def __init__(self, title: str, description: str,
                 parent: QWidget | None = None):
        super().__init__(parent)
        # Mỗi bước nằm trong một thẻ — để nền trong suốt thì nó ăn theo nền
        # của thẻ, không tự vẽ ra một khối tối rời rạc bên trong.
        clear_background(self)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(tokens.SP_4)
        heading = QLabel(title)
        heading.setStyleSheet(
            f"color: {tokens.TEXT_PRIMARY}; font-size: {tokens.FS_CARD_TITLE}px; "
            f"font-weight: 700; background: transparent;")
        note = QLabel(description)
        note.setWordWrap(True)
        note.setStyleSheet(
            f"color: {tokens.TEXT_MUTED}; font-size: {tokens.FS_META}px; "
            f"background: transparent;")
        self.body.addWidget(heading)
        self.body.addWidget(note)

    def finish(self) -> None:
        """Đẩy phần còn lại lên trên, gọi sau khi thêm hết các ô nhập."""
        self.body.addStretch()

    def is_complete(self) -> tuple[bool, str]:
        """Bước này đã đủ dữ liệu chưa, kèm lý do nếu chưa."""
        return True, ""


class VideoStep(_StepPanel):
    """Bước 1: chọn nguồn video."""

    SOURCES = [("Dán liên kết", "url"), ("Tải tệp lên", "file"),
               ("Tiếp tục dang dở", "resume")]

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Chọn video", "Dán liên kết, chọn tệp từ máy, hoặc "
                                       "chạy tiếp một dự án đang dở.", parent)
        self.source = SegmentedControl(self.SOURCES)
        self.source.selection_changed.connect(self._on_source)
        self.body.addWidget(LabeledWidget("Nguồn video", self.source))

        self.url = LabeledLineEdit(
            "Liên kết video", "https://www.youtube.com/watch?v=...",
            "Dán liên kết YouTube, Douyin hoặc liên kết tải trực tiếp")
        self.url.changed.connect(lambda _t: self.changed.emit())
        self.body.addWidget(self.url)

        self.file_row, self.file_edit = self._picker(
            "Tệp video trên máy", "Chưa chọn tệp nào", self._pick_file)
        self.body.addWidget(self.file_row)

        self.resume_row, self.resume_edit = self._picker(
            "Thư mục dự án đang dở", "Chọn thư mục kết quả của lần chạy trước",
            self._pick_folder)
        self.body.addWidget(self.resume_row)

        self.info = ElidedLabel("")
        self.info.setStyleSheet(
            f"color: {tokens.TEXT_SECONDARY}; font-size: {tokens.FS_META}px; "
            f"background: transparent;")
        self.body.addWidget(self.info)
        self.finish()
        self._on_source("url")

    def _picker(self, label: str, placeholder: str,
                handler) -> tuple[QWidget, LabeledLineEdit]:
        holder = QWidget()
        clear_background(holder)     # ăn theo nền của thẻ chứa nó
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(tokens.SP_2)
        edit = LabeledLineEdit(label, placeholder)
        edit.changed.connect(lambda _t: self._on_path_changed())
        row = QHBoxLayout()
        row.addStretch()
        button = GhostButton("Chọn…")
        button.clicked.connect(handler)
        row.addWidget(button)
        layout.addWidget(edit)
        layout.addLayout(row)
        return holder, edit

    def _on_source(self, key: str) -> None:
        self.url.setVisible(key == "url")
        self.file_row.setVisible(key == "file")
        self.resume_row.setVisible(key == "resume")
        self.changed.emit()

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn video", os.path.expanduser("~"), VIDEO_FILTER)
        if path:
            self.file_edit.set_text(path)
            self.source.set_key("file")
            self._on_source("file")

    def _pick_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Chọn thư mục dự án đang dở", "output")
        if path:
            self.resume_edit.set_text(path)
            self.source.set_key("resume")
            self._on_source("resume")

    def _on_path_changed(self) -> None:
        path = self.file_edit.text()
        if path and os.path.isfile(path):
            size = os.path.getsize(path)
            warn = ("  —  video rất lớn, xử lý có thể mất nhiều giờ"
                    if size > _LARGE_FILE_BYTES else "")
            self.info.setText(f"{os.path.basename(path)} · "
                              f"{format_size(size)}{warn}")
        else:
            self.info.setText("")
        self.changed.emit()

    def set_file(self, path: str) -> None:
        """Điền sẵn tệp khi người dùng kéo thả từ Trang chủ."""
        self.source.set_key("file")
        self._on_source("file")
        self.file_edit.set_text(path)

    def values(self) -> dict:
        return {
            "source": self.source.current_key(),
            "url": self.url.text(),
            "file_path": self.file_edit.text(),
            "resume_dir": self.resume_edit.text(),
        }

    def load(self, data: dict) -> None:
        self.source.set_key(data.get("source", "url"))
        self.url.set_text(data.get("url", ""))
        self.file_edit.set_text(data.get("file_path", ""))
        self.resume_edit.set_text(data.get("resume_dir", ""))
        self._on_source(self.source.current_key())

    def is_complete(self) -> tuple[bool, str]:
        key = self.source.current_key()
        if key == "url" and not self.url.text():
            return False, "Hãy dán liên kết video trước khi đi tiếp."
        if key == "file":
            path = self.file_edit.text()
            if not path:
                return False, "Hãy chọn một tệp video trước khi đi tiếp."
            if not os.path.isfile(path):
                return False, "Không tìm thấy tệp này nữa. Hãy chọn lại."
        if key == "resume" and not os.path.isdir(self.resume_edit.text()):
            return False, "Hãy chọn thư mục dự án đang dở có thật trên máy."
        return True, ""


class RecognizeStep(_StepPanel):
    """Bước 2: nghe và chép lời video gốc."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Nghe và chép lời",
                         "Ứng dụng nghe video gốc rồi chép lại thành chữ. "
                         "Chép càng đúng thì bản dịch càng sát.", parent)
        self.engine = LabeledCombo(
            "Bộ nhận dạng", consts.ASR_ENGINES,
            "Whisper nghe được mọi ngôn ngữ. Paraformer chính xác hơn với "
            "video tiếng Trung nhưng phải cài thêm một lần.")
        self.model = LabeledCombo(
            "Độ chính xác", consts.WHISPER_MODELS,
            "Mức càng cao thì nghe càng đúng nhưng chạy càng lâu và tải "
            "về càng nặng.")
        self.language = LabeledCombo(
            "Ngôn ngữ trong video", consts.SOURCE_LANGS,
            "Cho biết video gốc nói tiếng gì.")
        self.auto_detect = QCheckBox("Để ứng dụng tự nhận ra ngôn ngữ")
        self.auto_detect.setToolTip(
            "Bật khi bạn không chắc video nói tiếng gì. Tắt thì dùng đúng "
            "ngôn ngữ bạn chọn ở trên, thường chính xác hơn.")
        self.auto_detect.toggled.connect(self._on_auto)
        for widget in (self.engine, self.model, self.language):
            widget.changed.connect(self.changed.emit)
        self.body.addWidget(self.engine)
        self.body.addWidget(self.model)
        self.body.addWidget(self.language)
        self.body.addWidget(self.auto_detect)
        self.finish()

    def _on_auto(self, checked: bool) -> None:
        self.language.setEnabled(not checked)
        self.changed.emit()

    def values(self) -> dict:
        return {
            "asr_engine": self.engine.current_key(),
            "whisper_model": self.model.current_key(),
            "source_lang": self.language.current_key(),
            "auto_detect": self.auto_detect.isChecked(),
        }

    def load(self, data: dict) -> None:
        self.engine.set_key(data.get("asr_engine", "whisper"))
        self.model.set_key(data.get("whisper_model", "auto"))
        self.language.set_key(data.get("source_lang", "zh-CN"))
        self.auto_detect.setChecked(bool(data.get("auto_detect", False)))


class TranslateStep(_StepPanel):
    """Bước 3: dịch sang tiếng Việt."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Dịch sang tiếng Việt",
                         "Chọn giọng văn cho bản dịch. Ngôn ngữ đích luôn là "
                         "tiếng Việt.", parent)
        self.source_view = QLabel("")
        self.source_view.setStyleSheet(
            f"color: {tokens.TEXT_SECONDARY}; font-size: {tokens.FS_BODY}px; "
            f"background: {tokens.BG_INPUT}; border-radius: 8px; "
            f"padding: 8px 12px;")
        self.body.addWidget(LabeledWidget(
            "Dịch từ", self.source_view,
            "Lấy theo ngôn ngữ bạn chọn ở bước Nghe và chép lời."))

        target = QLabel("Tiếng Việt")
        target.setStyleSheet(
            f"color: {tokens.TEXT_PRIMARY}; font-size: {tokens.FS_BODY}px; "
            f"font-weight: 600; background: {tokens.BG_INPUT}; "
            f"border-radius: 8px; padding: 8px 12px;")
        self.body.addWidget(LabeledWidget(
            "Dịch sang", target, "Bản này chỉ lồng tiếng Việt."))

        self.style = LabeledCombo(
            "Phong cách dịch",
            [(label, key) for label, key, _note in consts.TRANSLATE_STYLES],
            "Quyết định giọng văn của bản dịch, ví dụ trang trọng hay đời thường.")
        self.engine = LabeledCombo(
            "Dịch bằng", consts.TRANSLATE_ENGINES,
            "Để nguyên Theo cài đặt chung nếu bạn không có nhu cầu đặc biệt.")
        self.note = LabeledLineEdit(
            "Ghi chú thêm cho người dịch",
            "ví dụ: giữ tên nhân vật Hán Việt, xưng hô mình với các bạn",
            "Ghi chú này được gửi kèm mỗi lần dịch.")
        for widget in (self.style, self.engine):
            widget.changed.connect(self.changed.emit)
        self.note.changed.connect(lambda _t: self.changed.emit())
        self.body.addWidget(self.style)
        self.body.addWidget(self.engine)
        self.body.addWidget(self.note)
        self.finish()

    def set_source_language(self, label: str) -> None:
        self.source_view.setText(label)

    def values(self) -> dict:
        return {
            "translate_style": self.style.current_key(),
            "translate_engine": self.engine.current_key(),
            "translate_note": self.note.text(),
        }

    def load(self, data: dict) -> None:
        self.style.set_key(data.get("translate_style", "natural"))
        self.engine.set_key(data.get("translate_engine", ""))
        self.note.set_text(data.get("translate_note", ""))


class VoiceStep(_StepPanel):
    """Bước 4: dùng giọng mặc định; muốn giọng khác thì mở phần đổi giọng."""

    preview_requested = Signal(str)     # tên giọng

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Giọng đọc tiếng Việt",
                         "Video này sẽ đọc bằng giọng mặc định bạn chọn trong "
                         "Cài đặt. Chỉ mở phần đổi giọng khi muốn riêng video "
                         "này dùng giọng khác.",
                         parent)
        from autodub_gui.ui.collapsible import CollapsibleSection
        from autodub_gui.voice_picker import VoicePicker

        # Giọng mặc định đang dùng + nút nghe thử ngay
        default_row = QHBoxLayout()
        default_row.setSpacing(tokens.SP_2)
        self._default_label = ElidedLabel("")
        self._default_label.setStyleSheet(
            f"color: {tokens.TEXT_PRIMARY}; font-size: {tokens.FS_BODY}px; "
            f"font-weight: 600; background: transparent;")
        self._btn_default_preview = GhostButton("Nghe thử")
        self._btn_default_preview.clicked.connect(
            lambda: self.preview_requested.emit(self._default_voice()))
        default_row.addWidget(self._default_label, 1)
        default_row.addWidget(self._btn_default_preview)
        self.body.addLayout(default_row)

        # Đổi giọng riêng — gập lại mặc định; mở ra nghĩa là muốn ghi đè.
        self._override = CollapsibleSection("Đổi giọng riêng cho video này")
        self._override.toggled.connect(lambda _e: self.changed.emit())
        self.picker = VoicePicker("Giọng đọc")
        self.picker.changed.connect(self.changed.emit)
        self.picker.preview_requested.connect(self.preview_requested.emit)
        self._override.add_widget(self.picker)
        self.body.addWidget(self._override)

        self.speed = LabeledSlider(
            "Tốc độ đọc", 0.5, 2.0, 0.05,
            "1.00 là tốc độ tự nhiên. Tăng lên khi câu tiếng Việt dài hơn câu "
            "gốc và bị chồng sang câu sau.", "x")
        self.speed.set_value(1.0)
        self.speed.changed.connect(lambda _v: self.changed.emit())
        self.body.addWidget(self.speed)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(
            f"color: {tokens.TEXT_MUTED}; font-size: {tokens.FS_META}px; "
            f"background: transparent;")
        self.body.addWidget(self.status)
        self.finish()
        self._refresh_default_label()

    @staticmethod
    def _default_voice() -> str:
        """Tên giọng mặc định trong Cài đặt, đọc lại mỗi lần cần."""
        from autodub.speech.tts.voices import DEFAULT_VOICE

        try:
            from autodub.config import Settings
            return Settings.load(override=True).vieneu_voice or DEFAULT_VOICE
        except Exception:  # noqa: BLE001 — cấu hình hỏng thì dùng giọng gốc
            return DEFAULT_VOICE

    def _refresh_default_label(self) -> None:
        self._default_label.setText(
            f"Giọng mặc định: {self._default_voice()}")
        self._default_label.setToolTip(
            "Đổi giọng mặc định trong Cài đặt, thẻ Giọng đọc.")

    def showEvent(self, event) -> None:  # noqa: N802 — theo quy ước của Qt
        # Người dùng có thể vừa đổi giọng mặc định trong Cài đặt.
        self._refresh_default_label()
        super().showEvent(event)

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def values(self) -> dict:
        # Không mở phần đổi giọng thì trả về rỗng — pipeline sẽ tự dùng
        # giọng mặc định trong Cài đặt.
        return {
            "voice": (self.picker.voice()
                      if self._override.is_expanded() else ""),
            "voice_speed": self.speed.value(),
        }

    def load(self, data: dict) -> None:
        voice = (data.get("voice") or "").strip()
        self.picker.reload()
        if voice:
            self.picker.set_voice(voice)
        self._override.set_expanded(bool(voice))
        self.speed.set_value(float(data.get("voice_speed", 1.0)))
        self._refresh_default_label()


class SubtitleStep(_StepPanel):
    """Bước 5: phụ đề."""

    style_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Phụ đề",
                         "Chọn cách hiện phụ đề trên video kết quả. Sau khi "
                         "chạy xong bạn vẫn sửa được chữ và kiểu chữ trong "
                         "Trình chỉnh sửa, không phải chạy lại.", parent)
        from autodub.media.subtitle import PRESET_CHOICES

        self.mode = LabeledCombo(
            "Kiểu phụ đề", consts.SUBTITLE_MODES,
            "Phụ đề rời là tệp riêng, người xem tự bật tắt. Ghi thẳng vào "
            "hình thì chữ nằm luôn trên video.")
        self.mode.changed.connect(self.changed.emit)
        self.body.addWidget(self.mode)

        self.preset = LabeledCombo(
            "Bộ kiểu chữ", PRESET_CHOICES,
            "Chọn một bộ có sẵn là xong. Muốn tự quyết từng thông số thì bấm "
            "Kiểu chữ và vùng che.")
        self.preset.changed.connect(self.changed.emit)
        self.body.addWidget(self.preset)

        row = QHBoxLayout()
        row.setSpacing(tokens.SP_2)
        self.btn_style = GhostButton("Kiểu chữ và vùng che…")
        self.btn_style.clicked.connect(self.style_requested.emit)
        row.addWidget(self.btn_style)
        row.addStretch()
        self.body.addLayout(row)

        self.summary = QLabel("Kiểu mặc định, chưa che vùng nào")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(
            f"color: {tokens.TEXT_MUTED}; font-size: {tokens.FS_META}px; "
            f"background: transparent;")
        self.body.addWidget(self.summary)
        self.finish()

    def set_summary(self, text: str) -> None:
        self.summary.setText(text)

    def values(self) -> dict:
        return {"subtitle_mode": self.mode.current_key(),
                "subtitle_preset": self.preset.current_key()}

    def load(self, data: dict) -> None:
        self.mode.set_key(data.get("subtitle_mode", "none"))
        self.preset.set_key(data.get("subtitle_preset", "clean"))


class ExportStep(_StepPanel):
    """Bước 6: nhạc nền và bảng tóm tắt trước khi chạy."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Xuất video",
                         "Chọn cách xử lý nhạc nền rồi xem lại toàn bộ lựa "
                         "chọn trước khi bắt đầu.", parent)
        self.background = LabeledCombo(
            "Nhạc nền", consts.BG_MODES,
            "Tách giọng gốc giữ được nhạc nền hay nhất nhưng chạy lâu hơn.")
        self.background.changed.connect(self._on_background)
        self.duck = LabeledSlider(
            "Mức giảm tiếng gốc", -40.0, 0.0, 1.0,
            "Càng âm thì tiếng gốc càng nhỏ khi có lời thoại tiếng Việt.",
            " dB", decimals=0)
        self.duck.set_value(-12.0)
        self.duck.changed.connect(lambda _v: self.changed.emit())
        self.audio_only = QCheckBox("Chỉ xuất âm thanh và phụ đề, bỏ ghép video")
        self.audio_only.setToolTip(
            "Bật khi bạn tự dựng video ở phần mềm khác và chỉ cần tiếng Việt "
            "cùng tệp phụ đề.")
        self.audio_only.toggled.connect(lambda _c: self.changed.emit())
        self.body.addWidget(self.background)
        self.body.addWidget(self.duck)
        self.body.addWidget(self.audio_only)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        self.summary.setStyleSheet(
            f"color: {tokens.TEXT_SECONDARY}; font-size: {tokens.FS_META}px; "
            f"background: {tokens.BG_INPUT}; border-radius: 8px; "
            f"padding: 10px 12px;")
        self.body.addWidget(LabeledWidget("Tóm tắt lựa chọn", self.summary))
        self.finish()
        self._on_background()

    def _on_background(self) -> None:
        self.duck.setEnabled(self.background.current_key() == "duck")
        self.changed.emit()

    def set_summary(self, rows: list[tuple[str, str]]) -> None:
        """Đổ bảng tóm tắt hai cột."""
        lines = [f"<b>{name}:</b> {value}" for name, value in rows]
        self.summary.setText("<br>".join(lines))

    def values(self) -> dict:
        return {
            "bg_mode": self.background.current_key(),
            "bg_duck_db": self.duck.value(),
            "skip_video": self.audio_only.isChecked(),
        }

    def load(self, data: dict) -> None:
        self.background.set_key(data.get("bg_mode", "demucs"))
        self.duck.set_value(float(data.get("bg_duck_db", -12.0)))
        self.audio_only.setChecked(bool(data.get("skip_video", False)))
        self._on_background()
