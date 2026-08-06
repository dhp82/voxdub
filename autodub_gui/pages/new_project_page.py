"""Trang Tạo dự án: sáu bước lưỡng dụng.

Trước khi chạy, sáu bước là một trình hướng dẫn để cấu hình. Trong khi chạy,
chính sáu bước đó phản ánh tiến độ thật của quá trình xử lý, nên người dùng
không phải học thêm cách đọc nào khác.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from autodub.media.subtitle import PRESET_CHOICES
from autodub.pipeline import DubRequest, DubResult
from autodub_gui import dub_constants as consts
from autodub_gui import icons, tokens
from autodub_gui.pages import BasePage
from autodub_gui.pages.new_project_steps import (
    STEP_NAMES, ExportStep, RecognizeStep, SubtitleStep, TranslateStep,
    VideoStep, VoiceStep,
)
from autodub_gui.run_state import LEVEL_INFO, REGISTRY, ActiveJob
from autodub_gui.system_open import open_file, open_folder
from autodub_gui.ui.buttons import DangerButton, GhostButton, PrimaryButton
from autodub_gui.ui.cards import Card
from autodub_gui.ui.empty import EmptyState
from autodub_gui.ui.modal import ConfirmDialog
from autodub_gui.ui.stepper import Stepper
from autodub_gui.ui.style import clear_background
from autodub_gui.ui.toast import TOASTS
from autodub_gui.voice_preview import VoicePreview
from autodub_gui.widgets import Banner, LogPanel, StepTracker
from autodub_gui.workers import DubWorker

DRAFT_FILE = "draft_project.json"
_DRAFT_DEBOUNCE_MS = 800
_PAGE_MARGIN = 28
_FORM_MAX_W = 460
_FORM_MIN_W = 390          # đủ chỗ cho bước rộng nhất — không thì cắt mép phải
_PREVIEW_STRETCH = 6
_FORM_STRETCH = 4

# Bước nào của quá trình xử lý ứng với ô nào trên thanh sáu bước
_STEP_TO_INDEX = {
    "acquire": 0, "extract": 0,
    "separate": 1, "asr": 1,
    "translate": 2,
    "tts": 3,
    "merge_audio": 5, "merge_video": 5, "content": 5,
}


def cache_dir() -> str:
    """Thư mục lưu bản nháp và dữ liệu tạm của giao diện."""
    path = os.path.join(os.path.expanduser("~"), ".voxdub_cache")
    os.makedirs(path, exist_ok=True)
    return path


class NewProjectPage(BasePage):
    """Trình hướng dẫn tạo một dự án lồng tiếng."""

    settings_needed = Signal(str)
    edit_requested = Signal(str)
    home_requested = Signal()

    def __init__(self, settings_provider, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings_provider = settings_provider
        self._worker: DubWorker | None = None
        self._result: DubResult | None = None
        self._blur_regions: list[dict] = []
        self._subtitle_style: dict | None = None
        self._preview = VoicePreview(self)
        self._draft_timer = QTimer(self)
        self._draft_timer.setSingleShot(True)
        self._draft_timer.setInterval(_DRAFT_DEBOUNCE_MS)
        self._draft_timer.timeout.connect(self._save_draft)
        self._build()
        self._load_draft()
        REGISTRY.job_changed.connect(self._sync_live_stepper)

    # -- Dựng giao diện ------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(_PAGE_MARGIN, tokens.SP_2,
                                _PAGE_MARGIN, tokens.SP_5)
        root.setSpacing(tokens.SP_4)

        self.stepper = Stepper(list(STEP_NAMES))
        self.stepper.step_clicked.connect(self._go_to_step)
        root.addWidget(self.stepper)

        body = QHBoxLayout()
        body.setSpacing(18)
        body.addWidget(self._build_left(), _PREVIEW_STRETCH)
        body.addWidget(self._build_form(), _FORM_STRETCH)
        root.addLayout(body, 1)
        root.addLayout(self._build_footer())

    def _build_left(self) -> QWidget:
        """Cột trái: trước khi chạy là phần xem trước, khi chạy là tiến trình."""
        card = Card(padding=tokens.SP_4)
        self._left_title = card.add_header("Video sẽ được lồng tiếng")

        # Phần xem trước: chỉ hiện trước khi bấm chạy.
        self.preview = EmptyState(
            "Chưa chọn video nào",
            "Chọn nguồn video ở bước 1. Khi bắt đầu chạy, chỗ này sẽ hiện "
            "tiến trình từng bước và nhật ký xử lý.",
            icon=icons.upload_cloud())
        card.body.addWidget(self.preview, 1)

        self.steps = StepTracker()
        self.steps.setVisible(False)
        card.body.addWidget(self.steps)
        self.log = LogPanel()
        self.log.setVisible(False)
        card.body.addWidget(self.log, 1)

        self.pending_banner = Banner("warning", "Đang chờ bản dịch tiếng Việt")
        btn_hint = GhostButton("Mở hướng dẫn dịch")
        btn_hint.clicked.connect(self._open_hint)
        btn_dir = GhostButton("Mở thư mục dự án")
        btn_dir.clicked.connect(self._open_result_folder)
        self.btn_resume_after = PrimaryButton("Đã dịch xong, tiếp tục")
        self.btn_resume_after.clicked.connect(self._resume_after_translation)
        for button in (btn_hint, btn_dir, self.btn_resume_after):
            self.pending_banner.add_button(button)
        card.body.addWidget(self.pending_banner)

        self.done_banner = Banner("success", "Đã lồng tiếng xong")
        btn_video = GhostButton("Mở video")
        btn_video.clicked.connect(self._open_result_video)
        btn_folder = GhostButton("Mở thư mục")
        btn_folder.clicked.connect(self._open_result_folder)
        btn_edit = PrimaryButton("Chỉnh sửa dự án")
        btn_edit.clicked.connect(self._open_editor)
        for button in (btn_video, btn_folder, btn_edit):
            self.done_banner.add_button(button)
        card.body.addWidget(self.done_banner)
        return card

    def _build_form(self) -> QWidget:
        holder = QWidget()
        clear_background(holder)
        holder.setMaximumWidth(_FORM_MAX_W)
        # Cột biểu mẫu không được co hẹp hơn bước rộng nhất — khi cửa sổ nhỏ
        # thì phần xem trước bên trái nhường chỗ, không phải cắt ô nhập.
        holder.setMinimumWidth(_FORM_MIN_W)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)

        card = Card(padding=tokens.SP_5)
        scroll = QScrollArea()
        clear_background(scroll)
        scroll.viewport().setStyleSheet("background: transparent;")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        self.pages = QStackedWidget()
        clear_background(self.pages)
        self.step_video = VideoStep()
        self.step_recognize = RecognizeStep()
        self.step_translate = TranslateStep()
        self.step_voice = VoiceStep()
        self.step_subtitle = SubtitleStep()
        self.step_export = ExportStep()
        self._steps = (self.step_video, self.step_recognize,
                       self.step_translate, self.step_voice,
                       self.step_subtitle, self.step_export)
        for step in self._steps:
            step.changed.connect(self._on_form_changed)
            self.pages.addWidget(step)
        self.step_voice.preview_requested.connect(self._preview_voice)
        self.step_subtitle.style_requested.connect(self._open_style_dialog)
        self._preview.status_changed.connect(self.step_voice.set_status)

        scroll.setWidget(self.pages)
        card.body.addWidget(scroll, 1)
        layout.addWidget(card)
        return holder

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(tokens.SP_2)
        self.btn_back = GhostButton("Quay lại")
        self.btn_back.clicked.connect(self._go_back)
        self.btn_clear_draft = GhostButton("Xóa nháp")
        self.btn_clear_draft.setToolTip(
            "Xóa những lựa chọn đã lưu tạm và quay về giá trị mặc định")
        self.btn_clear_draft.clicked.connect(self._clear_draft)
        self.btn_next = PrimaryButton("Tiếp tục")
        self.btn_next.clicked.connect(self._go_next)
        self.btn_stop = DangerButton("Dừng")
        self.btn_stop.clicked.connect(self._cancel)
        self.btn_stop.setVisible(False)
        row.addWidget(self.btn_back)
        row.addWidget(self.btn_clear_draft)
        row.addStretch()
        row.addWidget(self.btn_stop)
        row.addWidget(self.btn_next)
        return row

    # -- Điều hướng giữa các bước --------------------------------------
    def _go_to_step(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        self.stepper.set_current(index)
        self._refresh_footer()
        if index == 2:
            self.step_translate.set_source_language(self._source_lang_label())
        if index == 5:
            self.step_export.set_summary(self._summary_rows())

    def _go_next(self) -> None:
        index = self.stepper.current_step()
        ok, reason = self._steps[index].is_complete()
        if not ok:
            TOASTS.warn(reason)
            return
        if index >= len(self._steps) - 1:
            self._start()
            return
        self.stepper.set_max_reached(index)
        self._go_to_step(index + 1)

    def _go_back(self) -> None:
        index = self.stepper.current_step()
        if index == 0:
            self.home_requested.emit()
            return
        self._go_to_step(index - 1)

    def _refresh_footer(self) -> None:
        last = self.stepper.current_step() >= len(self._steps) - 1
        self.btn_next.setText("Bắt đầu lồng tiếng" if last else "Tiếp tục")

    def _on_form_changed(self) -> None:
        self._draft_timer.start()
        self._refresh_preview()
        if self.stepper.current_step() == 5:
            self.step_export.set_summary(self._summary_rows())

    def _refresh_preview(self) -> None:
        """Nhắc lại nguồn video đang chọn ở cột trái cho dễ đối chiếu."""
        if self.is_running():
            return
        data = self.values()
        source = {"url": data["url"],
                  "file": os.path.basename(data["file_path"]),
                  "resume": data["resume_dir"]}.get(data["source"], "")
        if source:
            self.preview.set_message(
                source, "Đi hết sáu bước rồi bấm Bắt đầu lồng tiếng. "
                        "Tiến trình từng bước sẽ hiện ngay tại đây.")
        else:
            self.preview.set_message(
                "Chưa chọn video nào",
                "Chọn nguồn video ở bước 1. Khi bắt đầu chạy, chỗ này sẽ hiện "
                "tiến trình từng bước và nhật ký xử lý.")

    # -- Gom dữ liệu ---------------------------------------------------
    def values(self) -> dict:
        data: dict = {}
        for step in self._steps:
            data.update(step.values())
        return data

    def _source_lang_label(self) -> str:
        if self.step_recognize.auto_detect.isChecked():
            return "Tự nhận ra từ video"
        code = self.step_recognize.language.current_key()
        return next((label for label, key in consts.SOURCE_LANGS
                     if key == code), code)

    def _summary_rows(self) -> list[tuple[str, str]]:
        """Bảng tóm tắt hiện ở bước cuối, viết bằng lời thường."""
        data = self.values()

        def label_of(options, key, default="—"):
            return next((text for text, value in options if value == key), default)

        source = {"url": data["url"], "file": os.path.basename(data["file_path"]),
                  "resume": data["resume_dir"]}.get(data["source"], "")
        return [
            ("Video", source or "chưa chọn"),
            ("Ngôn ngữ gốc", self._source_lang_label()),
            ("Độ chính xác khi nghe",
             label_of(consts.WHISPER_MODELS, data["whisper_model"])),
            ("Phong cách dịch",
             label_of([(a, b) for a, b, _c in consts.TRANSLATE_STYLES],
                      data["translate_style"])),
            ("Giọng đọc",
             f"{data['voice'] or 'theo cài đặt chung'} · "
             f"tốc độ {data['voice_speed']:.2f}x"),
            ("Phụ đề",
             f"{label_of(consts.SUBTITLE_MODES, data['subtitle_mode'])} · "
             f"kiểu {label_of(PRESET_CHOICES, data['subtitle_preset'])}"),
            ("Nhạc nền", label_of(consts.BG_MODES, data["bg_mode"])),
            ("Chỉ xuất âm thanh", "có" if data["skip_video"] else "không"),
        ]

    def preload_file(self, path: str) -> None:
        """Điền sẵn tệp video khi người dùng kéo thả ở Trang chủ."""
        self.step_video.set_file(path)
        self._go_to_step(0)

    # -- Bản nháp ------------------------------------------------------
    def _draft_path(self) -> str:
        return os.path.join(cache_dir(), DRAFT_FILE)

    def _save_draft(self) -> None:
        try:
            with open(self._draft_path(), "w", encoding="utf-8") as f:
                json.dump(self.values(), f, ensure_ascii=False)
        except OSError:
            pass      # không lưu được nháp thì cũng không cản trở việc chính

    def _load_draft(self) -> None:
        try:
            with open(self._draft_path(), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        if data:
            for step in self._steps:
                step.load(data)
        else:
            self._apply_defaults_from_settings()
        self._go_to_step(0)

    def _apply_defaults_from_settings(self) -> None:
        """Lần đầu mở thì lấy giá trị mặc định từ tệp cấu hình."""
        try:
            settings = self._settings_provider()
        except Exception:  # noqa: BLE001 — cấu hình hỏng thì dùng mặc định sẵn
            return
        self.step_recognize.engine.set_key(settings.asr_engine)
        self.step_recognize.model.set_key(settings.whisper_model)
        self.step_voice.picker.reload(settings)
        self.step_voice.picker.set_voice(settings.vieneu_voice)
        self.step_voice.speed.set_value(settings.voice_speed)
        self.step_subtitle.mode.set_key(settings.subtitle_mode)
        self.step_subtitle.preset.set_key(settings.subtitle_preset)

    def _clear_draft(self) -> None:
        confirmed, _ = ConfirmDialog.ask(
            self, "Xóa bản nháp",
            "Những lựa chọn đang lưu tạm sẽ bị xóa và các bước quay về giá trị "
            "mặc định. Bạn có chắc không?",
            kind="warning", confirm_label="Xóa nháp")
        if not confirmed:
            return
        try:
            os.remove(self._draft_path())
        except OSError:
            pass
        for step in self._steps:
            step.load({})
        self._apply_defaults_from_settings()
        self._go_to_step(0)
        TOASTS.info("Đã xóa bản nháp.")

    # -- Nghe thử và kiểu phụ đề ---------------------------------------
    def _preview_voice(self, voice: str) -> None:
        try:
            settings = self._settings_provider()
        except Exception as e:  # noqa: BLE001 — báo lên giao diện
            self.step_voice.set_status(f"Không đọc được cấu hình: {e}")
            return
        self._preview.play(settings, voice)

    def _open_style_dialog(self) -> None:
        from autodub.media.subtitle import preset_style
        from autodub_gui.style_dialog import StyleDialog

        video = self._current_video_path()
        # Chưa tự chỉnh gì thì mở ra với đúng bộ kiểu đang chọn ở bước này,
        # để cửa sổ xem trước khớp với thứ người dùng vừa chọn.
        style = self._subtitle_style or preset_style(
            self.step_subtitle.preset.current_key())
        try:
            dialog = StyleDialog(video, style, self._blur_regions, self)
        except Exception as e:  # noqa: BLE001 — thường do thiếu ffmpeg
            ConfirmDialog.show_error(
                self, "Không mở được khung xem trước",
                "Ứng dụng cần lấy một khung hình từ video để bạn canh chữ, "
                "nhưng lần này không lấy được. Hãy kiểm tra video còn trên máy "
                "và máy đã cài FFmpeg chưa.", detail=str(e))
            return
        if not dialog.exec():
            return
        self._subtitle_style = dialog.style()
        if video:
            self._blur_regions = dialog.regions()
        self._update_style_summary()

    def _update_style_summary(self) -> None:
        parts: list[str] = []
        style = self._subtitle_style
        if style:
            position = {"bottom": "dưới", "middle": "giữa",
                        "top": "trên"}.get(style.get("position", "bottom"), "dưới")
            parts.append(f"cỡ chữ {style.get('font_size', 22)}, đặt ở {position}")
            if style.get("display") == "karaoke":
                parts.append(f"hiện theo cụm {style.get('words_per_cue', 3)} chữ")
        else:
            parts.append("kiểu mặc định")
        count = len(self._blur_regions)
        parts.append(f"che {count} vùng" if count else "chưa che vùng nào")
        self.step_subtitle.set_summary(", ".join(parts).capitalize())
        if style and self.step_subtitle.mode.current_key() != "burn":
            self.step_subtitle.mode.set_key("burn")
            TOASTS.info("Kiểu chữ tự chỉnh cần ghi thẳng vào hình, nên phụ đề "
                        "đã chuyển sang Ghi thẳng vào hình.")

    def _current_video_path(self) -> str:
        data = self.values()
        path = data["file_path"]
        if path and os.path.isfile(path):
            return path
        work_dir = data["resume_dir"] or (
            self._result.work_dir if self._result else "")
        if work_dir and os.path.isdir(work_dir):
            from autodub.pipeline import source_video_path
            return source_video_path(work_dir) or ""
        return ""

    # -- Chạy ----------------------------------------------------------
    def _build_request(self) -> DubRequest | None:
        ok, reason = self.step_video.is_complete()
        if not ok:
            TOASTS.warn(reason)
            self._go_to_step(0)
            return None
        from autodub.media.subtitle import preset_style

        data = self.values()
        source = data["source"]
        return DubRequest(
            url=data["url"] if source == "url" else None,
            file_path=data["file_path"] if source in ("file", "resume") else None,
            source_lang=("" if data["auto_detect"] else data["source_lang"]),
            voice=data["voice"] or None,   # rỗng = dùng giọng mặc định trong Cài đặt
            bg_mode=data["bg_mode"],
            bg_duck_db=data["bg_duck_db"],
            skip_video=data["skip_video"],
            resume_dir=data["resume_dir"] if source == "resume" else None,
            subtitle_mode=data["subtitle_mode"],
            blur_regions=list(self._blur_regions),
            subtitle_style=(self._subtitle_style
                            or preset_style(data["subtitle_preset"])),
            translate_engine=data["translate_engine"] or None,
        )

    def _run_settings(self):
        """Cấu hình cho lần chạy này, đã ghép phong cách dịch và tốc độ đọc."""
        data = self.values()
        settings = self._settings_provider()
        note = consts.style_note(data["translate_style"])
        extra = data["translate_note"].strip()
        merged = "\n".join(
            part for part in (settings.translate_style_notes, note, extra)
            if part).strip()
        changes = {"voice_speed": data["voice_speed"]}
        if merged != settings.translate_style_notes:
            changes["translate_style_notes"] = merged
        if data["asr_engine"]:
            changes["asr_engine"] = data["asr_engine"]
        if data["whisper_model"]:
            changes["whisper_model"] = data["whisper_model"]
        if data["voice"]:
            changes["vieneu_voice"] = data["voice"]
        return replace(settings, **changes)

    def _start(self) -> None:
        request = self._build_request()
        if request is not None:
            self._launch(request)

    def _resume_after_translation(self) -> None:
        if self._result is None:
            return
        request = self._build_request()
        if request is None:
            return
        request.resume_dir = self._result.work_dir
        request.url = None
        self._launch(request)

    def _launch(self, request: DubRequest) -> None:
        if self._worker is not None and self._worker.isRunning():
            TOASTS.warn("Đang có một video chạy dở. Hãy đợi xong hoặc bấm Dừng.")
            return
        if REGISTRY.is_busy():
            job = REGISTRY.current()
            TOASTS.warn(f"Đang chạy «{job.title}» ở trang khác. "
                        "Hãy đợi xong hoặc dừng việc đó trước.")
            return
        self.pending_banner.setVisible(False)
        self.done_banner.setVisible(False)
        self.steps.reset()
        self.log.clear()
        self._set_running(True)

        worker = DubWorker(self._run_settings(), request)
        worker.progress.connect(self.steps.apply_event)
        worker.progress.connect(REGISTRY.update_job)
        worker.log.connect(self.log.append_log)
        worker.finished_ok.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        worker.finished.connect(lambda: self._set_running(False))
        self._worker = worker
        REGISTRY.start_job(
            ActiveJob(kind="dub", title=self._job_title(request)),
            on_cancel=self._cancel)
        worker.start()

    def _job_title(self, request: DubRequest) -> str:
        if request.file_path:
            return os.path.basename(request.file_path)
        if request.resume_dir:
            return os.path.basename(request.resume_dir.rstrip("\\/"))
        return request.url or "Video mới"

    def _cancel(self) -> None:
        if self._worker is None or not self._worker.isRunning():
            return
        self._worker.cancel()
        self.btn_stop.setEnabled(False)
        self.btn_stop.setText("Đang dừng…")

    def _set_running(self, running: bool) -> None:
        self.preview.setVisible(not running)
        self.steps.setVisible(running)
        self.log.setVisible(running)
        self._left_title.itemAt(0).widget().setText(
            "Tiến trình xử lý" if running else "Video sẽ được lồng tiếng")
        for step in self._steps:
            step.setEnabled(not running)
        self.btn_next.setVisible(not running)
        self.btn_back.setVisible(not running)
        self.btn_clear_draft.setVisible(not running)
        self.btn_stop.setVisible(running)
        self.btn_stop.setEnabled(True)
        self.btn_stop.setText("Dừng")
        self.stepper.set_live_mode(running)
        if not running:
            self.stepper.set_current(self.pages.currentIndex())
            self._refresh_footer()

    def _sync_live_stepper(self) -> None:
        """Đưa tiến độ thật lên thanh sáu bước khi đang chạy."""
        job = REGISTRY.current()
        if job is None or self._worker is None or not self._worker.isRunning():
            return
        index = _STEP_TO_INDEX.get(job.step)
        if index is not None:
            self.stepper.set_live_progress(index)

    # -- Kết quả -------------------------------------------------------
    def _on_finished(self, result: DubResult) -> None:
        self._result = result
        if result.status == "translate_pending":
            self._show_pending(result)
            REGISTRY.finish_job(False, "đang chờ bản dịch tiếng Việt")
            return
        self.stepper.mark_all_done()
        report = result.report or {}
        files = report.get("files") or {}
        self.done_banner.set_text(
            f"Video kết quả: {files.get('dubbed_video') or 'chỉ có âm thanh'}\n"
            f"Số câu thoại: {report.get('total_segments', '—')}\n"
            f"Thư mục dự án: {result.work_dir}")
        self.done_banner.setVisible(True)
        REGISTRY.finish_job(True)
        TOASTS.success("Đã lồng tiếng xong.", action_label="Mở video",
                       on_action=self._open_result_video)

    def _show_pending(self, result: DubResult) -> None:
        self.pending_banner.set_text(
            "Video đang chờ bản dịch tiếng Việt. Bấm Mở hướng dẫn dịch rồi làm "
            "theo ba bước, mất khoảng hai đến ba phút. Lưu bản dịch xong thì "
            "quay lại đây bấm Đã dịch xong, tiếp tục.\n\n"
            "Mẹo: điền API Key Gemini miễn phí trong Cài đặt để ứng dụng tự "
            "dịch, khỏi làm tay.\n\n"
            f"Thư mục dự án: {result.work_dir}")
        self.pending_banner.setVisible(True)

    def _on_failed(self, message: str) -> None:
        self.log.append_log("Lỗi: " + message, 40)
        REGISTRY.finish_job(False, message[:120])
        if "Missing required setting" in message:
            ConfirmDialog.show_error(
                self, "Thiếu cấu hình",
                "Còn vài mục bắt buộc chưa điền nên chưa chạy được. Hãy mở "
                "trang Cài đặt và điền nốt, rồi bấm chạy lại.", detail=message)
            self.settings_needed.emit(message)
            return
        friendly = consts.friendly_error(message)
        if friendly is not None:
            title, advice = friendly
            ConfirmDialog.show_error(self, title, advice, detail=message)
            return
        ConfirmDialog.show_error(
            self, "Quá trình xử lý dừng giữa chừng",
            "Có lỗi ngoài dự tính nên ứng dụng phải dừng lại. Tiến độ đã lưu "
            "trên đĩa vẫn còn: bạn có thể chọn Tiếp tục dang dở ở bước 1 để "
            "chạy tiếp từ chỗ dừng.", detail=message)

    def _on_cancelled(self) -> None:
        self.log.append_log("Đã dừng theo yêu cầu của bạn.", 30)
        REGISTRY.add_activity(LEVEL_INFO, "Đã dừng việc lồng tiếng theo yêu cầu")
        REGISTRY.finish_job(False, "bạn đã bấm dừng")

    # -- Mở kết quả ----------------------------------------------------
    def _open_hint(self) -> None:
        if self._result is None:
            return
        path = os.path.join(self._result.work_dir, "TRANSLATE_PENDING.txt")
        ok, message = open_file(path)
        if not ok:
            TOASTS.warn(message)

    def _open_result_folder(self) -> None:
        if self._result is None:
            return
        ok, message = open_folder(self._result.work_dir)
        if not ok:
            TOASTS.warn(message)

    def _open_result_video(self) -> None:
        if self._result is None:
            return
        files = (self._result.report or {}).get("files") or {}
        ok, message = open_file(files.get("dubbed_video", ""))
        if not ok:
            self._open_result_folder()

    def _open_editor(self) -> None:
        if self._result is not None:
            self.edit_requested.emit(self._result.work_dir)

    # -- Vòng đời ------------------------------------------------------
    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def shutdown(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(5000)

    def cleanup(self) -> None:
        self._save_draft()
        self._preview.cleanup()
