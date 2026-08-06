"""Background workers: run the pipeline / batch / downloads off the UI thread.

Each worker is a QThread emitting Qt signals; ProgressEvent objects from the
core pipeline are forwarded as-is (they are plain dataclasses, safe across
threads via queued connections). A logging.Handler subclass forwards core log
records into the GUI log panel.
"""
from __future__ import annotations

import logging
import re
import threading

from PySide6.QtCore import QObject, QRunnable, QThread, Signal

from autodub.config import Settings
from autodub.pipeline import DubPipeline, DubRequest, DubResult
from autodub.progress import PipelineCancelled


# --- Lọc log cho người dùng --------------------------------------------------
# Nhật ký GUI chỉ hiện những dòng người dùng HIỂU và CẦN. Log kỹ thuật
# (worker, VRAM, DLL, per-segment...) vẫn ra console/file cho dev — chỉ bị
# ẩn khỏi panel. Warning/Error luôn được giữ: một cảnh báo bị ẩn tệ hơn một
# dòng khó hiểu.

# Dòng INFO chứa các từ này là log kỹ thuật — ẩn dù viết tiếng Việt.
_DEV_TERMS = re.compile(
    r"VRAM|worker|atempo|DLL|NVENC|ONNX|cuBLAS|cu\d{3}|loudnorm|ffmpeg"
    r"|filtergraph|KV cache|keep.?alive|fontsdir|force_style|PlayRes",
    re.IGNORECASE)

# Tiếng Việt có dấu — log hướng tới người dùng đều viết tiếng Việt, còn log
# tiếng Anh thuần (STEP N, Loading model, Transcript saved...) là log dev.
_VI_DIACRITICS = re.compile(
    "[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợ"
    "ùúủũụưừứửữựỳýỷỹỵđ]", re.IGNORECASE)


def user_facing(message: str, levelno: int) -> bool:
    """True nếu dòng log nên hiện trong Nhật ký của GUI."""
    if levelno >= logging.WARNING:
        return True
    text = message.strip()
    if not text or set(text) <= {"=", "-"}:      # dòng kẻ phân cách
        return False
    if _DEV_TERMS.search(text):
        return False
    # INFO không có chữ tiếng Việt nào = log kỹ thuật tiếng Anh.
    return bool(_VI_DIACRITICS.search(text))


class GuiLogHandler(logging.Handler):
    """Forward autodub.* log records to a Qt signal (thread-safe via emit)."""

    def __init__(self, signal):
        super().__init__()
        self._signal = signal
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if not user_facing(record.getMessage(), record.levelno):
                return
            self._signal.emit(self.format(record), record.levelno)
        except RuntimeError:
            pass  # window closed while a worker was still logging


def attach_gui_logging(signal) -> GuiLogHandler:
    """Attach a GUI handler to the shared 'autodub' logger namespace."""
    handler = GuiLogHandler(signal)
    root = logging.getLogger("autodub")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return handler


def detach_gui_logging(handler: GuiLogHandler) -> None:
    logging.getLogger("autodub").removeHandler(handler)


class DubWorker(QThread):
    """Run one DubPipeline.run() in the background."""

    progress = Signal(object)          # ProgressEvent
    log = Signal(str, int)             # message, levelno
    finished_ok = Signal(object)       # DubResult
    failed = Signal(str)               # error message
    cancelled = Signal()

    def __init__(self, settings: Settings, request: DubRequest, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._request = request
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        handler = attach_gui_logging(self.log)
        try:
            pipeline = DubPipeline(
                self._settings,
                progress=self.progress.emit,
                cancel_event=self._cancel_event,
            )
            result: DubResult = pipeline.run(self._request)
            self.finished_ok.emit(result)
        except PipelineCancelled:
            self.cancelled.emit()
        except Exception as e:  # noqa: BLE001 — surfaced to the user verbatim
            self.failed.emit(str(e))
        finally:
            detach_gui_logging(handler)


class SaveAllWorker(QThread):
    """Save every edited line, then re-run TTS for the ones that changed.

    One worker for the whole batch: the user edits freely, presses save once,
    and gets a single progress stream instead of per-row round trips.
    """

    log = Signal(str, int)
    seg_done = Signal(int, int, int)          # seg_id, index, total
    finished_ok = Signal(list)                # re-synthesized seg ids
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, settings: Settings, work_dir: str, edits: dict[int, str],
                 target_key: str, voice: str | None, parent=None,
                 force_all: bool = False):
        super().__init__(parent)
        self._settings = settings
        self._work_dir = work_dir
        self._edits = edits
        self._target_key = target_key
        self._voice = voice
        self._force_all = force_all
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        from autodub.editor import resynth_segments, save_segment_texts
        from autodub.progress import ProgressReporter

        handler = attach_gui_logging(self.log)
        reporter = ProgressReporter(lambda _e: None, self._cancel_event)
        try:
            changed = save_segment_texts(self._work_dir, self._edits, self._target_key)
            # Đổi giọng cho cả video: đọc lại mọi câu, kể cả câu không sửa chữ.
            if self._force_all:
                changed = sorted(self._edits.keys())
            if not changed:
                self.finished_ok.emit([])
                return
            resynth_segments(
                self._work_dir, changed, self._settings,
                self._target_key, self._voice, reporter,
                on_progress=lambda done, total, sid:
                    self.seg_done.emit(sid, done, total))
            self.finished_ok.emit(changed)
        except PipelineCancelled:
            self.cancelled.emit()
        except Exception as e:  # noqa: BLE001 — surfaced to the user verbatim
            self.failed.emit(str(e))
        finally:
            detach_gui_logging(handler)


