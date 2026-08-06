"""Sinh nội dung đăng bài: prompt ảnh bìa + tiêu đề, mô tả, hashtag.

Mỗi dự án nhận đúng ba nhóm tệp, mỗi tệp một việc — không tệp nào lặp nội
dung của tệp khác:

- ``youtube_post.txt`` — CHỈ nội dung đăng bài (YouTube, TikTok, Facebook).
- ``thumbnail_prompts.txt`` — CHỈ prompt tạo ảnh bìa: một prompt khổ ngang
  16:9 cho YouTube và một prompt khổ dọc 9:16 cho TikTok/Shorts/Reels.
- ``script_original.txt`` / ``script_vi.txt`` — lời thoại thuần chữ.

Phần chữ chạy được với Gemini hoặc bất kỳ nơi dịch tương thích OpenAI nào
đang cấu hình; riêng việc VẼ ảnh bìa cần Gemini (API ảnh).
"""
import json
import os
import re
import time

import requests

from autodub.utils import setup_logging

logger = setup_logging("autodub.content_generator")

#: Hai khổ ảnh bìa cần sinh — (khóa, tỉ lệ, kích thước, nơi dùng).
THUMBNAIL_FORMATS: tuple[tuple[str, str, str, str], ...] = (
    ("landscape", "16:9", "1280x720",
     "YouTube (video ngang, ảnh bìa hiện trong kết quả tìm kiếm và đề xuất)"),
    ("portrait", "9:16", "1080x1920",
     "TikTok / YouTube Shorts / Facebook Reels (ảnh bìa video dọc)"),
)


def _extract_video_id(url: str) -> str | None:
    """Lấy mã video YouTube từ một liên kết."""
    if not url:
        return None
    patterns = [
        r"(?:v=|/v/|youtu\.be/)([a-zA-Z0-9_-]{11})",
        r"(?:shorts/)([a-zA-Z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def fetch_original_thumbnail(url: str, output_dir: str) -> str | None:
    """Tải ảnh bìa gốc của video YouTube."""
    video_id = _extract_video_id(url)
    if not video_id:
        return None

    thumb_urls = [
        f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
        f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
    ]
    for thumb_url in thumb_urls:
        try:
            resp = requests.get(thumb_url, timeout=10)
            if resp.status_code == 200 and len(resp.content) > 1000:
                path = os.path.join(output_dir, "thumbnail_original.jpg")
                with open(path, "wb") as f:
                    f.write(resp.content)
                logger.info(f"Đã tải ảnh bìa gốc: {path}")
                return path
        except requests.RequestException:
            continue
    return None


def extract_reference_frame(video_path: str, output_dir: str,
                            at_seconds: float = 30.0) -> str | None:
    """Trích một khung hình của video làm ảnh tham chiếu phong cách.

    Dùng khi video là tệp trên máy (không có ảnh bìa YouTube) — có ảnh tham
    chiếu thì prompt và ảnh sinh ra giữ ĐÚNG phong cách đồ họa của video
    (hoạt hình 2D, 3D hay người thật) thay vì để mô hình tự đoán.
    """
    import subprocess

    if not video_path or not os.path.exists(video_path):
        return None
    out = os.path.join(output_dir, "thumbnail_reference.jpg")
    for ss in (at_seconds, 1.0):
        try:
            result = subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", str(ss), "-i", video_path,
                 "-frames:v", "1", "-q:v", "2", "-y", out],
                capture_output=True, text=True, timeout=60)
            if (result.returncode == 0 and os.path.exists(out)
                    and os.path.getsize(out) > 1000):
                logger.info(f"Đã lấy khung hình tham chiếu: {out}")
                return out
        except (OSError, subprocess.TimeoutExpired):
            return None
    return None


def extract_script_text(segments: list[dict], text_field: str,
                        output_path: str) -> str:
    """Rút lời thoại thuần chữ ra tệp .txt và trả về chính chuỗi đó."""
    lines = []
    for seg in segments:
        text = str(seg.get(text_field) or seg.get("text", "")).strip()
        if text:
            lines.append(text)
    script_text = " ".join(lines)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(script_text)
    return script_text


