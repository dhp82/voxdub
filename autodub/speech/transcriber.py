import ctypes
import json
import os

from autodub.config import Settings
from autodub.languages import WHISPER_LANG_MAP
from autodub.utils import gpu_venv_dir, setup_logging

logger = setup_logging("autodub.transcriber")


def _enable_cuda_dlls() -> bool:
    """Cho faster-whisper nạp được cuBLAS/cuDNN trên Windows.

    ctranslate2 cần cublas và cudnn, mà venv chính không mang theo — nhưng
    bản torch trong venv CUDA thì có sẵn. Nạp trước từ đó là Whisper chạy
    được trên card đồ họa mà không phải cài thêm gì. Trả về True khi các
    tệp .dll dùng được (ngoài Windows thì coi như hệ thống đã có).
    """
    if os.name != "nt":
        return True
    import glob

    venv = gpu_venv_dir()
    if not venv:
        return False
    lib_dir = os.path.join(venv, "Lib", "site-packages", "torch", "lib")
    # Glob thay vì ghim cublas64_12: torch cu13 mang cublas64_13 — ghim
    # cứng số phiên bản là rơi về CPU âm thầm sau một lần nâng cấp venv.
    matches = glob.glob(os.path.join(lib_dir, "cublas64_*.dll"))
    if not matches:
        return False
    try:
        os.add_dll_directory(lib_dir)
        ctypes.CDLL(matches[0])
        return True
    except OSError:
        return False


def asr_will_use_gpu(settings: Settings, language: str) -> bool:
    """Bước ASR sắp tới có tranh card đồ họa với Demucs không?

    Trả về False khi ASR chắc chắn chạy CPU: Paraformer (ONNX/CPU trong
    .venv-asr) cho tiếng Trung, hoặc Whisper mà không nạp được cuBLAS/cuDNN.
    Pipeline dùng câu trả lời này để quyết định có cho Demucs (GPU) chạy
    song song với ASR hay không — sai về phía True là an toàn (chỉ mất cơ
    hội song song, không bao giờ làm hai việc giành nhau GPU).
    """
    if (settings.asr_engine == "paraformer"
            and (language or "").lower().startswith("zh")
            and settings.paraformer_configured()):
        return False
    return _enable_cuda_dlls()


def _load_whisper_model(model_name: str, settings: Settings):
    """Load faster-whisper on GPU when possible, falling back to CPU.

    ``model_name`` may be "auto": large-v3 when CUDA works (ASR runs
    GPU-exclusive so the ~3 GB fits even on 6 GB cards), medium on CPU —
    the accuracy gap on Chinese sources is the single biggest translation-
    quality lever upstream of the translator itself.
    """
    from faster_whisper import WhisperModel

    if _enable_cuda_dlls():
        resolved = settings.resolved_whisper_model(cuda_available=True)
        try:
            model = WhisperModel(resolved, device="cuda", compute_type="int8")
            logger.info(f"Whisper '{resolved}' chạy trên GPU (CUDA)")
            return model
        except Exception as e:
            logger.warning(f"Không chạy được Whisper trên GPU ({e}) — dùng CPU")
    resolved = settings.resolved_whisper_model(cuda_available=False)
    return WhisperModel(resolved, device="cpu", compute_type="int8")