class RebuildWorker(QThread):
    """Rebuild the final audio + video from edited segments off the UI thread."""

    progress = Signal(object)          # ProgressEvent
    log = Signal(str, int)
    finished_ok = Signal(str)          # dubbed video path
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, settings: Settings, work_dir: str, target_key: str,
                 voice: str | None, bg_mode: str, bg_duck_db: float,
                 subtitle_mode: str | None, blur_regions: list[dict] | None,
                 subtitle_style: dict | None = None, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._work_dir = work_dir
        self._target_key = target_key
        self._voice = voice
        self._bg_mode = bg_mode
        self._bg_duck_db = bg_duck_db
        self._subtitle_mode = subtitle_mode
        self._blur_regions = blur_regions
        self._subtitle_style = subtitle_style
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        from autodub.editor import rebuild_output
        from autodub.progress import ProgressReporter

        handler = attach_gui_logging(self.log)
        reporter = ProgressReporter(self.progress.emit, self._cancel_event)
        try:
            out = rebuild_output(
                self._work_dir, self._settings, self._target_key, self._voice,
                self._bg_mode, self._bg_duck_db,
                self._subtitle_mode, self._blur_regions,
                self._subtitle_style, reporter)
            self.finished_ok.emit(out)
        except PipelineCancelled:
            self.cancelled.emit()
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
        finally:
            detach_gui_logging(handler)


class SubtitleWorker(QThread):
    """Ghi lại phụ đề vào video mà không đụng tới giọng đọc.

    Đây là đường nhanh cho việc sửa chữ hoặc đổi kiểu chữ: chỉ vẽ lại chữ lên
    hình, dùng lại nguyên bản âm thanh của lần xuất trước.
    """

    progress = Signal(object)
    log = Signal(str, int)
    finished_ok = Signal(str)          # đường dẫn video kết quả
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, settings: Settings, work_dir: str, target_key: str,
                 subtitle_mode: str | None, blur_regions: list[dict] | None,
                 subtitle_style: dict | None = None, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._work_dir = work_dir
        self._target_key = target_key
        self._subtitle_mode = subtitle_mode
        self._blur_regions = blur_regions
        self._subtitle_style = subtitle_style
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        from autodub.editor import rebuild_subtitles
        from autodub.progress import ProgressReporter

        handler = attach_gui_logging(self.log)
        reporter = ProgressReporter(self.progress.emit, self._cancel_event)
        try:
            out = rebuild_subtitles(
                self._work_dir, self._settings, self._target_key,
                self._subtitle_mode, self._blur_regions,
                self._subtitle_style, reporter)
            self.finished_ok.emit(out)
        except PipelineCancelled:
            self.cancelled.emit()
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
        finally:
            detach_gui_logging(handler)