# ------------------------------------------------------- nội dung đăng bài -- #

def _metadata_prompt(script_original: str, script_translated: str) -> str:
    """Lời nhắc viết nội dung đăng bài, dùng chung cho mọi nơi gọi mô hình."""
    lang_name = "Vietnamese"
    orig_trimmed = script_original[:800]
    trans_trimmed = script_translated[:1200]

    return f"""You are a Vietnamese content creator with millions of followers who writes ALL posts yourself. Based on the video script below, write the posting content for YouTube, TikTok and Facebook.

VOICE & STYLE (CRITICAL — this is what separates viral posts from robotic ones):
- Write like a real Vietnamese creator talking to their audience, NOT like a marketing department. Natural spoken Vietnamese, the way people actually type on social media.
- Lead with the HOOK: the most surprising fact, the twist, the pain point, or the burning question of the video. Never open with generic phrases like "Trong video này...", "Chào mừng các bạn...", "Hãy cùng khám phá...".
- Create curiosity gaps: tease the outcome without spoiling it ("ai ngờ cái kết...", "xem đến cuối mới hiểu", "chi tiết nhỏ mà 90% người bỏ qua").
- Use numbers, contrast and stakes when the content has them ("3 sai lầm", "chỉ 20k", "từ con số 0", "suýt mất trắng").
- STRICTLY NO EMOJI, no emoticons, no decorative symbols (no fire/mind-blown/pointing-hand emoji, no ">>", no "!!!", no ALL-CAPS words). Plain Vietnamese text and punctuation only.
- No hollow clickbait: every claim in the title must actually be answered in the video.

Now write:

1. **Title** (YouTube, max 70 chars): one hook-driven line in {lang_name} that makes people click, with the main search keyword of the topic appearing naturally. Think how top Vietnamese YouTubers title this exact video.
2. **Description** (YouTube, 150-300 words, {lang_name}):
   - First 2 lines = the hook expanded (these show before "xem thêm" — make them count).
   - Then what the viewer will get, written as flowing text, not bullet-point corporate speak.
   - End with ONE natural call-to-action sentence (asking a question that invites comments beats "nhớ like share subscribe").
3. **Hashtags** (YouTube): 10-15 hashtags — topic keywords people actually search, mix of {lang_name} (no diacritics is fine and common) and English. Specific beats generic: #meovat #suachua beat #video #hay.
4. **TikTok**:
   - "title": max 60 chars, {lang_name} — a scroll-stopping caption in authentic TikTok voice (curiosity gap, bold claim, or relatable pain). No emoji.
   - "hashtags": 4-6 tags — 2-3 SPECIFIC to the video topic + the broad-reach ones that genuinely fit (#xuhuong, #fyp, #foryou, #learnontiktok, #reviewphim, #meovat...). Specific tags first.
5. **Facebook**:
   - "title": max 120 chars, {lang_name} — a share-worthy caption, conversational like telling a friend, ideally ending with a question that makes people comment or tag friends. No emoji.
   - "hashtags": 2-4 tags only (Facebook users hate hashtag walls).

Original script:
{orig_trimmed}

Translated script ({lang_name}):
{trans_trimmed}

Respond in this exact JSON format (no markdown code blocks):
{{"title": "...", "description": "...", "hashtags": ["#tag1", ...], "tiktok": {{"title": "...", "hashtags": ["#tag1", ...]}}, "facebook": {{"title": "...", "hashtags": ["#tag1", ...]}}}}"""


# Emoji và ký hiệu trang trí (hình tượng, dingbat, biến thể, cờ, tông da).
# Không đụng tới chữ tiếng Việt hay dấu câu thường.
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # hình tượng, mặt cười, phương tiện, ký hiệu...
    "\U00002700-\U000027BF"   # dingbat
    "\U00002600-\U000026FF"   # ký hiệu linh tinh
    "\U0001F1E6-\U0001F1FF"   # cờ quốc gia
    "\U0000FE00-\U0000FE0F"   # ký tự chọn biến thể
    "\U0001F3FB-\U0001F3FF"   # tông da
    "\U0000200D"              # dấu nối độ rộng bằng không
    "\U000020E3"              # dấu phím kết hợp
    "\U00002B00-\U00002BFF"   # mũi tên, ngôi sao
    "\U00002190-\U000021FF"   # mũi tên
    "\U00002500-\U000025FF"   # khung, hình khối
    "]+"
)


