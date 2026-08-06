"""Design token — nguồn sự thật DUY NHẤT về màu, khoảng cách và bo góc.

Mọi file giao diện đều lấy màu từ đây. Cấm viết mã màu hex ở nơi khác
(bài kiểm thử `tests/test_ui_tokens.py` sẽ báo lỗi).
"""
from __future__ import annotations

# -- Nền --------------------------------------------------------------
BG_APP          = "#0a0a12"   # nền cửa sổ
BG_SIDEBAR      = "#0c0c15"   # thanh bên (sâu hơn một bậc)
BG_MAIN         = "#0d0d17"   # vùng nội dung
BG_PANEL        = "#14141f"   # thẻ / khung nhóm
BG_PANEL_HOVER  = "#1a1a28"
BG_INPUT        = "#1b1b29"   # ô nhập, nút phụ

# Nền phụ trợ (dẫn xuất, dùng trong bảng kiểu QSS)
BG_INPUT_DISABLED = "#16161f"
BG_BUTTON         = "#171722"
BG_BUTTON_PRESSED = "#12121b"
BG_VIDEO          = "#07070d"
BG_SELECTED       = "#1f1f38"
BG_SELECTED_SOFT  = "#1a1a2e"

# -- Viền -------------------------------------------------------------
BORDER_SUBTLE   = "#1f1f30"
BORDER_DEFAULT  = "#26263c"
BORDER_ACTIVE   = "#5b5fef"
BORDER_BUTTON   = "#262640"
BORDER_DANGER   = "#3a1c22"
BORDER_UPLOAD   = "#34347a"

# -- Màu chính --------------------------------------------------------
PRIMARY         = "#5b5fef"
PRIMARY_HOVER   = "#6e72f5"
PRIMARY_DARK    = "#4548c4"
PRIMARY_GRAD_B  = "#7a4ff0"   # điểm cuối dải chuyển sắc của nút chính
PRIMARY_GRAD_B_HOVER = "#8b62f7"
PRIMARY_DISABLED_BG  = "#202038"

# -- Màu nhấn ---------------------------------------------------------
ACCENT_BLUE     = "#7b93ff"
ACCENT_PURPLE   = "#8b5cf6"
ACCENT_PURPLE_HOVER = "#9d73f8"

# -- Chữ --------------------------------------------------------------
TEXT_PRIMARY    = "#f0f0fa"
TEXT_SECONDARY  = "#a0a2b8"
TEXT_MUTED      = "#6e708a"
TEXT_DISABLED   = "#47485e"
TEXT_ON_ACCENT  = "#ffffff"

# -- Trạng thái -------------------------------------------------------
SUCCESS         = "#69c983"
WARNING         = "#e7aa47"
DANGER          = "#f06a5e"
PROCESSING      = "#7b7ff2"

# Nền huy hiệu (tối, độ tương phản thấp — KHÔNG dùng màu bão hòa làm nền)
SUCCESS_BG      = "#12241a"
WARNING_BG      = "#29200d"
DANGER_BG       = "#2a1216"
PROCESSING_BG   = "#191938"
NEUTRAL_BG      = "#1b1b29"
PURPLE_BG       = "#1d1440"

# -- Dải thời gian ----------------------------------------------------
WAVEFORM         = "#5b5fef"
WAVEFORM_LIGHT   = "#8b8ef7"
PLAYHEAD         = "#e0518f"   # hồng tím, lấy mẫu từ ảnh tham chiếu
SUB_BLOCK_BG     = "#5b4314"
SUB_BLOCK_BORDER = "#927226"
SUB_BLOCK_TEXT   = "#f2cf74"
RULER_TEXT       = "#687589"

# -- Khung xem trước kiểu phụ đề --------------------------------------
PREVIEW_CANVAS_BG   = "#141517"   # nền khung xem trước
PREVIEW_GUIDE       = "#3f6fb5"   # đường canh vị trí chữ
PREVIEW_BLUR_EDGE   = "#c2913a"   # viền vùng che
PREVIEW_EMPTY_BG    = "#26282c"   # nền khi chưa lấy được khung hình
PREVIEW_EMPTY_TEXT  = "#4a4d55"
LOG_BG              = "#17181b"   # nền khung nhật ký

# -- Màu mặc định của chữ phụ đề ghi lên video -------------------------
# Đây là màu của nội dung xuất ra, không phải màu giao diện, nhưng vẫn để ở
# đây vì mọi mã màu trong dự án đều phải tập trung một chỗ.
SUBTITLE_TEXT_DEFAULT      = "#FFFFFF"
SUBTITLE_OUTLINE_DEFAULT   = "#000000"
SUBTITLE_HIGHLIGHT_DEFAULT = "#FFD54A"
SUBTITLE_BOXFILL_DEFAULT   = "#000000"   # khối nền mờ sau chữ