class BatchWorker(QThread):
    """Run a batch of pasted URLs (one per line) in the background."""

    progress = Signal(object)                    # ProgressEvent (current video)
    item_status = Signal(int, int, str, str, str)  # index, total, url, status, detail
    log = Signal(str, int)
    finished_ok = Signal(object)                 # BatchSummary
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, settings: Settings, req_template: DubRequest,
                 items: list, retry_done: bool = False, reuse_tts: bool = True,
                 parent=None):
        super().__init__(parent)
        self._settings = settings
        self._template = req_template
        self._items = items          # list[BatchItem] (or pasted text lines)
        self._retry_done = retry_done
        self._reuse_tts = reuse_tts
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        from autodub.batch import run_batch

        handler = attach_gui_logging(self.log)

        def observer(i, total, item, status, detail):
            self.item_status.emit(i, total, item.key, status, detail)

        synth_cache = None
        try:
            if self._reuse_tts:
                from autodub.speech.tts import SynthCache
                synth_cache = SynthCache()
            pipeline = DubPipeline(
                self._settings,
                progress=self.progress.emit,
                cancel_event=self._cancel_event,
                synth_cache=synth_cache,
            )
            summary = run_batch(self._items, self._settings, self._template,
                                pipeline=pipeline, observer=observer,
                                retry_done=self._retry_done)
            self.finished_ok.emit(summary)
        except PipelineCancelled:
            self.cancelled.emit()
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
        finally:
            if synth_cache is not None:
                synth_cache.close()
            detach_gui_logging(handler)


class ProjectScanWorker(QThread):
    """Quét thư mục kết quả ở luồng nền.

    Việc này đọc rất nhiều tệp nhỏ và tính dung lượng cả cây thư mục, nên
    chạy trên luồng giao diện sẽ làm cửa sổ đứng vài giây khi có nhiều dự án.
    """

    ready = Signal(list)          # list[Project]
    failed = Signal(str)

    def __init__(self, output_dir: str, running_dir: str = "", parent=None):
        super().__init__(parent)
        self._output_dir = output_dir
        self._running_dir = running_dir

    def run(self) -> None:
        from autodub_gui.projects import scan

        try:
            self.ready.emit(scan(self._output_dir, self._running_dir))
        except Exception as e:  # noqa: BLE001 — hiện thành màn hình lỗi
            self.failed.emit(str(e))


class ThumbnailWorker(QRunnable):
    """Tạo một ảnh đại diện bằng ffmpeg, chạy trong nhóm luồng dùng chung."""

    class Signals(QObject):
        ready = Signal(str, str)      # khóa dự án, đường dẫn ảnh

    def __init__(self, project):
        super().__init__()
        self.signals = self.Signals()
        self._project = project
        self.setAutoDelete(True)

    def run(self) -> None:
        from autodub_gui.projects import ensure_thumbnail

        try:
            path = ensure_thumbnail(self._project)
        except Exception:  # noqa: BLE001 — thiếu ảnh thì dùng ô giữ chỗ
            path = ""
        if path:
            self.signals.ready.emit(self._project.key, path)


class WaveformWorker(QThread):
    """Tính dạng sóng ở luồng nền.

    Việc này quét cả tệp âm thanh, với video dài có thể mất vài giây, nên
    không được làm trên luồng giao diện.
    """

    ready = Signal(list)      # danh sách biên độ từ 0 tới 1

    def __init__(self, wav_path: str, buckets: int = 0, parent=None):
        super().__init__(parent)
        self._path = wav_path
        self._buckets = buckets

    def run(self) -> None:
        from autodub_gui.waveform import DEFAULT_BUCKETS, peaks

        try:
            self.ready.emit(peaks(self._path, self._buckets or DEFAULT_BUCKETS))
        except Exception:  # noqa: BLE001 — không vẽ được thì hiện dải phẳng
            self.ready.emit([])


class PreflightWorker(QThread):
    """Chạy kiểm tra tiền chuyến bay (autodub.preflight) ở luồng nền.

    Kiểm tra chạm đĩa và gọi ffmpeg nên không được làm trên luồng giao diện.
    Kết quả là danh sách CheckResult (dataclass thuần, an toàn qua signal).
    """

    ready = Signal(list)      # list[autodub.preflight.CheckResult]

    def run(self) -> None:
        from autodub.preflight import run_preflight

        try:
            results = run_preflight(Settings.load(override=True))
        except Exception:  # noqa: BLE001 — không được làm sập giao diện
            results = []
        self.ready.emit(results)