def _strip_emoji(text: str) -> str:
    """Bỏ emoji và ký hiệu trang trí, dọn khoảng trắng thừa còn lại."""
    cleaned = _EMOJI_RE.sub("", str(text))
    return " ".join(cleaned.split()) if cleaned != text else text


def _strip_emoji_metadata(meta: dict) -> dict:
    """Áp :func:`_strip_emoji` lên mọi trường chữ của nội dung đăng bài.

    Người dùng cấm emoji tuyệt đối — lời nhắc đã dặn nhưng mô hình vẫn hay
    lỡ tay, nên đây là lớp bảo đảm cuối cùng.
    """
    if not isinstance(meta, dict):
        return meta
    out: dict = {}
    for k, v in meta.items():
        if isinstance(v, str):
            out[k] = _strip_emoji(v)
        elif isinstance(v, list):
            out[k] = [_strip_emoji(x) if isinstance(x, str) else x for x in v]
        elif isinstance(v, dict):
            out[k] = _strip_emoji_metadata(v)
        else:
            out[k] = v
    return out


def _parse_metadata_reply(text: str) -> dict:
    """Đọc (và vá nếu cần) khối JSON nội dung đăng bài mô hình trả về."""
    from autodub.text.translate_common import repair_json, strip_fences

    text = strip_fences(text)
    for candidate in (text, repair_json(text)):
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            return _strip_emoji_metadata(data)

    # Vá không nổi — bóc từng trường bằng biểu thức chính quy.
    title_match = re.search(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    desc_match = re.search(r'"description"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    tags = re.findall(r'"(#[^"]+)"', text)
    if title_match:
        extra = {}
        for platform in ("tiktok", "facebook"):
            m = re.search(
                r'"' + platform + r'"\s*:\s*\{[^{}]*?"title"\s*:\s*'
                r'"((?:[^"\\]|\\.)*)"([^{}]*)', text)
            if m:
                extra[platform] = {
                    "title": m.group(1),
                    "hashtags": re.findall(r'"(#[^"]+)"', m.group(2)),
                }
        return _strip_emoji_metadata({
            "title": title_match.group(1),
            "description": desc_match.group(1) if desc_match else "",
            "hashtags": tags,
            **extra,
        })
    logger.error("Không đọc được nội dung đăng bài do mô hình trả về")
    # Gắn nhãn rõ ràng: đây là câu trả lời thô của mô hình, KHÔNG phải mô
    # tả — trình bày như mô tả sẽ khiến người dùng dán nhầm lên YouTube.
    return {"title": "Video",
            "description": ("[Không đọc được kết quả AI — nội dung thô bên "
                            "dưới, hãy tự viết mô tả]\n" + text[:1500]),
            "hashtags": []}


_SYSTEM = ("You are a top Vietnamese content creator writing your own social "
           "posts. Natural spoken Vietnamese, hook-first, no emoji ever. "
           "Reply with pure JSON only.")