def transcribe(audio_path: str, language: str, settings: Settings) -> list[dict]:
    """Transcribe audio with the configured local ASR (free, offline).

    Engines: Whisper (default, multilingual) or Paraformer (Chinese only,
    CPU/ONNX in .venv-asr — more accurate on zh sources). Paraformer failures
    or misconfiguration fall back to Whisper so a run never dies here.

    Segments keep the engine's fragment granularity — translation, subtitles,
    the editor AND the voice all follow the source video's own per-fragment
    timeline (strict 1:1 rendering, one clip per segment).
    """
    segments = None
    if settings.asr_engine == "paraformer":
        if not (language or "").lower().startswith("zh"):
            logger.warning("Paraformer chỉ hỗ trợ tiếng Trung — dùng Whisper "
                           f"cho ngôn ngữ '{language}'")
        elif not settings.paraformer_configured():
            logger.warning("Paraformer chưa cài (đúp chuột 'Cai dat ASR tieng "
                           "Trung (Paraformer).bat') — dùng Whisper")
        else:
            try:
                from autodub.speech.paraformer_transcriber import (
                    transcribe_paraformer)
                segments = transcribe_paraformer(audio_path, settings)
            except Exception as e:
                logger.warning(f"Paraformer lỗi ({e}) — chuyển sang Whisper")
    if segments is None:
        segments = _transcribe_whisper(audio_path, language, settings)

    logger.info(f"Transcription complete: {len(segments)} raw segments")

    # Split long segments into ~MAX_SEGMENT_DURATION chunks
    segments = split_long_segments(segments, max_duration=10.0)
    logger.info(f"After splitting: {len(segments)} segments")

    # words đã phục vụ xong việc cắt — bỏ khỏi transcript trên đĩa (giữ
    # format cũ, file nhỏ, các bước sau không phải biết tới nó).
    for seg in segments:
        seg.pop("words", None)

    return segments


def _release_vram() -> None:
    """Free the Whisper model's VRAM immediately after transcription.

    Whisper large-v3 giữ khoảng 2 GB VRAM; để nó nằm lại tới hết lượt chạy
    là chiếm chỗ vô ích của các bước sau. ctranslate2 chỉ trả bộ nhớ card khi
    đối tượng model bị thu gom — lớp gọi bỏ tham chiếu trước, rồi hàm này ép
    thu gom ngay thay vì đợi tới lúc tiến trình thoát.
    """
    import gc

    gc.collect()
    logger.info("Đã giải phóng bộ nhớ Whisper cho bước tạo giọng")


def _transcribe_whisper(audio_path: str, language: str, settings: Settings) -> list[dict]:
    """Local ASR via faster-whisper — free, offline, no API key needed.

    ``word_timestamps=True``: mỗi segment mang kèm mảng ``words``
    (word + start/end thật) để :func:`split_long_segments` cắt câu dài tại
    mốc thời gian THẬT của từ, thay vì chia theo tỷ lệ ký tự (đoán). Mảng
    words bị loại khỏi kết quả cuối — transcript trên đĩa giữ format cũ.
    """
    whisper_lang = WHISPER_LANG_MAP.get(language, language.split("-")[0])
    model_name = settings.whisper_model

    logger.info(f"Loading Whisper model: {model_name} (first run downloads the model)")
    model = _load_whisper_model(model_name, settings)

    logger.info(f"Starting transcription: {audio_path} (language: {whisper_lang})")
    raw_segments, info = model.transcribe(
        audio_path,
        language=whisper_lang,
        beam_size=settings.whisper_beam_size,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        word_timestamps=True,
    )
    if whisper_lang is None and getattr(info, "language", None):
        logger.info(f"Ngôn ngữ tự nhận dạng: {info.language} "
                    f"(độ tin cậy {getattr(info, 'language_probability', 0):.0%})")

    segments = []
    segment_id = 0
    for seg in raw_segments:
        text = seg.text.strip()
        if not text:
            continue
        segment_id += 1
        start = seg.start
        end = seg.end
        segment = {
            "id": segment_id,
            "text": text,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
        }
        words = getattr(seg, "words", None)
        if words:
            segment["words"] = [
                {"word": w.word, "start": round(w.start, 3),
                 "end": round(w.end, 3)}
                for w in words
            ]
        segments.append(segment)
        logger.info(f"Segment {segment_id}: [{start:.1f}s-{end:.1f}s] {text[:50]}...")

    # The transcript is fully materialised — hand the VRAM back before the
    # TTS step sizes its worker pool. raw_segments (a generator) also pins
    # the model, so drop both before collecting.
    del model, raw_segments
    _release_vram()

    return segments