class UpdateCheckWorker(QThread):
    """Hỏi GitHub xem có bản VoxDub mới không, chạy ở luồng nền.

    Gọi mạng nên không được chạy trên luồng giao diện. Không có mạng hay kho
    chưa có bản phát hành nào thì im lặng — kiểm tra nền không được làm phiền.
    """

    found = Signal(object)    # autodub.updates.UpdateInfo

    def __init__(self, repo: str, current_version: str, parent=None):
        super().__init__(parent)
        self._repo = repo
        self._current = current_version

    def run(self) -> None:
        from autodub.updates import check_for_update

        try:
            info = check_for_update(self._repo, self._current)
        except Exception:  # noqa: BLE001 — lỗi mạng thì coi như không có bản mới
            return
        if info is not None:
            self.found.emit(info)


class SystemStatusWorker(QThread):
    """Đọc lại tệp cấu hình và kiểm tra ba thứ thiết yếu, chạy ở luồng nền.

    Kiểm tra giọng đọc, dịch tự động và FFmpeg. Việc này chạm vào ổ đĩa nên
    tuyệt đối không được làm trên luồng giao diện.
    """

    ready = Signal(dict)      # {"voice": (chữ, ổn), "translate": ..., "ffmpeg": ...}

    def run(self) -> None:
        import shutil

        result: dict[str, tuple[str, bool | None]] = {}
        try:
            settings = Settings.load(override=True)
            result["voice"] = self._voice_status(settings)
            result["translate"] = self._translate_status(settings)
            ok = bool(shutil.which("ffmpeg"))
            result["ffmpeg"] = ("sẵn sàng" if ok else "chưa cài", ok)
        except Exception as e:  # noqa: BLE001 — không được làm sập giao diện
            result = {"voice": ("không đọc được", False),
                      "translate": ("không đọc được", False),
                      "ffmpeg": (str(e)[:40], False)}
        self.ready.emit(result)

    @staticmethod
    def _voice_status(settings: Settings) -> tuple[str, bool | None]:
        """Bộ giọng đã cài chưa, và đang có bao nhiêu giọng dùng được."""
        if not settings.vieneu_configured():
            return ("chưa cài", False)
        try:
            from autodub.speech.tts.voices import catalog
            return (f"{len(catalog(settings))} giọng", True)
        except Exception:  # noqa: BLE001 — không được làm sập giao diện
            return ("sẵn sàng", True)

    @staticmethod
    def _translate_status(settings: Settings) -> tuple[str, bool | None]:
        """Nơi dịch đang chọn đã đủ API Key và mô hình chưa."""
        if not settings.translate_enabled:
            return ("đang tắt", None)
        engine = settings.translate_engine
        if engine == "gemini":
            label = "Gemini"
        else:
            from autodub.text.translate_openai import label_of
            label = label_of(engine)
        if settings.translate_configured():
            return (label, True)
        return (f"{label} chưa đủ cấu hình", False)


class DownloadWorker(QThread):
    """Download a list of URLs (no dubbing)."""

    item_status = Signal(int, int, str, str, str)  # index, total, url, status, detail
    log = Signal(str, int)
    finished_ok = Signal(int, int)                 # success, failed
    failed = Signal(str)                           # whole-run error (e.g. bad output dir)
    cancelled = Signal()

    def __init__(self, urls: list[str], output_dir: str,
                 cookies_from_browser: str | None = None,
                 cookies_file: str | None = None, parent=None):
        super().__init__(parent)
        self._urls = urls
        self._output_dir = output_dir
        self._cookies_browser = cookies_from_browser or None
        self._cookies_file = cookies_file or None
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        from autodub.media.downloader import download_one
        from autodub.utils import ensure_dir

        handler = attach_gui_logging(self.log)
        success = failed = 0
        try:
            ensure_dir(self._output_dir)
            total = len(self._urls)
            for i, url in enumerate(self._urls):
                if self._cancel_event.is_set():
                    self.cancelled.emit()
                    return
                self.item_status.emit(i, total, url, "start", "")
                try:
                    entry = download_one(url, self._output_dir,
                                         self._cookies_browser, self._cookies_file)
                    success += 1
                    self.item_status.emit(i, total, url, "success", entry["filepath"])
                except Exception as e:  # noqa: BLE001 — per-item failure
                    failed += 1
                    self.item_status.emit(i, total, url, "failed", str(e)[:200])
            self.finished_ok.emit(success, failed)
        except Exception as e:  # noqa: BLE001 — e.g. thư mục lưu không tạo được
            self.failed.emit(str(e))
        finally:
            detach_gui_logging(handler)