def generate_social_metadata(script_original: str, script_translated: str,
                             settings) -> dict:
    """Viết tiêu đề, mô tả và hashtag bằng nơi dịch đang cấu hình.

    Ưu tiên chính nơi dịch người dùng đã chọn (API Key đó chắc chắn còn
    hạn mức vì vừa dịch xong), rồi mới tới Gemini. Nơi đầu lỗi thì tự chuyển
    sang nơi còn lại.
    """
    prompt = _metadata_prompt(script_original, script_translated)

    def _via_gemini() -> str:
        from autodub.text.translate_gemini import _generate
        return _generate(settings.google_api_key, settings.content_model_id,
                         system=_SYSTEM, user=prompt, max_retries=3)

    def _via_openai_compat() -> str:
        from autodub.text.translate_openai import chat
        return chat(settings, settings.translate_engine, system=_SYSTEM,
                    user=prompt, max_retries=3)

    attempts: list[tuple[str, object]] = []
    if settings.translate_engine != "gemini" and settings.translate_configured():
        from autodub.text.translate_openai import label_of
        attempts.append((label_of(settings.translate_engine), _via_openai_compat))
    if settings.gemini_configured():
        attempts.append(("Gemini", _via_gemini))
    if (settings.translate_engine != "gemini"
            and not settings.translate_configured()
            and settings.translate_credentials()[0]):
        # API Key có nhưng thiếu địa chỉ/mô hình — vẫn thử, lỗi thì rơi
        # xuống nơi tiếp theo.
        from autodub.text.translate_openai import label_of
        attempts.append((label_of(settings.translate_engine), _via_openai_compat))

    for i, (name, fn) in enumerate(attempts):
        try:
            metadata = _parse_metadata_reply(fn())
            logger.info(f"Đã viết xong nội dung đăng bài bằng {name}: "
                        f"«{str(metadata.get('title', ''))[:50]}»")
            return metadata
        except Exception as e:      # nội dung đăng bài là bước phụ
            if i + 1 < len(attempts):
                logger.warning(f"Viết nội dung bằng {name} lỗi "
                               f"({str(e)[:100]}) — thử {attempts[i + 1][0]}")
            else:
                logger.error(f"Viết nội dung bằng {name} lỗi ({str(e)[:100]}) "
                             "— bỏ qua phần đăng bài (không ảnh hưởng video)")
    if not attempts:
        logger.info("Bỏ qua nội dung đăng bài (chưa có API Key nào)")
    return {}


# ---------------------------------------------------------- prompt ảnh bìa -- #

def _style_block(has_reference: bool) -> str:
    """Đoạn yêu cầu bám đúng phong cách đồ họa của video."""
    if has_reference:
        return (
            "- CRITICAL — MATCH THE VIDEO'S VISUAL STYLE: a reference image "
            "from the actual video is attached. Reproduce its art style "
            "EXACTLY — if it is 2D animation, the thumbnail must be 2D "
            "animation in the same drawing style; if 3D animation, stay 3D "
            "with the same render look; if live-action/real footage, stay "
            "photorealistic. Keep the same characters' appearance, color "
            "palette and overall look. NEVER switch 2D to 3D or animation to "
            "photo-real (or the reverse)."
        )
    return (
        "- CRITICAL — MATCH THE VIDEO'S VISUAL STYLE: infer from the script "
        "whether the video is 2D animation, 3D animation, or live-action, and "
        "render the thumbnail in that SAME style. NEVER mix styles (e.g. a 3D "
        "render for a 2D cartoon)."
    )


def build_thumbnail_prompts(script_original: str, script_translated: str,
                            has_reference: bool = False) -> list[tuple[str, str]]:
    """Hai prompt ảnh bìa: một khổ ngang 16:9, một khổ dọc 9:16.

    Trả về ``[(mô tả nơi dùng, prompt), ...]`` theo đúng thứ tự của
    :data:`THUMBNAIL_FORMATS`. Hai prompt KHÁC NHAU thật sự: khổ ngang bố cục
    trái–phải cho màn hình rộng, khổ dọc bố cục trên–dưới và chừa chỗ cho
    thanh giao diện của ứng dụng điện thoại.
    """
    lang_name = "tiếng Việt"
    lang_code = "Vietnamese"
    orig_short = script_original[:400]
    trans_short = script_translated[:400]
    style_match = _style_block(has_reference)

    landscape = f"""Create a professional YouTube thumbnail image.
ASPECT RATIO: 16:9 horizontal (1280x1920 is WRONG — the image MUST be 1280x720, wider than it is tall).

The video is about:
{orig_short}

Requirements:
{style_match}
- Horizontal composition: main subject on one side, large text block on the other side. Use the full width.
- Bold, eye-catching design with vibrant colors and high contrast so it stands out in YouTube search results and the sidebar.
- Include a SHORT {lang_code} text overlay (max 5-6 words) that summarizes the video topic.
- The text must be in {lang_name}, large, with a strong outline or drop shadow so it stays readable when the thumbnail is shown at postage-stamp size.
- NO small text, NO cluttered design, NO borders or frames.

{lang_code} context:
{trans_short}"""

    portrait = f"""Create a professional vertical cover image for TikTok / YouTube Shorts / Facebook Reels.
ASPECT RATIO: 9:16 vertical (the image MUST be 1080x1920 — taller than it is wide). This is NOT a 16:9 image.

The video is about:
{orig_short}

Requirements:
{style_match}
- Vertical composition: the main subject fills the middle of the frame; the text sits in the UPPER THIRD.
- Keep the bottom 20 percent and the right edge visually simple — the app's caption, username and action buttons cover those areas.
- Use a DIFFERENT, shorter {lang_code} hook than a horizontal thumbnail would use (max 4-5 words), stacked on 1-2 lines.
- The text must be in {lang_name}, very large and bold, readable on a phone screen at arm's length.
- Dramatic lighting and strong colour contrast; a single clear focal point, nothing cluttered.

{lang_code} context:
{trans_short}"""

    return [(THUMBNAIL_FORMATS[0][3], landscape),
            (THUMBNAIL_FORMATS[1][3], portrait)]


