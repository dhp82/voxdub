"""Video dubbing pipeline (Vietnamese).

GUI-ready core:
- no ``input()`` calls — voice must be resolved by the caller
- no ``sys.exit`` — errors raise, missing config raises :class:`ConfigError`
- progress observable via a callback (:class:`autodub.progress.ProgressEvent`)
- cancellable via ``threading.Event``
- the manual-translation stop is returned as data (``status="translate_pending"``),
  not an exception

Typical use::

    from autodub import DubRequest, DubPipeline, Settings

    settings = Settings.load()
    result = DubPipeline(settings).run(DubRequest(url="https://...", voice="male"))
    if result.status == "translate_pending":
        ...  # show TRANSLATE_PENDING.txt instructions, later re-run with resume_dir
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

from autodub.config import Settings
from autodub.languages import TargetLang, get_target, resolve_source_lang
from autodub.progress import PipelineCancelled, ProgressFn, ProgressReporter
from autodub.utils import setup_logging, ensure_dir, seg_wav_path
from autodub.workdir import data_dir, data_path, youtube_dir

logger = setup_logging("autodub.pipeline")


def source_video_path(work_dir: str) -> str | None:
    """External source video remembered for this work_dir, if still valid.

    Runs started from a local file outside work_dir record it in
    ``source_video.json`` so resumes (CLI and GUI) find the source without
    the user re-picking it.
    """
    marker = data_path(work_dir, "source_video.json")
    if not os.path.exists(marker):
        return None
    try:
        with open(marker, encoding="utf-8") as f:
            path = json.load(f).get("file_path")
    except (json.JSONDecodeError, OSError):
        return None
    if path and os.path.exists(path):
        return path
    return None


@dataclass
class DubRequest:
    """Everything needed for one dubbing run (Vietnamese)."""
    url: str | None = None
    file_path: str | None = None
    source_lang: str = "zh-CN"
    #: TÊN giọng đọc (xem autodub.speech.tts.voices). None → giọng mặc định
    #: trong cấu hình.
    voice: str | None = None
    bg_mode: str = "demucs"            # "demucs" | "duck" | "none"
    bg_duck_db: float = -12.0
    skip_video: bool = False
    output_dir: str | None = None      # default resolved from Settings
    resume_dir: str | None = None
    subtitle_mode: str = "none"        # "none" | "soft" | "burn"
    # Rectangles (normalized 0..1: x/y/w/h, optional t_start/t_end) blurred to
    # cover hardcoded source captions. Any region forces a video re-encode.
    blur_regions: list[dict] = field(default_factory=list)
    subtitle_style: dict | None = None  # libass styling; None → Settings default
    #: Nơi dịch riêng cho lần chạy này (xem autodub.config.TRANSLATE_ENGINES).
    #: None → Settings.translate_engine.
    translate_engine: str | None = None

    # The dub target is always Vietnamese now.
    target: str = "vi"


@dataclass
class DubResult:
    status: str                        # "completed" | "translate_pending"
    work_dir: str
    report: dict = field(default_factory=dict)


class DubPipeline:
    """Runs the full dub pipeline for a :class:`DubRequest`."""

    def __init__(
        self,
        settings: Settings,
        progress: ProgressFn | None = None,
        cancel_event: threading.Event | None = None,
        synth_cache=None,
        demucs_cache=None,
    ):
        self.settings = settings
        self._reporter = ProgressReporter(progress, cancel_event)
        # Optional autodub.speech.tts.SynthCache — lets a batch run reuse one
        # warmed TTS model across videos. When set, the cache owns the
        # synthesizer lifecycle (this pipeline never closes it).
        self._synth_cache = synth_cache
        # Optional autodub.media.vocal_separator.DemucsCache — batch runs
        # reuse one loaded Demucs model across videos (caller owns lifecycle).
        self._demucs_cache = demucs_cache

    def _get_synth(self, target, voice):
        from autodub.speech.tts import get_synthesizer
        if self._synth_cache is not None:
            return self._synth_cache.get(target, self.settings, voice)
        return get_synthesizer(target, self.settings, voice)

    # ------------------------------------------------------------------ #

    def run(self, req: DubRequest) -> DubResult:
        """Run the pipeline; on error/cancel, release TTS workers + the
        background-separation future so a long-lived GUI process doesn't
        strand multi-GB worker subprocesses (VRAM) between runs."""
        self._active_synth = None
        self._active_bg_future = None
        try:
            return self._run_impl(req)
        except BaseException:
            fut = self._active_bg_future
            if fut is not None:
                fut.cancel()
                # Observe the eventual result so a failure in the background
                # thread isn't reported as "exception was never retrieved".
                fut.add_done_callback(
                    lambda f: f.cancelled() or f.exception())
            synth = self._active_synth
            if (self._synth_cache is None and synth is not None
                    and hasattr(synth, "close")):
                try:
                    synth.close()
                except Exception as e:
                    logger.warning(f"Không đóng được TTS synth khi dọn dẹp: {e}")
            raise
        finally:
            self._active_synth = None
            self._active_bg_future = None

    @staticmethod
    def _log_machine_info(settings: Settings) -> None:
        """Một dòng cấu hình máy đầu mỗi lượt chạy — để đọc log là biết ngay
        video chậm vì máy yếu hay vì lỗi, không phải hỏi lại người dùng."""
        from autodub.sysinfo import available_ram_gb, total_ram_gb
        from autodub.media.vocal_separator import gpu_venv_python

        total = total_ram_gb()
        avail = available_ram_gb()
        ram_txt = (f"RAM {avail:.1f}/{total:.1f} GB trống"
                   if total is not None and avail is not None else "RAM ?")
        gpu_txt = "có" if gpu_venv_python() else "không"
        logger.info(
            f"Máy: {os.cpu_count() or '?'} nhân, {ram_txt}, GPU (venv) {gpu_txt} — "
            f"TTS {settings.vieneu_max_workers} luồng, "
            f"parallel {settings.parallel_workers}")

    def _run_impl(self, req: DubRequest) -> DubResult:
        start_time = time.time()
        settings = self.settings
        rep = self._reporter
        # Ghi nhận thời gian từng bước để dễ phát hiện điểm nghẽn hiệu năng.
        stage_times: dict[str, float] = {}
        _stage_start: list[float] = [start_time]

        def _tick(name: str) -> None:
            now = time.time()
            stage_times[name] = round(now - _stage_start[0], 2)
            _stage_start[0] = now
            logger.info(f"  ⏱  {name}: {stage_times[name]:.1f}s")

        target = get_target(req.target)
        lang_code = resolve_source_lang(req.source_lang)
        logger.info(f"Source language: {lang_code} → {target.name}")
        self._log_machine_info(settings)

        # Resume an existing work_dir or create a new timestamped one
        if req.resume_dir:
            if not os.path.isdir(req.resume_dir):
                raise FileNotFoundError(f"Resume directory not found: {req.resume_dir}")
            work_dir = req.resume_dir
            folder_name = os.path.basename(os.path.normpath(work_dir))
            logger.info(f"Resuming work directory: {work_dir}")
        else:
            output_dir = req.output_dir or self.default_output_dir(target)
            folder_name = datetime.now().strftime("%Y%m%d%H%M%S") + target.folder_suffix
            work_dir = ensure_dir(os.path.join(output_dir, folder_name))
            logger.info(f"Output folder: {work_dir}")

        # Bố cục thư mục: file kỹ thuật vào data/, kết quả nằm ở gốc.
        # Thư mục cũ (mọi thứ phẳng ở gốc) được data_path tự nhận và giữ nguyên.
        transcript_orig_path = data_path(work_dir, "transcript_original.json",
                                         create_dir=True)
        transcript_dub_path = data_path(work_dir, target.transcript_name)
        audio_path = data_path(work_dir, "original_audio.wav")

        # --- Step 1: Download or use local file ---
        rep.check_cancelled()
        rep.emit("acquire", "start")
        logger.info("=" * 60)
        logger.info("STEP 1: Acquiring video")
        if req.url:
            logger.info("Đang tải video về máy...")
        video_path = self._resolve_video(work_dir, req.url, req.file_path)
        logger.info(f"Video: {video_path}")
        logger.info(f"Đã có video: {os.path.basename(video_path)}")
        rep.emit("acquire", "done", detail=video_path)
        _tick("acquire")

        # --- Step 2: Extract audio ---
        rep.check_cancelled()
        logger.info("=" * 60)
        if os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
            logger.info(f"STEP 2: Reusing existing extracted audio: {audio_path}")
            rep.emit("extract", "skip", detail=audio_path)
        else:
            logger.info("STEP 2: Extracting audio")
            rep.emit("extract", "start")
            from autodub.media.audio import extract_audio
            extract_audio(video_path, audio_path, sample_rate=settings.audio_sample_rate)
            rep.emit("extract", "done", detail=audio_path)

        # HQ extract for the background/mix path: 44.1 kHz stereo. The 16 kHz
        # mono file above is what ASR wants, but running Demucs on it crushes
        # the soundtrack to phone-call bandwidth — the single biggest audio
        # quality loss of the old pipeline. Failure falls back to the ASR wav.
        hq_audio_path = data_path(work_dir, "original_audio_hq.wav")
        if settings.hq_background:
            if not (os.path.exists(hq_audio_path)
                    and os.path.getsize(hq_audio_path) > 0):
                try:
                    from autodub.media.audio import extract_audio
                    extract_audio(video_path, hq_audio_path,
                                  sample_rate=44100, channels=2)
                except Exception as e:
                    logger.warning(f"Không rút được audio HQ ({e}) — "
                                   "nhạc nền dùng bản 16 kHz như cũ")
                    hq_audio_path = audio_path
        else:
            hq_audio_path = audio_path

        # --- Step 2.5: Background track — starts right after the audio
        # extract. The pipeline waits for it BEFORE ASR: Demucs and Whisper
        # sharing a 6 GB GPU slow each other down more than running back to
        # back, and each step alone peaks lower on RAM/VRAM.
        rep.check_cancelled()
        from concurrent.futures import ThreadPoolExecutor
        bg_executor = ThreadPoolExecutor(max_workers=1)
        bg_future = bg_executor.submit(
            self._resolve_background, req.bg_mode, req.bg_duck_db,
            hq_audio_path, work_dir,
        )
        bg_executor.shutdown(wait=False)
        self._active_bg_future = bg_future

        engine = req.translate_engine or settings.translate_engine
        tts_synth = None
        # Kiểu phụ đề chốt MỘT lần ở đây rồi dùng lại cho mọi bước sinh phụ
        # đề bên dưới — chữ trong tệp .srt và chữ ghi vào hình luôn khớp.
        from autodub.media.subtitle import normalize_style
        from autodub.text.subtitles import refresh_subtitles
        subtitle_style = normalize_style(req.subtitle_style
                                         or settings.subtitle_style())

        # --- Step 3: Speech-to-Text (ASR) — GPU-exclusive ---
        rep.check_cancelled()
        logger.info("=" * 60)
        if os.path.exists(transcript_orig_path):
            logger.info(f"STEP 3: Reusing existing transcript: {transcript_orig_path}")
            with open(transcript_orig_path, encoding="utf-8") as f:
                segments = json.load(f)
            logger.info(f"Dùng lại lời thoại đã nghe từ lần chạy trước "
                        f"({len(segments)} câu) — đỡ chờ")
            rep.emit("asr", "skip", detail=f"{len(segments)} segments (cached)")
        else:
            # Demucs và ASR chỉ được chạy song song khi KHÔNG giành nhau
            # tài nguyên: Demucs trong tiến trình con GPU còn ASR chắc chắn
            # chạy CPU (Paraformer, hoặc Whisper không nạp được CUDA).
            # Còn lại giữ rào chắn cũ — hai việc nặng chen nhau trên cùng
            # GPU (hoặc cùng 4 nhân CPU) chậm hơn chạy lần lượt.
            from autodub.media.vocal_separator import gpu_venv_python
            from autodub.speech.transcriber import asr_will_use_gpu
            overlap_ok = (req.bg_mode == "demucs"
                          and bool(gpu_venv_python())
                          and not asr_will_use_gpu(settings, lang_code))
            if overlap_ok:
                logger.info("Demucs (GPU) và ASR (CPU) chạy song song — "
                            "tiết kiệm thời gian chờ")
            else:
                # Let the separation finish first so Whisper gets the GPU
                # alone (duck/none resolve instantly; a cached Demucs too).
                bg_future.result()
            logger.info("STEP 3: Transcribing audio (ASR)")
            logger.info("Đang nghe và ghi lại lời thoại trong video — "
                        "video dài thì bước này hơi lâu...")
            rep.emit("asr", "start")
            from autodub.speech.transcriber import transcribe, save_transcript
            from autodub.text.srt import generate_srt
            segments = transcribe(audio_path, lang_code, settings)
            save_transcript(segments, transcript_orig_path)
            generate_srt(segments, data_path(work_dir, "transcript_original.srt"),
                         text_field="text")
            logger.info(f"Nghe xong: video có {len(segments)} câu thoại")
            rep.emit("asr", "done", detail=f"{len(segments)} segments")
        logger.info(f"Transcribed {len(segments)} segments")
        if not segments:
            # Không có lời nói → dịch/TTS đều vô nghĩa; báo đúng nguyên nhân
            # thay vì để bước dịch fail với thông điệp gây hiểu nhầm.
            raise RuntimeError(
                "Không nhận dạng được lời nói nào trong video (video chỉ có "
                "nhạc, hoặc chọn sai ngôn ngữ gốc). Kiểm tra lại ngôn ngữ "
                "nguồn trong tab Lồng tiếng.")

        # Real per-clip time window (until the next line starts) — drives the
        # translation character budget and the TTS target duration.
        from autodub.text.translate_hint import annotate_slots
        annotate_slots(segments)

        # Khởi động sớm bộ giọng: việc nạp model (vài giây trên CPU) nấp sau
        # bước dịch. VieNeu chạy CPU nên không tranh card đồ họa với bất cứ
        # thứ gì — lúc nào cũng khởi động sớm được. Ở đây chỉ NẠP model, còn
        # việc tạo giọng vẫn nằm nguyên trong Bước 5.
        try:
            tts_synth = self._get_synth(target, req.voice)
            self._active_synth = tts_synth
            warm = getattr(tts_synth, "warm_up_async", None)
            if warm is not None:
                bg_future.add_done_callback(lambda _f: warm())
        except Exception as e:
            logger.warning(f"Bỏ qua khởi động sớm bộ giọng ({e})")
            tts_synth = None
            self._active_synth = None

        # --- Step 4: Load translation (manual via skill or web AI) ---
        rep.check_cancelled()
        logger.info("=" * 60)
        logger.info(f"STEP 4: Loading {target.name} translation")
        if os.path.exists(transcript_dub_path):
            logger.info(f"Reusing existing translation: {transcript_dub_path}")
            logger.info("Dùng lại bản dịch đã có — đỡ chờ")
            segments = self._load_translation(transcript_dub_path, segments, target)
            refresh_subtitles(segments, work_dir, target, subtitle_style)
            # The hint file is no longer needed once the translation exists
            hint_leftover = os.path.join(work_dir, "TRANSLATE_PENDING.txt")
            if os.path.exists(hint_leftover):
                os.remove(hint_leftover)
            rep.emit("translate", "done", detail=transcript_dub_path)
        else:
            translated = self._auto_translate(segments, target,
                                              req.source_lang, engine,
                                              work_dir=work_dir)
            if translated is None:
                from autodub.text.translate_hint import write_hint
                hint_path = write_hint(work_dir, target, req.source_lang,
                                       settings=self.settings)
                # Dòng info tiếng Anh cho console/dev; warning tiếng Việt là
                # dòng người dùng thấy trong Nhật ký.
                logger.info("Translation pending — see TRANSLATE_PENDING.txt in work dir")
                logger.warning("Video đang chờ bản dịch — xem hướng dẫn "
                               "3 bước hiện trên màn hình")
                rep.emit("translate", "start", detail=hint_path)
                # Let the background separation finish before stopping: the
                # result is cached on disk, so the resume run reuses it.
                bg_future.result()
                # Don't hold TTS resources while waiting for a manual
                # translation (cache-owned synths stay alive — the batch
                # owner closes them).
                if (self._synth_cache is None and tts_synth is not None
                        and hasattr(tts_synth, "close")):
                    tts_synth.close()
                return DubResult(status="translate_pending", work_dir=work_dir)

            # Persist before validating so the file is editable and the next
            # run hits the cached branch above (resume-safe, same as manual).
            from autodub.speech.transcriber import save_transcript
            save_transcript(translated, transcript_dub_path)
            segments = self._load_translation(transcript_dub_path, segments, target)
            refresh_subtitles(segments, work_dir, target, subtitle_style)
            rep.emit("translate", "done", detail=transcript_dub_path)

        # --- Step 5: TTS ---
        # Strict 1:1 rendering: every translated segment becomes exactly one
        # spoken clip, placed at its original start time. Voice, subtitles,
        # translation and editor all share Whisper's per-fragment timeline —
        # nothing is grouped, so clips can never pile onto each other.
        rep.check_cancelled()
        logger.info("=" * 60)
        logger.info(f"STEP 5: Synthesizing {target.name} audio (TTS)")
        logger.info(f"Bắt đầu tạo giọng đọc cho {len(segments)} câu — "
                    "bước lâu nhất, tiến độ hiện ở khung bên trái...")
        seg_dir = ensure_dir(data_path(work_dir, "segments", create_dir=True))
        self._ensure_render_mode(work_dir, seg_dir)
        tts_results = self._synthesize_segments(target, req.voice, segments,
                                                seg_dir, synth=tts_synth)
        # Free the TTS workers' VRAM before the NVENC video encode — unless a
        # batch cache owns them (the next video reuses the warm pool).
        if (self._synth_cache is None and tts_synth is not None
                and hasattr(tts_synth, "close")):
            tts_synth.close()

        # --- Step 5.5: Video speed (optional) — slow the WHOLE video by
        # VIDEO_SPEED (e.g. 0.82) so the naturally-longer dub simply fits.
        # One uniform factor: no per-segment fitting, no trimming ever.
        # Mutates segments onto the slowed timeline; on failure returns None
        # and the run continues on the original video.
        background_path, background_gain_db = bg_future.result()
        deferred_speed: tuple[float, str] | None = None
        if settings.video_speed < 0.999 and not req.skip_video:
            rep.check_cancelled()
            logger.info("=" * 60)
            logger.info(f"STEP 5.5: Slowing video ({settings.video_speed}x)")
            from autodub.media.retime import (apply_video_speed,
                                              defer_video_speed,
                                              rescale_blur_regions)
            # Video đằng nào cũng mã hóa lại ở bước ghép (phụ đề ghi vào
            # hình / che chữ) → gộp setpts vào lượt đó, đỡ nguyên một lần
            # encode toàn bộ video. Không mã hóa lại thì đi đường rời như cũ.
            deferred = None
            if req.subtitle_mode == "burn" or req.blur_regions:
                deferred = defer_video_speed(video_path, background_path,
                                             segments, work_dir, settings)
            if deferred is not None:
                if deferred[0] is not None:
                    background_path = deferred[0]
                deferred_speed = (float(settings.video_speed), deferred[2])
                if req.blur_regions:
                    req.blur_regions = rescale_blur_regions(
                        req.blur_regions, deferred[1])
                refresh_subtitles(segments, work_dir, target, subtitle_style)
            else:
                slowed = apply_video_speed(video_path, background_path,
                                           segments, work_dir, settings)
                if slowed is not None:
                    video_path = slowed[0]
                    if slowed[1] is not None:
                        background_path = slowed[1]
                    # Blur windows + SRT follow the slowed timeline; the dub
                    # transcript keeps ORIGINAL timestamps on disk so a resume
                    # rescales from the same base (and reuses the cached encode).
                    if req.blur_regions:
                        req.blur_regions = rescale_blur_regions(
                            req.blur_regions, slowed[2])
                    refresh_subtitles(segments, work_dir, target, subtitle_style)

        # --- Step 6: voice speed + merge audio ---
        rep.check_cancelled()
        logger.info("=" * 60)
        rep.emit("merge_audio", "start")
        total_duration = max(seg["end"] for seg in segments) + 1.0 if segments else 0

        # Hậu kỳ giọng: loudnorm + highpass + fade cho TỪNG clip — mọi giọng
        # ra cùng một mức âm lượng cảm nhận, hết click đầu
        # câu. Chạy trước voice_speed để atempo nhận clip đã sạch.
        merge_src = seg_dir
        if settings.voice_postprocess:
            logger.info("STEP 6a: Voice postprocess (loudnorm, fade, highpass)")
            logger.info("Đang cân chỉnh âm lượng các câu cho đều nhau...")
            from autodub.media.audio import postprocess_voice_clips
            merge_src = postprocess_voice_clips(
                segments, seg_dir, data_path(work_dir, "segments_post"),
                target_lufs=settings.voice_target_lufs,
                max_workers=min(8, settings.parallel_workers))

        merge_dir = self._apply_voice_speed(segments, merge_src, work_dir)

        # Chống chồng tiếng mềm: đặt lại vị trí clip (ưu tiên DỒN TRỄ vào
        # khoảng lặng — tốc độ đọc mọi câu giữ nguyên; nén nhẹ chỉ khi bất
        # khả kháng, trần thấp). Chạy trên clip ĐÃ hậu kỳ + voice_speed để
        # số đo thời lượng là thật. Mutates segments → SRT làm lại bên dưới.
        timing_report = None
        if settings.soft_timing_fit:
            from autodub.media.timing import apply_soft_timing
            merge_dir, timing_report = apply_soft_timing(
                segments, merge_dir, data_path(work_dir, "segments_timed"),
                settings, max_workers=min(8, settings.parallel_workers))
            refresh_subtitles(segments, work_dir, target, subtitle_style)

        # A long clip may run past the last segment's end — extend the mix
        # so the merge never cuts a clip at the timeline boundary.
        from autodub.media.audio import wav_duration_s
        for seg in segments:
            dur = wav_duration_s(seg_wav_path(merge_dir, seg["id"]))
            if dur:
                total_duration = max(total_duration, seg["start"] + dur + 0.5)

        logger.info("STEP 6: Merging audio segments")
        logger.info("Đang ghép giọng đọc với nhạc nền...")
        merged_audio_path = data_path(work_dir, target.audio_name)
        from autodub.media.audio import merge_segments
        merge_segments(
            segments, merge_dir, merged_audio_path, total_duration,
            background_path=background_path,
            background_gain_db=background_gain_db,
            duck_voice_db=settings.bg_duck_voice_db,
        )
        rep.emit("merge_audio", "done", detail=merged_audio_path)

        # --- Step 7: Merge video (optional) ---
        # Ghim tên giọng THẬT đã dùng (kể cả khi người dùng để mặc định),
        # để Trình chỉnh sửa hiện đúng giọng của video này thay vì đoán.
        # Ghim CẢ KHI bỏ qua bước ghép video, và GỘP vào render_opts thay vì
        # ghi đè — không xóa tùy chọn Trình chỉnh sửa đã lưu trước đó.
        from autodub.editor import load_render_opts, save_render_opts
        from autodub.speech.tts import voices as voice_catalog
        render_opts = load_render_opts(work_dir)
        render_opts["voice"] = voice_catalog.resolve(self.settings, req.voice)
        dubbed_video_path = None
        if not req.skip_video:
            rep.check_cancelled()
            logger.info("=" * 60)
            logger.info("STEP 7: Creating dubbed video")
            logger.info("Đang xuất video hoàn chỉnh"
                        + (" (có ghi phụ đề/che chữ nên lâu hơn chút)"
                           if req.subtitle_mode == "burn" or req.blur_regions
                           else "") + "...")
            rep.emit("merge_video", "start")
            dubbed_video_path = os.path.join(work_dir, "dubbed_video.mp4")
            render_opts.update({
                "subtitle_mode": req.subtitle_mode,
                "blur_regions": req.blur_regions,
                "subtitle_style": subtitle_style,
            })
        save_render_opts(work_dir, render_opts)
        if not req.skip_video:
            # Phụ đề ghi vào hình: cả câu (.srt) hay cụm chữ theo giọng đọc
            # (.ass) đều do refresh_subtitles quyết, dùng đúng bộ clip cuối
            # cùng nên chữ nhảy khớp giọng.
            _srt_path, burn_path = refresh_subtitles(
                segments, work_dir, target, subtitle_style,
                merge_dir=merge_dir, settings=settings,
                for_burn=req.subtitle_mode == "burn")
            from autodub.media.video import merge_video
            merge_video(
                video_path, merged_audio_path, dubbed_video_path,
                srt_path=burn_path,
                subtitle_mode=req.subtitle_mode,
                blur_regions=req.blur_regions,
                subtitle_style=subtitle_style,
                subtitle_lang=target.iso639_2,
                speed=deferred_speed[0] if deferred_speed else None,
                fps=deferred_speed[1] if deferred_speed else None,
            )
            rep.emit("merge_video", "done", detail=dubbed_video_path)

        # --- Step 8: Generate thumbnails + YouTube metadata ---
        content_result = self._generate_content(target, segments, req.url,
                                                work_dir, video_path)

        # --- Report + timing guide ---
        elapsed = time.time() - start_time
        report = self._build_report(
            target, folder_name, req, lang_code, segments, tts_results,
            work_dir, audio_path, merged_audio_path, dubbed_video_path,
            content_result, elapsed,
        )

        report_path = data_path(work_dir, "report.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        # Quality report — người dùng thấy NGAY video này còn vấn đề gì và ở
        # câu nào (chồng tiếng còn lại, câu bị dồn trễ/nén, câu tràn budget)
        # trước khi quyết định đăng.
        quality = self._build_quality_report(target, segments, timing_report,
                                             settings)
        quality_path = data_path(work_dir, "quality_report.json")
        with open(quality_path, "w", encoding="utf-8") as f:
            json.dump(quality, f, ensure_ascii=False, indent=2)
        # GUI shows this summary in the done-banner.
        report["quality"] = quality["summary"]
        s = quality["summary"]
        if s["segments_overlapped"]:
            logger.info(
                f"Kiểm tra chất lượng: {s['segments_ok']}/"
                f"{s['segments_total']} câu ổn — còn "
                f"{s['segments_overlapped']} câu chồng tiếng nhẹ. Nghe thử "
                "video; nếu khó chịu, giảm Tốc độ video trong Cài đặt rồi "
                "chạy lại (rất nhanh vì giọng đọc đã có sẵn).")
        elif len(quality["per_segment"]):
            logger.info(
                f"Kiểm tra chất lượng: {s['segments_ok']}/"
                f"{s['segments_total']} câu chuẩn, số còn lại chỉ lệch nhẹ "
                "— nghe thử để yên tâm.")
        else:
            logger.info("Kiểm tra chất lượng: tất cả các câu đều khớp đẹp")

        timing_guide = self._build_timing_guide(target, report, segments,
                                                tts_results)
        timing_path = data_path(work_dir, "timing_guide.json")
        with open(timing_path, "w", encoding="utf-8") as f:
            json.dump(timing_guide, f, ensure_ascii=False, indent=2)
        logger.info(f"Timing guide: {timing_path}")

        logger.info("=" * 60)
        logger.info(f"PIPELINE COMPLETE ({target.name})")
        logger.info(f"  Output:    {work_dir}")
        logger.info(f"  Segments:  {report['total_segments']}")
        logger.info(f"  Duration:  {report['total_original_duration']:.1f}s original, "
                    f"{report['total_tts_duration']:.1f}s dub audio")
        logger.info(f"  Adjusted:  {report['segments_speed_adjusted']} segments speed-adjusted")
        logger.info(f"  Time:      {elapsed:.1f}s")
        logger.info("=" * 60)
        mins, secs = divmod(int(elapsed), 60)
        logger.info(f"Hoàn tất! Lồng tiếng xong {report['total_segments']} "
                    f"câu trong {f'{mins} phút {secs} giây' if mins else f'{secs} giây'} "
                    "— video nằm trong thư mục kết quả.")

        # Tự dọn tệp trung gian nếu người dùng đã bật trong Cài đặt. Làm sau
        # cùng, khi mọi báo cáo đã ghi xong — video kết quả và phụ đề được
        # module diskspace giữ nguyên.
        if settings.auto_clean_intermediates:
            from autodub.diskspace import clean_project

            freed = clean_project(work_dir)
            if freed:
                logger.info(f"Đã tự dọn tệp trung gian, giải phóng "
                            f"{freed / (1024 ** 2):.0f} MB.")

        rep.emit("done", "done", detail=work_dir)
        return DubResult(status="completed", work_dir=work_dir, report=report)

    # ------------------------------------------------------------------ #

    def default_output_dir(self, target: TargetLang) -> str:
        return self.settings.vi_output_dir()

    def _auto_translate(
        self, segments: list[dict], target: TargetLang,
        source_lang: str, engine: str,
        work_dir: str | None = None,
    ) -> list[dict] | None:
        """Dịch bằng nơi đã chọn, hoặc trả về None để chuyển sang dịch tay.

        Trả về None khi tắt dịch tự động hoặc nơi dịch chưa đủ cấu hình —
        lớp gọi khi đó ghi TRANSLATE_PENDING.txt và dừng lại. Hết hạn mức hay
        API Key hỏng cũng chỉ dừng ở đây, không bao giờ vứt đi cả một lượt
        nghe-chép dài. Lệnh hủy vẫn được truyền lên trên.

        Dịch hai lượt: lượt 0 phân tích lời thoại rồi bơm ngữ cảnh (tóm tắt,
        xưng hô, thuật ngữ) vào lời nhắc của MỌI lô; dịch xong chạy lượt rà
        soát các câu nghi vấn. Cả hai đều có bộ nhớ đệm và tắt được riêng, và
        hỏng thì cũng không làm hỏng bản dịch.
        """
        settings, rep = self.settings, self._reporter
        if not settings.translate_enabled:
            return None

        from autodub.text.translate_openai import label_of
        name = label_of(engine) if engine != "gemini" else "Gemini"
        if not settings.translate_configured(engine):
            logger.warning(
                f"Đã chọn dịch bằng {name} nhưng chưa đủ API Key hoặc mô "
                "hình — chuyển sang dịch tay. Điền lại trong trang Cài đặt, "
                "phần Kết nối.")
            return None

        # Lượt 0 — kết quả lưu lại trong thư mục dự án nên chạy tiếp không
        # tốn thêm lượt gọi. Bản cấu hình hiệu dụng chỉ sống trong lượt dịch
        # này (không ghi ra .env).
        from autodub.text.translate_analysis import (analyze_transcript,
                                                     apply_analysis)
        cache = data_path(work_dir, "video_context.json") if work_dir else None
        analysis = analyze_transcript(segments, source_lang, engine, settings,
                                      cache_path=cache)
        effective = apply_analysis(settings, analysis)

        _key, _url, model = effective.translate_credentials(engine)
        rep.emit("translate", "start", detail=f"{name}: {model}")
        logger.info(f"Đang dịch {len(segments)} câu sang tiếng Việt...")
        try:
            if engine == "gemini":
                from autodub.text.translate_gemini import translate_segments
                result = translate_segments(segments, target, source_lang,
                                            effective, rep)
            else:
                from autodub.text.translate_openai import translate_segments
                result = translate_segments(segments, target, source_lang,
                                            effective, rep, engine=engine)
        except PipelineCancelled:
            raise
        except Exception as e:
            logger.warning(f"{name} dịch lỗi ({e}) — chuyển sang dịch tay")
            rep.emit("translate", "error", detail=str(e))
            return None

        # Lượt rà soát — soát câu tràn khung / lẫn chữ Hán / sót ý rồi dịch
        # lại đúng các câu đó.
        try:
            from autodub.text.translate_review import review_translations
            result = review_translations(result, target, source_lang, engine,
                                         effective)
        except PipelineCancelled:
            raise
        except Exception as e:
            logger.warning(f"Rà soát bản dịch lỗi ({e}) — dùng bản lượt đầu")
        return result

    def _load_translation(
        self, path: str, original_segments: list[dict], target: TargetLang
    ) -> list[dict]:
        """Load and validate the manually-produced translated transcript.

        Guards against the most common hand-made mistakes (wrong root type,
        missing translated field, segment count mismatch) with actionable
        errors instead of a crash deep inside TTS.
        """
        try:
            with open(path, encoding="utf-8") as f:
                segments = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Invalid JSON in {path}: {e}. "
                "If the AI wrapped the output in ```json fences, remove them and re-save."
            ) from e

        if not isinstance(segments, list) or not segments:
            raise ValueError(f"{path} must be a non-empty JSON array of segments")

        # Timing fields are as essential as the text: a hand-made file
        # missing start/end/duration would otherwise crash with a bare
        # KeyError — possibly AFTER an expensive TTS pass already ran.
        bad_timing = [
            s.get("id", i + 1) for i, s in enumerate(segments)
            if not all(isinstance(s.get(k), (int, float))
                       for k in ("start", "end", "duration"))
        ]
        if bad_timing:
            raise ValueError(
                f"{path}: {len(bad_timing)} segment(s) missing numeric "
                f"start/end/duration (ids: {bad_timing[:10]}"
                f"{'...' if len(bad_timing) > 10 else ''}). "
                "Keep every field from transcript_original.json — only ADD "
                f"the '{target.text_field}' field."
            )

        missing = [s.get("id", i + 1) for i, s in enumerate(segments)
                   if not str(s.get(target.text_field, "")).strip()]
        if missing:
            raise ValueError(
                f"{path}: {len(missing)} segment(s) missing the '{target.text_field}' "
                f"field (ids: {missing[:10]}{'...' if len(missing) > 10 else ''})"
            )

        if len(segments) != len(original_segments):
            logger.warning(
                f"Translated transcript has {len(segments)} segments but the "
                f"original has {len(original_segments)} — using the translated file."
            )

        # Normalise for TTS (terminal punctuation, single spaces) — manual
        # translations and old transcripts bypass merge_translations, so the
        # guarantee is re-applied here for every path into the pipeline.
        # Slots too: hand-made files (and pre-slot work dirs) lack them.
        from autodub.text.translate_hint import annotate_slots, ensure_terminal_punct
        annotate_slots(segments)
        for seg in segments:
            seg[target.text_field] = ensure_terminal_punct(
                str(seg[target.text_field]))
        return segments

    def _resolve_video(self, work_dir: str, url: str | None, file_path: str | None) -> str:
        """Locate the source video for this work_dir.

        Resume-friendly: reuse a previously downloaded/copied video in work_dir
        instead of re-downloading. Files matching ``dubbed_video*.mp4`` are
        treated as pipeline output, not source. If ``file_path`` is passed it
        takes precedence — useful when the user keeps the source outside
        work_dir. External sources are remembered in ``source_video.json`` so
        a resume finds them without ``--file``.
        """
        marker = data_path(work_dir, "source_video.json", create_dir=True)

        def _remember(path: str) -> str:
            try:
                with open(marker, "w", encoding="utf-8") as f:
                    json.dump({"file_path": os.path.abspath(path)}, f,
                              ensure_ascii=False, indent=2)
            except OSError:
                pass  # marker is a convenience, never fail the run over it
            return path

        if file_path:
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"Video file not found: {file_path}")
            return _remember(file_path)

        video_exts = (".mp4", ".mkv", ".webm", ".mov", ".avi")
        output_prefixes = ("dubbed_video",)
        if os.path.isdir(work_dir):
            for f in sorted(os.listdir(work_dir)):
                lower = f.lower()
                if not lower.endswith(video_exts):
                    continue
                if any(lower.startswith(prefix) for prefix in output_prefixes):
                    continue
                cached = os.path.join(work_dir, f)
                logger.info(f"Reusing existing video: {cached}")
                return cached

        # Resume of a run whose source was an external local file.
        remembered = source_video_path(work_dir)
        if remembered:
            logger.info(f"Reusing remembered source video: {remembered}")
            return remembered

        if url:
            from autodub.media.downloader import download_video
            return download_video(url, work_dir)

        raise RuntimeError(
            f"No source video found in {work_dir} and no --url/--file given. "
            "Pass --file <path> on resume if the original is outside work_dir."
        )

    def _resolve_background(
        self, bg_mode: str, bg_duck_db: float, audio_path: str, work_dir: str
    ) -> tuple[str | None, float]:
        """Resolve the background track for the dub merge (Step 2.5).

        ``audio_path`` is the HQ extract (44.1 kHz stereo) when
        ``hq_background`` is on — the stems keep that layout so the final mix
        retains the soundtrack's full bandwidth.
        """
        rep = self._reporter
        if bg_mode == "demucs":
            logger.info("=" * 60)
            logger.info("STEP 2.5: Separating vocals from original audio (Demucs)")
            logger.info("Đang tách giọng nói gốc ra khỏi nhạc nền — "
                        "mất vài phút với video dài...")
            rep.emit("separate", "start")
            # Keep the input's own layout: HQ path stays 44.1k stereo,
            # legacy path stays at the ASR rate (mono).
            import wave as _wave
            rate, ch = self.settings.audio_sample_rate, 1
            try:
                with _wave.open(audio_path, "rb") as w:
                    rate, ch = w.getframerate(), w.getnchannels()
            except (OSError, EOFError, _wave.Error):
                pass
            from autodub.media.vocal_separator import separate_vocals
            sep = separate_vocals(audio_path, data_dir(work_dir, create=True),
                                  sample_rate=rate, channels=ch,
                                  demucs_cache=self._demucs_cache)
            background_path = sep.get("no_vocals")
            if background_path is None:
                logger.warning(
                    "Vocal separation unavailable — dubbed audio will use a silent base"
                )
                rep.emit("separate", "error", detail="separation failed, silent base")
            else:
                rep.emit("separate", "done", detail=background_path)
            return background_path, 0.0

        if bg_mode == "duck":
            logger.info("=" * 60)
            logger.info(
                f"STEP 2.5: Ducking original audio by {bg_duck_db:+.1f} dB "
                "(no vocal separation)"
            )
            rep.emit("separate", "done", detail=f"duck {bg_duck_db:+.1f} dB")
            return audio_path, bg_duck_db

        logger.info("STEP 2.5 skipped: --bg-mode=none, dubbed audio uses silent base")
        rep.emit("separate", "skip")
        return None, 0.0

    # Cached segment wavs are only reusable if they were rendered under the
    # same grouping scheme. Bump when the text-to-wav mapping changes.
    RENDER_MODE = "per_fragment_v1"

    def _ensure_render_mode(self, work_dir: str, seg_dir: str) -> None:
        """Invalidate segment wavs cached under an older render scheme.

        Older runs grouped adjacent fragments into one wav keyed by the
        group's FIRST fragment id — reusing those files under the 1:1 scheme
        would play multi-line audio on a single line's slot (echoes and
        overlaps). A marker file records the scheme; on mismatch every
        derived wav is wiped so the run re-renders cleanly.
        """
        import shutil

        marker = os.path.join(seg_dir, ".render_mode")
        current = None
        if os.path.exists(marker):
            try:
                with open(marker, encoding="utf-8") as f:
                    current = f.read().strip()
            except OSError:
                current = None

        has_wavs = any(f.endswith(".wav") for f in os.listdir(seg_dir))
        if has_wavs and current != self.RENDER_MODE:
            logger.warning(
                "Cache giọng đọc cũ được tạo theo cơ chế gộp câu — xóa để "
                "tạo lại từng câu 1:1 (tránh tiếng lặp/chồng lớp)."
            )
            for f in os.listdir(seg_dir):
                if f.endswith(".wav"):
                    os.remove(os.path.join(seg_dir, f))
            for d in os.listdir(data_dir(work_dir)):
                if d.startswith(("segments_fit", "segments_slow",
                                 "segments_speed", "segments_post",
                                 "segments_timed")):
                    shutil.rmtree(data_path(work_dir, d), ignore_errors=True)

        if current != self.RENDER_MODE:
            with open(marker, "w", encoding="utf-8") as f:
                f.write(self.RENDER_MODE)

    def _synthesize_segments(
        self, target: TargetLang, voice: str | None,
        segments: list[dict], seg_dir: str, synth=None,
    ) -> list[dict]:
        """Step 5: per-segment TTS with caching (resume-safe), fanned out over
        one dispatch thread per live TTS worker (``recommended_threads``).

        ``synth`` reuses a pre-warmed synthesizer instance — creating a second
        one would load a second 5+ GB model and OOM a 6 GB card. Results keep
        segment order; cancellation propagates from any task.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from pydub import AudioSegment

        rep = self._reporter
        created_here = synth is None
        if synth is None:
            synth = self._get_synth(target, voice)
            # run()'s error path closes this if we die before the finally
            # below (e.g. an exception between here and the pool block).
            self._active_synth = synth
        text_field = target.text_field

        total = len(segments)
        # Mỗi câu một dòng nhật ký sẽ ngập giao diện khi video dài — lấy mẫu
        # để video 10 nghìn câu chỉ sinh khoảng 100 dòng tiến độ.
        log_every = 1 if total <= 60 else max(10, total // 100)
        # Số luồng gửi việc bám theo số tiến trình con đang sống thật (một
        # tiến trình chết giữa chừng sẽ bị loại khỏi nhóm).
        n_threads = max(1, getattr(
            synth, "recommended_threads",
            min(self.settings.parallel_workers,
                self.settings.vieneu_max_workers)))

        results: list[dict | None] = [None] * total
        count_lock = threading.Lock()
        done_count = 0

        def _one(seg: dict) -> dict:
            rep.check_cancelled()
            seg_path = seg_wav_path(seg_dir, seg["id"])
            if os.path.exists(seg_path) and os.path.getsize(seg_path) > 0:
                cached = AudioSegment.from_wav(seg_path)
                return {
                    "path": seg_path,
                    "actual_duration": round(len(cached) / 1000.0, 3),
                    "speed_adjusted": False,
                    "rate_applied": "cached",
                }
            return synth.synthesize(
                text=seg[text_field],
                output_path=seg_path,
                # Engines always render at natural pace now — timing is
                # handled globally by VIDEO_SPEED/VOICE_SPEED, never per clip.
                target_duration=None,
            ).to_dict()

        try:
            with ThreadPoolExecutor(max_workers=n_threads) as pool:
                # Longest texts first: a 15 s segment picked up last would
                # leave the other workers idle at the tail of the run. Cached
                # segments cost nothing, so ordering by text length is fine.
                order = sorted(range(total),
                               key=lambda i: len(str(segments[i].get(text_field, ""))),
                               reverse=True)
                futures = {pool.submit(_one, segments[i]): i for i in order}
                try:
                    for fut in as_completed(futures):
                        i = futures[fut]
                        result = fut.result()  # re-raises errors / cancellation
                        results[i] = result
                        with count_lock:
                            done_count += 1
                            n = done_count
                        rep.emit("tts", "progress", current=n, total=total)
                        if (n % log_every == 0 or n == total
                                or result["speed_adjusted"]):
                            logger.info(
                                f"  Segment {segments[i]['id']}: "
                                f"{result['actual_duration']:.1f}s "
                                f"(target: {segments[i]['duration']:.1f}s, "
                                f"rate: {result['rate_applied']}) [{n}/{total}]"
                            )
                except BaseException:
                    # Unstarted tasks never run; in-flight renders (a few
                    # seconds each) finish as the pool context exits, then we
                    # re-raise.
                    for f in futures:
                        f.cancel()
                    raise
        finally:
            # Release worker subprocesses even on error/cancel — a leaked
            # pool pins GBs of VRAM in a long-lived GUI process.
            if (created_here and self._synth_cache is None
                    and hasattr(synth, "close")):
                try:
                    synth.close()
                finally:
                    self._active_synth = None

        rep.emit("tts", "done", current=total, total=total)
        return results

    def _apply_voice_speed(self, segments: list[dict], seg_dir: str,
                           work_dir: str) -> str:
        """Apply the user's VOICE_SPEED uniformly to every clip.

        The ONLY voice-timing knob: no fitting, no trimming, no per-clip
        decisions. Overlaps are accepted — lower VIDEO_SPEED (or raise
        VOICE_SPEED) and re-run if lines collide. At 1.0 the raw renders
        are used as-is.
        """
        speed = self.settings.voice_speed
        if abs(speed - 1.0) < 0.005:
            return seg_dir
        logger.info(
            f"Chỉnh tốc độ giọng đọc {speed:.2f}x cho tất cả các câu "
            "(theo Cài đặt)"
        )
        from autodub.media.audio import slow_segments
        dst = data_path(work_dir, f"segments_speed{speed:.2f}".replace(".", "_"))
        return slow_segments(segments, seg_dir, dst, speed,
                             max_workers=min(8, self.settings.parallel_workers))

    def _generate_content(
        self, target: TargetLang, segments: list[dict],
        source_url: str | None, work_dir: str, video_path: str | None = None,
    ) -> dict:
        """Bước 8: prompt ảnh bìa + tiêu đề/mô tả/hashtag cho mạng xã hội."""
        rep = self._reporter
        settings = self.settings
        content_result = {"thumbnails": [], "metadata": {}}
        if not settings.generate_metadata:
            logger.info("Bỏ qua tạo tiêu đề và mô tả (đã tắt trong Cài đặt)")
            rep.emit("content", "skip")
            return content_result
        if not (settings.gemini_configured() or settings.translate_configured()):
            logger.info("Bỏ qua tạo tiêu đề và mô tả (chưa có API Key nào "
                        "trong trang Cài đặt)")
            rep.emit("content", "skip")
            return content_result

        logger.info("=" * 60)
        logger.info("STEP 8: Generating thumbnails & social metadata")
        logger.info("Đang viết tiêu đề, mô tả và hashtag cho "
                    "YouTube/TikTok/Facebook...")
        rep.emit("content", "start")
        try:
            from autodub.content.generator import generate_content
            content_result = generate_content(
                segments=segments,
                source_url=source_url,
                output_dir=youtube_dir(work_dir, create=True),
                settings=settings,
                video_path=video_path,
            )
            logger.info("Đã viết xong phần đăng bài và prompt ảnh bìa "
                        "(xem thư mục youtube trong dự án)")
            rep.emit("content", "done")
        except Exception as e:
            logger.error(f"Tạo nội dung đăng bài lỗi (không ảnh hưởng video): {e}")
            rep.emit("content", "error", detail=str(e))
        return content_result

    def _build_report(
        self, target: TargetLang, folder_name: str, req: DubRequest,
        lang_code: str, segments: list[dict], tts_results: list[dict],
        work_dir: str, audio_path: str, merged_audio_path: str,
        dubbed_video_path: str | None, content_result: dict, elapsed: float,
    ) -> dict:
        from autodub.speech.tts import voices as voice_catalog
        return {
            "session_id": folder_name,
            "source_url": req.url,
            "source_language": lang_code,
            "target_language": target.code,
            # Tên giọng đã dùng thật (đã phân giải), để thẻ dự án hiện đúng.
            "voice": voice_catalog.resolve(self.settings, req.voice),
            "total_segments": len(segments),
            "total_original_duration": round(sum(s["duration"] for s in segments), 3),
            "total_tts_duration": round(sum(r["actual_duration"] for r in tts_results), 3),
            "segments_speed_adjusted": sum(1 for r in tts_results if r["speed_adjusted"]),
            "processing_time_seconds": round(elapsed, 1),
            "output_dir": work_dir,
            "files": {
                "original_audio": audio_path,
                "transcript_original_json": data_path(work_dir, "transcript_original.json"),
                "transcript_original_srt": data_path(work_dir, "transcript_original.srt"),
                "transcript_dub_json": data_path(work_dir, target.transcript_name),
                "transcript_dub_srt": os.path.join(work_dir, target.srt_name),
                "dub_audio": merged_audio_path,
                "dubbed_video": dubbed_video_path,
                "thumbnails": content_result.get("thumbnails", []),
                "youtube_metadata": content_result.get("metadata_file"),
            },
        }

    @staticmethod
    def _build_quality_report(target: TargetLang, segments: list[dict],
                              timing_report, settings=None) -> dict:
        """quality_report.json — tổng hợp mọi vấn đề còn lại sau render.

        Nguồn: TimingReport của bước đặt timeline mềm + kiểm tra budget dịch.
        ``per_segment`` chỉ chứa các câu CÓ vấn đề (kèm text để tìm nhanh
        trong editor) — video sạch thì danh sách rỗng.
        """
        from autodub.text.translate_hint import effective_cps, payload_segment

        # Budget theo tốc độ THẬT: giọng đã chỉnh voice_speed; segments lúc
        # này ĐÃ nằm trên timeline kéo dài (video chậm xong rồi) nên không
        # nhân thêm 1/video_speed — không thì câu vừa khít vẫn bị báo oan.
        cps = effective_cps(settings, video_slowdown_pending=False)

        timing = timing_report.to_dict() if timing_report is not None else {}
        by_id = {d.get("id"): d for d in timing.get("details", [])}

        per_segment: list[dict] = []
        over_budget = 0
        for seg in segments:
            issues: dict = dict(by_id.get(seg.get("id"), {}))
            issues.pop("id", None)
            budget = payload_segment(seg, cps).get("max_chars")
            text = str(seg.get(target.text_field, ""))
            if budget and len(text) > budget * 1.25:
                issues["over_budget_chars"] = len(text) - budget
                over_budget += 1
            if issues:
                per_segment.append({
                    "id": seg.get("id"),
                    "start": seg.get("start"),
                    "text": text[:120],
                    **issues,
                })

        return {
            "summary": {
                "segments_total": len(segments),
                "segments_ok": len(segments) - len(per_segment),
                "segments_shifted": timing.get("segments_shifted", 0),
                "max_shift_s": timing.get("max_shift_s", 0.0),
                "segments_compressed": timing.get("segments_compressed", 0),
                "segments_overlapped": timing.get("segments_overlapped", 0),
                "total_overlap_s": timing.get("total_overlap_s", 0.0),
                "segments_over_budget": over_budget,
            },
            "hint": ("Câu 'overlap_prev_s' là chồng tiếng còn lại — rút gọn "
                     "bản dịch câu đó trong tab Chỉnh sửa, hoặc hạ "
                     "VIDEO_SPEED rồi chạy lại. Câu 'over_budget_chars' nên "
                     "được rút gọn để đọc thong thả hơn."),
            "per_segment": per_segment,
        }

    @staticmethod
    def _build_timing_guide(
        target: TargetLang, report: dict,
        segments: list[dict], tts_results: list[dict],
    ) -> dict:
        """Timing guide JSON showing per-segment original vs dub duration.

        Helps the user quickly identify which segments need manual adjustment
        in a video editor (e.g. CapCut).
        """
        lang = target.key  # "vi" — used in field names
        guide = {
            "session_id": report["session_id"],
            "source_url": report["source_url"],
            "target_language": target.code,
            "summary": {
                "total_segments": report["total_segments"],
                "original_duration": report["total_original_duration"],
                f"{lang}_duration": report["total_tts_duration"],
                "ratio": round(report["total_tts_duration"] / report["total_original_duration"], 2)
                         if report["total_original_duration"] > 0 else 0,
                "segments_need_edit": 0,
                "segments_ok": 0,
            },
            "segments": [],
        }

        need_edit = 0
        for seg, tts in zip(segments, tts_results):
            diff = round(tts["actual_duration"] - seg["duration"], 2)

            # OK if dub audio within ±30% of original duration
            if abs(diff) <= seg["duration"] * 0.3:
                status = "OK"
            elif diff > 0:
                status = "TOO_LONG"
                need_edit += 1
            else:
                status = "TOO_SHORT"
                need_edit += 1

            guide["segments"].append({
                "id": seg["id"],
                "text_original": seg.get("text", ""),
                target.text_field: seg.get(target.text_field, ""),
                "start": seg["start"],
                "end": seg["end"],
                "original_duration": seg["duration"],
                f"{lang}_duration": tts["actual_duration"],
                "diff_seconds": diff,
                "speed_adjusted": tts["speed_adjusted"],
                "rate_applied": tts.get("rate_applied", ""),
                "status": status,
                "edit_hint": f"{lang.upper()} {'dài' if diff > 0 else 'ngắn'} hơn {abs(diff):.1f}s"
                             if status != "OK" else "OK",
            })

        guide["summary"]["segments_need_edit"] = need_edit
        guide["summary"]["segments_ok"] = report["total_segments"] - need_edit

        return guide
