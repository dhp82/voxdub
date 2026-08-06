"""Batch dubbing: process a list of videos typed one per line, with crash-safe
status tracking.

The user pastes URLs — one per line — and the batch runner does the rest. An
optional voice name may follow the URL after ``|``, ``,`` or a tab::

    https://youtu.be/aaa
    https://youtu.be/bbb | Trúc Ly
    https://youtu.be/ccc | Phạm Tuyên

Progress is persisted to ``batch_state.json`` inside the output directory after
every video, so an interrupted batch can be resumed by pasting the same list
again: videos already marked ``success`` are skipped automatically.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Callable, Iterable

from autodub.config import Settings
from autodub.pipeline import DubPipeline, DubRequest
from autodub.progress import PipelineCancelled
from autodub.utils import save_json_atomic, setup_logging

logger = setup_logging("autodub.batch")

STATE_FILENAME = "batch_state.json"

# Tách một dòng thành liên kết + TÊN GIỌNG tùy chọn. Chỉ tách ở các dấu rõ
# ràng (| , ; tab, hoặc từ hai khoảng trắng trở lên) vì tên giọng tiếng Việt
# có khoảng trắng bên trong — tách ở một dấu cách sẽ cắt đôi «Trúc Ly».
_SPLIT_RE = re.compile(r"[|,;\t]|\s{2,}")


@dataclass
class BatchItem:
    """One video in a batch: a URL or a local file, plus per-video options."""
    url: str | None = None
    file_path: str | None = None
    voice: str | None = None
    blur_regions: list = None          # per-video blur rectangles (or None)
    subtitle_mode: str | None = None   # per-video override (or None = template)
    subtitle_style: dict | None = None  # per-video style (or None = template)
    ref: object = None  # backend-specific handle (state dict entry)

    @property
    def key(self) -> str:
        """Stable identity for state tracking (URL or absolute file path)."""
        return self.url or os.path.abspath(self.file_path or "")

    @property
    def label(self) -> str:
        """Short display name for tables/logs."""
        if self.url:
            return self.url
        return os.path.basename(self.file_path or "")


@dataclass
class BatchSummary:
    total: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0


# Observer signature: (index, total, item, status, detail)
# status: "start" | "success" | "failed"
BatchObserver = Callable[[int, int, BatchItem, str, str], None]


def parse_lines(text: str | Iterable[str]) -> list[BatchItem]:
    """Turn pasted text (or a list of lines) into batch items.

    Dòng trống và dòng bắt đầu bằng ``#`` bị bỏ qua, liên kết trùng chỉ lấy
    lần đầu. Tên giọng được giữ nguyên như người dùng gõ; giọng không có
    trong danh mục sẽ tự rơi về giọng mặc định lúc chạy chứ không làm hỏng
    cả danh sách."""
    lines = text.splitlines() if isinstance(text, str) else list(text)
    items: list[BatchItem] = []
    seen: set[str] = set()

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        parts = [p.strip() for p in _SPLIT_RE.split(line, maxsplit=1) if p and p.strip()]
        if not parts:
            continue  # dòng chỉ có ký tự phân tách ("|", ",") — bỏ qua
        url = parts[0]
        voice = parts[1] if len(parts) > 1 else None

        if url in seen:
            logger.info(f"Skipping duplicate URL: {url}")
            continue
        seen.add(url)
        items.append(BatchItem(url=url, voice=voice))

    return items


def _run_items(
    items: list[BatchItem],
    pipeline: DubPipeline,
    req_template: DubRequest,
    on_result: Callable[[BatchItem, dict | None, str | None], None],
    on_start: Callable[[BatchItem], None] | None = None,
    observer: BatchObserver | None = None,
) -> BatchSummary:
    """Process items sequentially; call ``on_result(item, report, error)`` after
    each one (report on success, error message on failure) so the caller can
    persist status crash-safely. ``observer`` (if given) receives display-only
    per-item events — used by the GUI. A :class:`PipelineCancelled` from the
    pipeline aborts the whole batch (it is not recorded as a failure)."""
    summary = BatchSummary(total=len(items))

    for i, item in enumerate(items):
        logger.info(f"[{i + 1}/{len(items)}] Processing: {item.label}")
        if on_start:
            on_start(item)
        if observer:
            observer(i, len(items), item, "start", "")
        try:
            result = pipeline.run(DubRequest(
                url=item.url,
                file_path=item.file_path,
                source_lang=req_template.source_lang,
                voice=item.voice or req_template.voice,
                bg_mode=req_template.bg_mode,
                bg_duck_db=req_template.bg_duck_db,
                skip_video=req_template.skip_video,
                subtitle_mode=item.subtitle_mode or req_template.subtitle_mode,
                subtitle_style=(item.subtitle_style
                                if item.subtitle_style is not None
                                else req_template.subtitle_style),
                blur_regions=(item.blur_regions
                              if item.blur_regions is not None
                              else req_template.blur_regions),
                output_dir=req_template.output_dir,
                translate_engine=req_template.translate_engine,
            ))
            if result.status != "completed":
                raise RuntimeError(
                    f"Pipeline stopped: {result.status} (work_dir={result.work_dir}). "
                    "Complete the translation and resume this video individually."
                )
            summary.success += 1
            logger.info(f"[{i + 1}/{len(items)}] SUCCESS → {result.report['session_id']}")
            on_result(item, result.report, None)
            if observer:
                observer(i, len(items), item, "success", result.report["session_id"])
        except PipelineCancelled:
            logger.info("Batch cancelled by user")
            raise
        except Exception as e:
            summary.failed += 1
            error_msg = str(e)[:200]
            logger.error(f"[{i + 1}/{len(items)}] FAILED: {error_msg}")
            on_result(item, None, error_msg)
            if observer:
                observer(i, len(items), item, "failed", error_msg)

    logger.info("=" * 60)
    logger.info("BATCH COMPLETE")
    logger.info(f"  Total:   {summary.total}")
    logger.info(f"  Success: {summary.success}")
    logger.info(f"  Failed:  {summary.failed}")
    if summary.skipped:
        logger.info(f"  Skipped: {summary.skipped} (already done)")
    logger.info("=" * 60)
    return summary


def _save_json_atomic(data: object, path: str) -> None:
    """Crash-safe save: write to temp file then replace."""
    save_json_atomic(data, path)


def _load_state(state_path: str) -> dict[str, dict]:
    """Read the per-URL status map from a previous run (empty if none)."""
    if not os.path.exists(state_path):
        return {}
    try:
        with open(state_path, encoding="utf-8") as f:
            data = json.load(f)
        return {v["video_url"]: v for v in data.get("videos", []) if v.get("video_url")}
    except Exception as e:  # noqa: BLE001 — a corrupt state file must not block a run
        logger.warning(f"Ignoring unreadable {STATE_FILENAME}: {e}")
        return {}


def run_batch(
    lines: str | Iterable[str] | list[BatchItem],
    settings: Settings,
    req_template: DubRequest,
    pipeline: DubPipeline | None = None,
    observer: BatchObserver | None = None,
    state_path: str | None = None,
    retry_done: bool = False,
    reuse_tts: bool = True,
) -> BatchSummary:
    """Dub every video in the batch.

    ``lines`` is either pasted text/lines of URLs (one per line, optional
    ``| voice`` suffix) or a ready list of :class:`BatchItem` — the GUI's
    upload table passes items directly, with per-video blur regions and
    subtitle modes.

    Videos recorded as ``success`` in ``batch_state.json`` are skipped unless
    ``retry_done`` is set. The state file is rewritten after every video, so a
    crashed or cancelled batch resumes cleanly from the same list.

    ``reuse_tts`` keeps one warmed TTS model alive across all videos instead
    of reloading it per video (10-60 s each) — only applies when no custom
    ``pipeline`` is injected.

    ``pipeline`` lets a frontend inject a DubPipeline wired with its own
    progress callback / cancel event; defaults to a plain one."""
    if (isinstance(lines, list) and lines
            and all(isinstance(x, BatchItem) for x in lines)):
        items = lines
    else:
        items = parse_lines(lines)
    if not items:
        logger.info("No videos in the batch list.")
        return BatchSummary()

    state_path = state_path or os.path.join(
        req_template.output_dir or settings.output_dir, STATE_FILENAME)
    previous = _load_state(state_path)

    pending: list[BatchItem] = []
    skipped = 0
    for item in items:
        done = previous.get(item.key, {}).get("status") == "success"
        if done and not retry_done:
            logger.info(f"Already done, skipping: {item.label}")
            skipped += 1
            continue
        pending.append(item)

    # Rebuild the state file around this run's list so the on-disk order matches
    # what the user provided, keeping results for videos they kept in the list.
    pending_keys = {item.key for item in pending}
    videos: list[dict] = []
    for item in items:
        entry = dict(previous.get(item.key, {}))
        entry["video_url"] = item.key
        entry["voice"] = item.voice or req_template.voice
        if item.key in pending_keys or not entry.get("status"):
            entry["status"] = "waiting"
        videos.append(entry)
    by_key = {v["video_url"]: v for v in videos}
    for item in pending:
        item.ref = by_key[item.key]

    state = {"output_dir": os.path.dirname(os.path.abspath(state_path)), "videos": videos}

    def flush() -> None:
        _save_json_atomic(state, state_path)

    if not pending:
        logger.info(f"Nothing to do: all {len(items)} video(s) already completed.")
        flush()
        return BatchSummary(total=len(items), skipped=skipped)

    logger.info(f"{len(pending)} video(s) to process, {skipped} already done")
    logger.info("=" * 60)
    flush()

    def on_start(item: BatchItem) -> None:
        item.ref["status"] = "processing"
        item.ref.pop("error", None)
        flush()

    def on_result(item: BatchItem, report: dict | None, error: str | None) -> None:
        entry = item.ref
        if report:
            entry["status"] = "success"
            entry["output_folder"] = report["session_id"]
            entry["segments"] = report["total_segments"]
            entry["duration_original"] = report["total_original_duration"]
            entry["duration_dub"] = report["total_tts_duration"]
            entry["processing_time"] = report["processing_time_seconds"]
            entry.pop("error", None)
        else:
            entry["status"] = "failed"
            entry["error"] = error
        flush()

    synth_cache = None
    demucs_cache = None
    if pipeline is None:
        if reuse_tts and len(pending) > 1:
            from autodub.speech.tts import SynthCache
            synth_cache = SynthCache()
        if len(pending) > 1:
            # Worker chỉ thực sự khởi động ở video đầu tiên cần Demucs —
            # tạo object ở đây là miễn phí, gating (venv GPU, RAM) nằm trong
            # DemucsCache._ensure().
            from autodub.media.vocal_separator import DemucsCache
            demucs_cache = DemucsCache()
        pipeline = DubPipeline(settings, synth_cache=synth_cache,
                               demucs_cache=demucs_cache)
    try:
        summary = _run_items(pending, pipeline, req_template, on_result,
                             on_start=on_start, observer=observer)
    finally:
        if synth_cache is not None:
            synth_cache.close()
        if demucs_cache is not None:
            demucs_cache.close()
    summary.skipped = skipped
    return summary

