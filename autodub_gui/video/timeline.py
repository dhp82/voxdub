"""Dải thời gian: thước đo, dạng sóng, khối câu thoại và con trỏ phát.

Hiệu năng là yêu cầu chính ở đây. Với video dài và hàng trăm câu, mỗi lần vẽ
chỉ được đụng tới phần đang nhìn thấy, nên mọi phép vẽ đều cắt theo khoảng
thời gian hiện ra trên màn hình.
"""
from __future__ import annotations

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF

from autodub_gui import icons, tokens
from autodub_gui.formatting import format_duration
from autodub_gui.ui.buttons import IconButton

RULER_H = 22
WAVE_H = 78
TRACK_H = 30
TOOLBAR_H = 34
TOTAL_H = RULER_H + WAVE_H + TRACK_H + TOOLBAR_H + 12

MIN_ZOOM, MAX_ZOOM = 1.0, 40.0
_ZOOM_STEP = 1.25
_SNAP_S = 0.05                 # bám mốc 0,05 giây khi kéo
_MIN_BLOCK_S = 0.2
_EDGE_GRAB_PX = 6              # vùng bắt mép trái phải của khối
_PLAYHEAD_W = 2
_HANDLE_W = 10
_LABEL_STEPS = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800)
_MIN_LABEL_GAP_PX = 70