# -- Thanh cuộn và rãnh trượt -----------------------------------------
STEP_DONE_BG        = "#5b5fef"   # vòng tròn bước đã xong
STEP_UPCOMING_BG    = "#22223a"   # vòng tròn bước chưa tới
STEP_UPCOMING_TEXT  = "#8f91aa"

TRACK_BG        = "#26263e"
SCROLL_HANDLE_HOVER = "#3d3d5c"
BRAND_LOGO_BG   = "#26265c"

# -- Thẻ giọng đọc & chip lọc -----------------------------------------
CHIP_BG            = "#1b1b29"   # nền chip lọc (thường)
CHIP_BG_ACTIVE     = "#232345"   # nền chip đang chọn
CHIP_BORDER_ACTIVE = "#5b5fef"   # viền chip đang chọn (= PRIMARY)
VOICE_SELECTED_BG  = "#191932"   # nền thẻ giọng đang chọn
SECTION_LABEL      = "#55566e"   # chữ CÔNG CỤ / HỆ THỐNG trong thanh bên

# Cặp màu gradient cho vòng tròn chữ cái đầu (avatar giọng đọc).
# Chọn cặp theo tên giọng bằng hàm băm ổn định để mỗi giọng luôn một màu.
AVATAR_GRADIENTS = (
    ("#5b5fef", "#8b5cf6"),
    ("#e0518f", "#8b5cf6"),
    ("#3ba7f0", "#5b5fef"),
    ("#69c983", "#3ba78f"),
    ("#e7aa47", "#f06a5e"),
    ("#8b5cf6", "#e0518f"),
)

# -- Màu bán trong suốt dùng trong QSS (Qt nhận alpha 0..255) ----------
NAV_SEL_GRAD_A  = "rgba(91,95,239,140)"    # mục đang chọn, phía trái
NAV_SEL_GRAD_B  = "rgba(122,79,240,110)"   # mục đang chọn, phía phải
NAV_HOVER_BG    = "rgba(255,255,255,9)"    # khoảng 3,5% trắng
MODAL_OVERLAY   = "rgba(6,6,12,158)"       # khoảng 0,62 alpha
DURATION_BADGE_BG = "rgba(0,0,0,184)"      # khoảng 0,72 alpha
UPLOAD_GRAD_A   = "rgba(91,95,239,16)"     # nền thẻ tải lên, chàm khoảng 6%
UPLOAD_GRAD_B   = "rgba(139,92,246,16)"    # nền thẻ tải lên, tím khoảng 6%
DRAG_ACTIVE_BG  = "rgba(91,95,239,26)"     # khoảng 0,10 alpha
PLAYER_BAR_BG   = "rgba(12,12,21,235)"     # khoảng 0,92 alpha
SUBTITLE_BOX_BG = "rgba(0,0,0,140)"        # khoảng 0,55 alpha

# -- Bo góc -----------------------------------------------------------
RADIUS_SM = 6
RADIUS_MD = 9
RADIUS_LG = 12
RADIUS_XL = 16

# -- Khoảng cách (lưới 4px) -------------------------------------------
SP_1, SP_2, SP_3, SP_4, SP_5, SP_6, SP_8 = 4, 8, 12, 16, 20, 24, 32

# -- Kiểu chữ ---------------------------------------------------------
FONT_STACK = '"Segoe UI Variable", "Segoe UI", Arial, sans-serif'
FONT_MONO = '"Consolas", "Cascadia Mono", monospace'
FS_PAGE_TITLE   = 25   # tiêu đề trang
FS_SECTION      = 17   # tiêu đề mục
FS_CARD_TITLE   = 14
FS_BODY         = 13
FS_LABEL        = 12
FS_META         = 11
FS_BADGE        = 10

# -- Kích thước cố định -----------------------------------------------
SIDEBAR_W        = 220   # co xuống 200 khi cửa sổ dưới 1440, còn 64 khi dưới 1024
SIDEBAR_W_COMPACT = 200
SIDEBAR_W_ICON   = 64
NAV_ITEM_H       = 40
HEADER_H         = 72
CARD_MIN_W       = 240

# -- Đổ bóng (Qt dùng QGraphicsDropShadowEffect) ----------------------
SHADOW_BLUR   = 24
SHADOW_Y      = 8
SHADOW_ALPHA  = 46      # tương đương rgba(0,0,0,.18)


def rgba(hex_color: str, alpha: float) -> str:
    """Đổi mã hex '#rrggbb' thành chuỗi 'rgba(r,g,b,a)' dùng trong QSS."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha:.3f})"