def _word_boundary_time(seg: dict, char_pos: int, fallback: float) -> float:
    """Thời điểm THẬT (giây) của ranh giới sau ``char_pos`` ký tự văn bản.

    Dò trong ``seg["words"]`` (word_timestamps của faster-whisper): cộng dồn
    độ dài từng từ đến khi đạt ``char_pos`` rồi lấy ``end`` của từ đó. Không
    có words (Paraformer, transcript cũ) → trả ``fallback`` (ước theo ký tự).
    """
    words = seg.get("words")
    if not words:
        return fallback
    acc = 0
    for w in words:
        acc += len(str(w.get("word", "")))
        if acc >= char_pos:
            end = w.get("end")
            return float(end) if end is not None else fallback
    return fallback


def split_long_segments(segments: list[dict], max_duration: float = 10.0) -> list[dict]:
    """Split segments longer than max_duration into smaller ones at sentence boundaries.

    Uses punctuation (. ! ? ;) to find split points. Chunk boundary TIMES come
    from real word timestamps when the ASR provided them (Whisper
    ``word_timestamps=True``) — the char-proportional estimate is only the
    fallback. A boundary that lands hundreds of ms off pushes every dub clip
    of the tail chunk onto the wrong spot, which the viewer hears as
    lip-sync drift.
    """
    import re
    result = []
    new_id = 0

    for seg in segments:
        if seg["duration"] <= max_duration:
            new_id += 1
            result.append({**seg, "id": new_id})
            continue

        # Split text at sentence boundaries. CJK marks (。！？；) carry no
        # trailing space, so match with \s* — benefits both Paraformer output
        # and Whisper zh transcripts.
        sentences = re.split(r'(?<=[.!?;。！？；])\s*', seg["text"].strip())
        sentences = [s for s in sentences if s]
        if len(sentences) <= 1:
            # No sentence boundary found, keep as-is
            new_id += 1
            result.append({**seg, "id": new_id})
            continue

        # Group sentences into chunks that fit within max_duration
        total_chars = sum(len(s) for s in sentences)
        total_duration = seg["duration"]
        start = seg["start"]

        chunk_sentences = []
        chunk_chars = 0
        consumed_chars = 0   # ký tự đã chốt vào các chunk trước (tra words)

        for sentence in sentences:
            estimated_chunk_duration = (chunk_chars + len(sentence)) / total_chars * total_duration

            # If adding this sentence exceeds max_duration and we already have content, flush
            if chunk_sentences and estimated_chunk_duration > max_duration:
                chunk_duration = chunk_chars / total_chars * total_duration
                est_end = start + chunk_duration
                consumed_chars += chunk_chars
                # Mốc thật từ word timestamps; kẹp trong (start, seg.end) để
                # words lệch/thiếu không sinh đoạn âm.
                end = _word_boundary_time(seg, consumed_chars, est_end)
                end = round(min(max(end, start + 0.1), seg["end"] - 0.1), 3)
                new_id += 1
                result.append({
                    "id": new_id,
                    "text": " ".join(chunk_sentences),
                    "start": round(start, 3),
                    "end": end,
                    "duration": round(end - start, 3),
                })
                start = end
                chunk_sentences = []
                chunk_chars = 0

            chunk_sentences.append(sentence)
            chunk_chars += len(sentence)

        # Flush remaining
        if chunk_sentences:
            # Ước lượng ký tự có thể trôi qua seg["end"] (join spaces không
            # được đếm trong total_chars) — kẹp lại để không sinh đoạn có
            # thời lượng âm.
            end = seg["end"]
            start = min(start, end - 0.1)
            new_id += 1
            result.append({
                "id": new_id,
                "text": " ".join(chunk_sentences),
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(end - start, 3),
            })

    return result


def save_transcript(segments: list[dict], output_path: str) -> str:
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False, indent=2)
    logger.info(f"Transcript saved: {output_path}")
    return output_path