class TimelineCanvas(QWidget):
    """Phần vẽ chính: thước, dạng sóng, khối câu và con trỏ phát."""

    seek_requested = Signal(float)
    segment_clicked = Signal(int)
    segment_moved = Signal(int, float, float)     # số câu, mốc đầu, mốc cuối
    split_requested = Signal(int, float)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setMouseTracking(True)
        self.setMinimumHeight(RULER_H + WAVE_H + TRACK_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self._peaks: list[float] = []
        self._segments: list[dict] = []
        self._duration = 0.0
        self._position = 0.0
        self._zoom = MIN_ZOOM
        self._offset = 0.0            # thời điểm ở mép trái, tính bằng giây
        self._selected = -1
        self._scissors = False
        self._drag: dict | None = None
        self._loading = False

    # -- Dữ liệu -------------------------------------------------------
    def set_duration(self, seconds: float) -> None:
        self._duration = max(0.0, seconds)
        self.update()

    def set_peaks(self, values: list[float]) -> None:
        self._peaks = values or []
        self._loading = False
        self.setToolTip("" if self._peaks else
                        "Không đọc được dạng sóng của tệp âm thanh này")
        self.update()

    def set_loading(self, loading: bool) -> None:
        self._loading = loading
        self.update()

    def set_segments(self, segments: list[dict]) -> None:
        self._segments = sorted(segments,
                                key=lambda s: float(s.get("start", 0.0)))
        self.update()

    def set_position(self, seconds: float) -> None:
        self._position = max(0.0, seconds)
        self._keep_playhead_visible()
        self.update()

    def set_selected(self, seg_id: int) -> None:
        self._selected = seg_id
        self.update()

    def set_scissors(self, on: bool) -> None:
        """Bật chế độ cắt: bấm vào một khối để tách câu tại đó."""
        self._scissors = on
        self.setCursor(Qt.CursorShape.SplitHCursor if on
                       else Qt.CursorShape.ArrowCursor)

    # -- Thu phóng -----------------------------------------------------
    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, value: float, anchor: float | None = None) -> None:
        """Đổi mức phóng to, giữ nguyên thời điểm đang ở giữa màn hình."""
        center = anchor if anchor is not None else self._center_time()
        self._zoom = max(MIN_ZOOM, min(MAX_ZOOM, value))
        self._offset = center - self._visible_span() / 2
        self._clamp_offset()
        self.update()

    def zoom_in(self) -> None:
        self.set_zoom(self._zoom * _ZOOM_STEP)

    def zoom_out(self) -> None:
        self.set_zoom(self._zoom / _ZOOM_STEP)

    def _visible_span(self) -> float:
        return self._duration / self._zoom if self._duration else 1.0

    def _center_time(self) -> float:
        return self._offset + self._visible_span() / 2

    def _clamp_offset(self) -> None:
        span = self._visible_span()
        self._offset = max(0.0, min(self._offset, max(0.0, self._duration - span)))

    def _keep_playhead_visible(self) -> None:
        """Cuộn theo con trỏ phát khi nó chạy ra ngoài vùng nhìn thấy."""
        span = self._visible_span()
        if self._position < self._offset or self._position > self._offset + span:
            self._offset = self._position - span / 2
            self._clamp_offset()

    # -- Đổi qua lại giữa thời gian và điểm ảnh -------------------------
    def _to_x(self, seconds: float) -> float:
        span = self._visible_span()
        if span <= 0:
            return 0.0
        return (seconds - self._offset) / span * self.width()

    def _to_time(self, x: float) -> float:
        span = self._visible_span()
        return self._offset + max(0.0, x) / max(1, self.width()) * span

    # -- Vẽ ------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 — theo quy ước của Qt
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(tokens.BG_MAIN))
        if self._duration <= 0:
            self._paint_placeholder(painter)
            painter.end()
            return
        self._paint_ruler(painter)
        self._paint_waveform(painter)
        self._paint_segments(painter)
        self._paint_playhead(painter)
        painter.end()

    def _paint_placeholder(self, painter: QPainter) -> None:
        painter.setPen(QColor(tokens.TEXT_DISABLED))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                         "Chưa có dự án nào được mở")

    def _label_step(self) -> int:
        """Khoảng cách giữa hai mốc chữ trên thước, đủ thưa để không chồng nhau."""
        span = self._visible_span()
        for step in _LABEL_STEPS:
            if step / span * self.width() >= _MIN_LABEL_GAP_PX:
                return step
        return _LABEL_STEPS[-1]

    def _paint_ruler(self, painter: QPainter) -> None:
        rect = QRectF(0, 0, self.width(), RULER_H)
        painter.fillRect(rect, QColor(tokens.BG_SIDEBAR))
        step = self._label_step()
        font = QFont(self.font())
        font.setPixelSize(tokens.FS_BADGE)
        painter.setFont(font)
        start = int(self._offset // step) * step
        end = self._offset + self._visible_span()
        tick = start
        while tick <= end:
            x = self._to_x(tick)
            painter.setPen(QPen(QColor(tokens.BORDER_DEFAULT), 1))
            painter.drawLine(QLineF(x, RULER_H - 6, x, RULER_H))
            painter.setPen(QColor(tokens.RULER_TEXT))
            painter.drawText(QPointF(x + 3, RULER_H - 8),
                             format_duration(tick))
            tick += step

    def _paint_waveform(self, painter: QPainter) -> None:
        top = RULER_H
        rect = QRectF(0, top, self.width(), WAVE_H)
        painter.fillRect(rect, QColor(tokens.BG_PANEL))
        middle = top + WAVE_H / 2
        if not self._peaks:
            self._paint_flat_band(painter, middle)
            return
        lines: list[QLineF] = []
        total = len(self._peaks)
        for x in range(0, self.width()):
            seconds = self._to_time(x)
            index = int(seconds / self._duration * total) if self._duration else 0
            if not 0 <= index < total:
                continue
            half = self._peaks[index] * (WAVE_H / 2 - 2)
            lines.append(QLineF(x, middle - half, x, middle + half))
        painter.setPen(QPen(QColor(tokens.WAVEFORM), 1))
        painter.drawLines(lines)

    def _paint_flat_band(self, painter: QPainter, middle: float) -> None:
        """Không đọc được dạng sóng thì vẽ một dải phẳng mờ, không vẽ sóng giả."""
        color = QColor(tokens.BORDER_DEFAULT)
        painter.setPen(QPen(color, 2))
        painter.drawLine(QLineF(0, middle, self.width(), middle))
        painter.setPen(QColor(tokens.TEXT_DISABLED))
        painter.drawText(
            QRectF(0, middle + 6, self.width(), 18),
            Qt.AlignmentFlag.AlignCenter,
            "Đang đọc dạng sóng…" if self._loading
            else "Không đọc được dạng sóng của tệp âm thanh này")

    def _paint_segments(self, painter: QPainter) -> None:
        top = RULER_H + WAVE_H
        painter.fillRect(QRectF(0, top, self.width(), TRACK_H),
                         QColor(tokens.BG_MAIN))
        visible_end = self._offset + self._visible_span()
        font = QFont(self.font())
        font.setPixelSize(tokens.FS_BADGE)
        painter.setFont(font)
        for segment in self._segments:
            start = float(segment.get("start", 0.0))
            end = float(segment.get("end", 0.0))
            if end < self._offset or start > visible_end:
                continue          # ngoài vùng nhìn thấy, khỏi vẽ
            self._paint_block(painter, segment, start, end, top)

    def _paint_block(self, painter: QPainter, segment: dict,
                     start: float, end: float, top: float) -> None:
        x0, x1 = self._to_x(start), self._to_x(end)
        rect = QRectF(x0, top + 3, max(2.0, x1 - x0), TRACK_H - 6)
        selected = segment.get("id") == self._selected
        painter.setBrush(QColor(tokens.SUB_BLOCK_BG))
        painter.setPen(QPen(QColor(tokens.BORDER_ACTIVE if selected
                                   else tokens.SUB_BLOCK_BORDER),
                            2 if selected else 1))
        painter.drawRoundedRect(rect, 4, 4)
        if rect.width() > 30:
            painter.setPen(QColor(tokens.SUB_BLOCK_TEXT))
            text = painter.fontMetrics().elidedText(
                str(segment.get("text_vi", "")), Qt.TextElideMode.ElideRight,
                int(rect.width()) - 8)
            painter.drawText(rect.adjusted(4, 0, -4, 0),
                             Qt.AlignmentFlag.AlignVCenter, text)

    def _paint_playhead(self, painter: QPainter) -> None:
        x = self._to_x(self._position)
        if x < 0 or x > self.width():
            return
        painter.setPen(QPen(QColor(tokens.PLAYHEAD), _PLAYHEAD_W))
        painter.drawLine(QLineF(x, 0, x, self.height()))
        painter.setBrush(QColor(tokens.PLAYHEAD))
        painter.setPen(Qt.PenStyle.NoPen)
        half = _HANDLE_W / 2
        painter.drawPolygon(QPolygonF([QPointF(x - half, 0),
                                       QPointF(x + half, 0),
                                       QPointF(x, _HANDLE_W)]))

    # -- Tương tác chuột -----------------------------------------------
    def _segment_at(self, x: float, y: float) -> dict | None:
        if not (RULER_H + WAVE_H <= y <= RULER_H + WAVE_H + TRACK_H):
            return None
        seconds = self._to_time(x)
        for segment in self._segments:
            if (float(segment.get("start", 0.0)) <= seconds
                    <= float(segment.get("end", 0.0))):
                return segment
        return None

    def mousePressEvent(self, event) -> None:  # noqa: N802 — theo quy ước của Qt
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x, y = event.position().x(), event.position().y()
        segment = self._segment_at(x, y)
        if segment is not None:
            if self._scissors:
                self.split_requested.emit(int(segment["id"]), self._to_time(x))
                return
            self._begin_drag(segment, x)
            self.segment_clicked.emit(int(segment["id"]))
            return
        self._drag = {"mode": "playhead"}
        self.seek_requested.emit(self._to_time(x))

    def _begin_drag(self, segment: dict, x: float) -> None:
        """Bấm vào giữa khối là dời cả câu, bấm sát mép là kéo dài hoặc rút ngắn."""
        start, end = float(segment["start"]), float(segment["end"])
        left, right = self._to_x(start), self._to_x(end)
        if abs(x - left) <= _EDGE_GRAB_PX:
            mode = "start"
        elif abs(x - right) <= _EDGE_GRAB_PX:
            mode = "end"
        else:
            mode = "move"
        self._drag = {"mode": mode, "id": int(segment["id"]),
                      "start": start, "end": end,
                      "grab": self._to_time(x)}

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 — theo quy ước của Qt
        x, y = event.position().x(), event.position().y()
        if self._drag is None:
            self._update_hover_cursor(x, y)
            return
        if self._drag["mode"] == "playhead":
            self.seek_requested.emit(self._to_time(x))
            return
        self._apply_drag(self._to_time(x))

    def _update_hover_cursor(self, x: float, y: float) -> None:
        if self._scissors:
            return
        segment = self._segment_at(x, y)
        if segment is None:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        left = self._to_x(float(segment["start"]))
        right = self._to_x(float(segment["end"]))
        near_edge = min(abs(x - left), abs(x - right)) <= _EDGE_GRAB_PX
        self.setCursor(Qt.CursorShape.SizeHorCursor if near_edge
                       else Qt.CursorShape.OpenHandCursor)

    def _apply_drag(self, seconds: float) -> None:
        """Tính mốc mới khi kéo, có bám lưới và giữ không lấn câu bên cạnh."""
        drag = self._drag
        start, end = drag["start"], drag["end"]
        if drag["mode"] == "move":
            shift = _snap(seconds - drag["grab"])
            start, end = start + shift, end + shift
        elif drag["mode"] == "start":
            start = min(_snap(seconds), end - _MIN_BLOCK_S)
        else:
            end = max(_snap(seconds), start + _MIN_BLOCK_S)
        start, end = self._limit_to_neighbours(drag["id"], start, end)
        self._preview_move(drag["id"], start, end)

    def _limit_to_neighbours(self, seg_id: int, start: float,
                             end: float) -> tuple[float, float]:
        """Không cho khối lấn sang câu liền trước hoặc câu liền sau."""
        index = next((i for i, s in enumerate(self._segments)
                      if s.get("id") == seg_id), -1)
        if index < 0:
            return start, end
        length = end - start
        if index > 0:
            floor = float(self._segments[index - 1].get("end", 0.0))
            if start < floor:
                start = floor
                end = max(start + length, start + _MIN_BLOCK_S)
        if index < len(self._segments) - 1:
            ceiling = float(self._segments[index + 1].get("start", 0.0))
            if end > ceiling:
                end = ceiling
                start = min(start, end - _MIN_BLOCK_S)
        return max(0.0, start), min(end, self._duration or end)

    def _preview_move(self, seg_id: int, start: float, end: float) -> None:
        """Cập nhật ngay trên màn hình, chưa ghi xuống đĩa."""
        for segment in self._segments:
            if segment.get("id") == seg_id:
                segment["start"], segment["end"] = start, end
                break
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 — quy ước Qt
        drag = self._drag
        self._drag = None
        if drag is None or drag["mode"] == "playhead":
            return
        for segment in self._segments:
            if segment.get("id") == drag["id"]:
                new_start = float(segment["start"])
                new_end = float(segment["end"])
                if (abs(new_start - drag["start"]) > 1e-6
                        or abs(new_end - drag["end"]) > 1e-6):
                    self.segment_moved.emit(drag["id"], new_start, new_end)
                break

    def wheelEvent(self, event) -> None:  # noqa: N802 — theo quy ước của Qt
        """Giữ Ctrl để phóng to thu nhỏ, còn lại là cuộn ngang."""
        delta = event.angleDelta().y()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            anchor = self._to_time(event.position().x())
            self.set_zoom(self._zoom * (_ZOOM_STEP if delta > 0
                                        else 1 / _ZOOM_STEP), anchor)
            return
        self._offset -= delta / 120 * self._visible_span() * 0.1
        self._clamp_offset()
        self.update()


def _snap(seconds: float) -> float:
    """Làm tròn về mốc 0,05 giây gần nhất cho dễ canh."""
    return round(seconds / _SNAP_S) * _SNAP_S


class Timeline(QWidget):
    """Dải thời gian hoàn chỉnh: phần vẽ cộng thanh công cụ bên dưới."""

    seek_requested = Signal(float)
    segment_clicked = Signal(int)
    segment_moved = Signal(int, float, float)
    split_requested = Signal(int, float)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(TOTAL_H)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.canvas = TimelineCanvas()
        self.canvas.seek_requested.connect(self.seek_requested.emit)
        self.canvas.segment_clicked.connect(self.segment_clicked.emit)
        self.canvas.segment_moved.connect(self.segment_moved.emit)
        self.canvas.split_requested.connect(self.split_requested.emit)
        root.addWidget(self.canvas, 1)
        root.addWidget(self._build_toolbar())

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(TOOLBAR_H)
        bar.setStyleSheet(f"background: {tokens.BG_SIDEBAR};")
        row = QHBoxLayout(bar)
        row.setContentsMargins(tokens.SP_2, 0, tokens.SP_2, 0)
        row.setSpacing(tokens.SP_2)

        self.btn_scissors = IconButton(
            icons.scissors(tokens.TEXT_SECONDARY),
            "Chế độ cắt: bấm vào một câu để tách nó tại chỗ bấm",
            size=26, checkable=True)
        self.btn_scissors.toggled.connect(self.canvas.set_scissors)
        row.addWidget(self.btn_scissors)

        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(int(MIN_ZOOM * 10), int(MAX_ZOOM * 10))
        self.zoom_slider.setValue(int(MIN_ZOOM * 10))
        self.zoom_slider.setToolTip("Phóng to hoặc thu nhỏ dải thời gian")
        self.zoom_slider.valueChanged.connect(
            lambda v: self.canvas.set_zoom(v / 10))
        row.addWidget(self.zoom_slider, 1)

        for icon, tip, handler in (
                (icons.zoom_out(tokens.TEXT_SECONDARY), "Thu nhỏ",
                 self._zoom_out),
                (icons.zoom_in(tokens.TEXT_SECONDARY), "Phóng to",
                 self._zoom_in)):
            button = IconButton(icon, tip, size=26)
            button.clicked.connect(handler)
            row.addWidget(button)

        self.zoom_label = QLabel("1.0x")
        self.zoom_label.setStyleSheet(
            f"color: {tokens.TEXT_MUTED}; font-size: {tokens.FS_BADGE}px; "
            f"background: transparent;")
        row.addWidget(self.zoom_label)
        return bar

    def _zoom_in(self) -> None:
        self.canvas.zoom_in()
        self._sync_zoom()

    def _zoom_out(self) -> None:
        self.canvas.zoom_out()
        self._sync_zoom()

    def _sync_zoom(self) -> None:
        value = self.canvas.zoom()
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(int(value * 10))
        self.zoom_slider.blockSignals(False)
        self.zoom_label.setText(f"{value:.1f}x")

    # -- Cửa cho trang cha gọi vào -------------------------------------
    def set_duration(self, seconds: float) -> None:
        self.canvas.set_duration(seconds)

    def set_peaks(self, values: list[float]) -> None:
        self.canvas.set_peaks(values)

    def set_loading(self, loading: bool) -> None:
        self.canvas.set_loading(loading)

    def set_segments(self, segments: list[dict]) -> None:
        self.canvas.set_segments(segments)

    def set_position(self, seconds: float) -> None:
        self.canvas.set_position(seconds)

    def set_selected(self, seg_id: int) -> None:
        self.canvas.set_selected(seg_id)