def generate_thumbnails(prompts: list[tuple[str, str]],
                        reference_path: str | None, output_dir: str,
                        api_key: str, model_id: str) -> list[str]:
    """Vẽ ảnh bìa bằng API ảnh của Gemini. Trả về danh sách tệp đã lưu."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    saved_paths: list[str] = []

    ref_part = None
    if reference_path and os.path.exists(reference_path):
        with open(reference_path, "rb") as f:
            ref_part = types.Part.from_bytes(data=f.read(),
                                             mime_type="image/jpeg")

    for (key, ratio, size, _where), (_label, prompt) in zip(THUMBNAIL_FORMATS,
                                                            prompts):
        try:
            contents = []
            if ref_part is not None:
                contents.append(
                    "Here is a reference image taken from the actual video. "
                    "MATCH its art style exactly (2D stays 2D, 3D stays 3D, "
                    "live-action stays photorealistic) and keep the "
                    "characters and palette consistent, then follow the "
                    "instructions below:")
                contents.append(ref_part)
            contents.append(prompt)

            response = None
            for attempt in range(5):
                try:
                    response = client.models.generate_content(
                        model=model_id, contents=contents,
                        config=types.GenerateContentConfig(
                            response_modalities=["TEXT", "IMAGE"]),
                    )
                    break
                except Exception as e:
                    error_str = str(e)
                    transient = ("503" in error_str or "UNAVAILABLE" in error_str
                                 or "429" in error_str
                                 or "RESOURCE_EXHAUSTED" in error_str
                                 or "ServerError" in type(e).__name__)
                    if transient and attempt < 4:
                        time.sleep((attempt + 1) * 15)
                    else:
                        raise

            for part in response.parts:
                if part.inline_data is not None:
                    path = os.path.join(output_dir, f"thumbnail_{key}.png")
                    with open(path, "wb") as fh:
                        fh.write(part.inline_data.data)
                    saved_paths.append(path)
                    logger.info(f"Đã tạo ảnh bìa {ratio} ({size}): {path}")
                    break
            else:
                logger.warning(f"Ảnh bìa {ratio}: mô hình không trả về ảnh")
        except Exception as e:
            logger.error(f"Tạo ảnh bìa {ratio} lỗi: {e}")

    return saved_paths


# ------------------------------------------------------------- ghi ra tệp -- #

def _write_post_file(path: str, meta: dict) -> None:
    """``youtube_post.txt`` — CHỈ nội dung đăng bài, không kèm prompt ảnh."""
    tiktok = meta.get("tiktok") or {}
    facebook = meta.get("facebook") or {}
    bar = "=" * 60

    def block(name: str, title: str, description: str,
              hashtags: list) -> list[str]:
        rows = [bar, name, bar, "", f"TIÊU ĐỀ:\n{title}", ""]
        if description:
            rows += [f"MÔ TẢ:\n{description}", ""]
        rows += [f"HASHTAG:\n{' '.join(hashtags or [])}", ""]
        return rows

    lines: list[str] = []
    lines += block("YOUTUBE", meta.get("title", ""),
                   meta.get("description", ""), meta.get("hashtags", []))
    lines += block("TIKTOK", tiktok.get("title", ""), "",
                   tiktok.get("hashtags", []))
    lines += block("FACEBOOK", facebook.get("title", ""), "",
                   facebook.get("hashtags", []))
    lines.append("Prompt tạo ảnh bìa nằm ở tệp thumbnail_prompts.txt "
                 "cùng thư mục này.")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _write_prompts_file(path: str, prompts: list[tuple[str, str]],
                        reference_image: str | None) -> None:
    """``thumbnail_prompts.txt`` — CHỈ prompt ảnh bìa, hai khổ khác nhau."""
    bar = "=" * 60
    lines = [bar, "PROMPT TẠO ẢNH BÌA", bar, "",
             "Dán từng prompt vào công cụ tạo ảnh bạn dùng. Hai prompt dưới "
             "đây khác nhau về BỐ CỤC và TỈ LỆ, đừng dùng lẫn.", ""]
    if reference_image:
        lines += [f"Kèm theo ảnh tham chiếu «{os.path.basename(reference_image)}» "
                  "(cùng thư mục này) để ảnh bìa giữ đúng phong cách đồ họa "
                  "của video.", ""]
    for (_key, ratio, size, where), (label, prompt) in zip(THUMBNAIL_FORMATS,
                                                           prompts):
        lines += [bar, f"KHỔ {ratio}  ({size})", f"Dùng cho: {where}", bar, "",
                  prompt, "", ""]
        del label
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def generate_content(
    segments: list[dict],
    source_url: str | None,
    output_dir: str,
    settings,
    video_path: str | None = None,
) -> dict:
    """Sinh toàn bộ phần nội dung đăng bài của một dự án.

    Các bước:

    1. Rút lời thoại thuần chữ ra tệp (nhẹ, rẻ khi gửi cho mô hình).
    2. Lấy ảnh tham chiếu phong cách: ảnh bìa YouTube gốc, hoặc một khung
       hình của video trên máy.
    3. Ghi hai prompt ảnh bìa (16:9 và 9:16); vẽ luôn thành ảnh nếu người
       dùng bật và có API Key Gemini.
    4. Viết tiêu đề / mô tả / hashtag rồi ghi ra ``youtube_post.txt``.

    Trả về dict có các khóa: thumbnails, metadata, metadata_file,
    thumbnail_prompts_file, post_file.
    """
    result: dict = {"thumbnails": [], "metadata": {}, "metadata_file": None}

    script_original = extract_script_text(
        segments, "text", os.path.join(output_dir, "script_original.txt"))
    script_translated = extract_script_text(
        segments, "text_vi", os.path.join(output_dir, "script_vi.txt"))

    reference_image = None
    if source_url:
        reference_image = fetch_original_thumbnail(source_url, output_dir)
    if reference_image is None and video_path:
        reference_image = extract_reference_frame(video_path, output_dir)

    prompts = build_thumbnail_prompts(script_original, script_translated,
                                      has_reference=reference_image is not None)
    prompts_path = os.path.join(output_dir, "thumbnail_prompts.txt")
    _write_prompts_file(prompts_path, prompts, reference_image)
    result["thumbnail_prompts_file"] = prompts_path

    if settings.generate_thumbnail_images:
        if settings.gemini_configured():
            logger.info("Đang vẽ ảnh bìa...")
            result["thumbnails"] = generate_thumbnails(
                prompts, reference_image, output_dir,
                settings.google_api_key, settings.image_model_id)
        else:
            logger.info("Bỏ qua vẽ ảnh bìa (cần API Key Gemini)")

    result["metadata"] = generate_social_metadata(
        script_original, script_translated, settings)

    metadata_path = os.path.join(output_dir, "youtube_metadata.json")
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(result["metadata"], f, ensure_ascii=False, indent=2)
    result["metadata_file"] = metadata_path

    post_path = os.path.join(output_dir, "youtube_post.txt")
    _write_post_file(post_path, result["metadata"])
    result["post_file"] = post_path
    return result
