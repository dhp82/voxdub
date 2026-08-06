import json
import os
import subprocess
from functools import lru_cache

from autodub.utils import setup_logging

logger = setup_logging("autodub.video_merger")


@lru_cache(maxsize=1)
def _nvenc_available() -> bool:
    """True if ffmpeg has the NVENC H.264 encoder and it actually initialises.

    A quick null-sink encode catches the case where ffmpeg lists h264_nvenc
    but the driver/GPU can't run it (old driver, no NVIDIA GPU).
    """
    try:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1",
             "-c:v", "h264_nvenc", "-f", "null", "-"],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def video_codec_args() -> list[str]:
    """Encoder argv shared by every re-encode in the app (merge, retime).

    NVENC (GPU) encodes several times faster than libx264 at comparable
    quality for this use case; fall back to libx264 when unavailable.
    veryfast ≈ 2-3× faster than medium at the same crf; the size bump is
    irrelevant for upload-and-delete dub outputs.
    """
    if _nvenc_available():
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "23", "-b:v", "0"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]


def probe_dimensions(video_path: str) -> tuple[int, int]:
    """Return DISPLAY (width, height) of the first video stream via ffprobe.

    Phone videos often store 1920x1080 with a 90° rotation tag; ffmpeg's
    decoder auto-rotates before the filtergraph, so blur/subtitle coordinates
    must be scaled against the rotated (display) dimensions — otherwise blurs
    land in the wrong place and crops can exceed the frame.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,side_data_list",
        "-of", "json",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {video_path}: {result.stderr}")
    try:
        stream = json.loads(result.stdout)["streams"][0]
        w, h = int(stream["width"]), int(stream["height"])
    except (KeyError, IndexError, ValueError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Could not read dimensions from {video_path}: {e}") from e
    rotation = 0
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                rotation = int(sd["rotation"])
            except (TypeError, ValueError):
                rotation = 0
            break
    if rotation % 180 != 0:
        w, h = h, w
    return w, h


def merge_video(
    video_path: str,
    audio_path: str,
    output_path: str,
    srt_path: str | None = None,
    subtitle_mode: str = "none",
    blur_regions: list[dict] | None = None,
    subtitle_style: dict | None = None,
    subtitle_lang: str = "und",
    speed: float | None = None,
    fps: str | None = None,
) -> str:
    """Mux the dubbed audio into the video, optionally adding subtitles/blur.

    ``subtitle_mode``:

    - ``none`` — audio only (default; stream-copies video, fastest)
    - ``soft`` — embed the SRT as a toggleable subtitle track (still no re-encode)
    - ``burn`` — draw subtitles into the pixels (requires a video re-encode)

    ``blur_regions`` blurs rectangles of the frame to cover hardcoded source
    captions. Coordinates are normalized 0..1 dicts (``x``/``y``/``w``/``h``,
    optional ``t_start``/``t_end``). Any blur forces a re-encode, so it is
    applied in the same pass as burned-in subtitles.

    ``speed`` (< 1.0) slows the video INSIDE the same encode pass
    (``setpts=PTS/speed,fps=<fps>`` prepended to the filtergraph) — the
    fused path for ``VIDEO_SPEED`` when a re-encode happens anyway. Callers
    must pass blur/subtitle timestamps already rescaled to the slowed
    timeline. Requires ``fps`` (ffprobe rational); forces a re-encode even
    without subs/blur.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio not found: {audio_path}")

    if subtitle_mode not in ("none", "soft", "burn"):
        raise ValueError(f"Invalid subtitle_mode: {subtitle_mode!r}")
    if subtitle_mode != "none" and not srt_path:
        raise ValueError(f"subtitle_mode={subtitle_mode!r} requires srt_path")
    if srt_path and subtitle_mode != "none" and not os.path.exists(srt_path):
        raise FileNotFoundError(f"Subtitle file not found: {srt_path}")

    from autodub.media.subtitle import build_filter_complex

    burn_srt = srt_path if subtitle_mode == "burn" else None
    filter_complex = None
    if blur_regions or burn_srt:
        width, height = probe_dimensions(video_path)
        filter_complex = build_filter_complex(
            blur_regions, width, height, burn_srt, subtitle_style
        )

    apply_speed = speed is not None and speed < 0.999
    if apply_speed:
        if not fps:
            raise ValueError("speed requires fps (ffprobe rational)")
        setpts = f"setpts=PTS/{speed},fps={fps}"
        if filter_complex:
            # setpts BEFORE blur/subs: their timestamps are on the slowed
            # timeline, so the frames must already be retimed when they apply.
            filter_complex = (f"[0:v]{setpts}[vslow];"
                              + filter_complex.replace("[0:v]", "[vslow]", 1))
        else:
            filter_complex = f"[0:v]{setpts}[vout]"

    cmd = ["ffmpeg", "-i", video_path, "-i", audio_path]
    if subtitle_mode == "soft":
        cmd += ["-i", srt_path]

    if filter_complex:
        # Re-encode: the filtergraph rewrites pixels, so -c:v copy is impossible.
        codec = video_codec_args()
        cmd += [
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "1:a",
            *codec,
            "-pix_fmt", "yuv420p",
        ]
        if apply_speed:
            cmd += ["-fps_mode", "cfr"]
    else:
        # 0:v:0 (not 0:v): downloaded MP4s can carry an attached-picture
        # thumbnail stream that would also be stream-copied.
        cmd += ["-c:v", "copy", "-map", "0:v:0", "-map", "1:a"]

    if subtitle_mode == "soft":
        # mov_text is the subtitle codec MP4 containers accept.
        cmd += ["-map", "2:0", "-c:s", "mov_text",
                "-metadata:s:s:0", f"language={subtitle_lang}"]

    cmd += ["-c:a", "aac", "-b:a", "192k", "-y", output_path]

    what = ["audio"]
    if subtitle_mode != "none":
        what.append(f"{subtitle_mode} subs")
    if blur_regions:
        what.append(f"{len(blur_regions)} blur region(s)")
    if apply_speed:
        what.append(f"speed {speed}x")
    logger.info(f"Merging video + {' + '.join(what)} → {output_path}")
    if filter_complex:
        logger.info("Re-encoding video (filters applied) — this takes a while")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg merge failed: {result.stderr}")

    logger.info(f"Video merged: {output_path}")
    return output_path
