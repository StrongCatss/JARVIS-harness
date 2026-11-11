from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

from codex_cil.models import UsageStats
from codex_cil.ui.theme_system import current_theme, theme_manager


def compact_number(value: int | None) -> str:
    if value is None:
        return "—"
    value = max(int(value), 0)
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


class ContextRing(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ratio = 0.0
        self._known = False
        self.setFixedSize(82, 82)
        theme_manager.changed.connect(self._theme_changed)

    def _theme_changed(self, _theme) -> None:  # type: ignore[no-untyped-def]
        self.update()

    def set_ratio(self, ratio: float | None) -> None:
        self._known = ratio is not None
        self._ratio = min(max(ratio or 0.0, 0.0), 1.0)
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        theme = current_theme()
        rect = QRectF(8, 8, self.width() - 16, self.height() - 16)
        background = QPen(
            QColor(theme.border),
            7,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
        )
        painter.setPen(background)
        painter.drawArc(rect, 225 * 16, -270 * 16)

        if self._ratio >= 0.95:
            color = theme.danger
        elif self._ratio >= 0.85:
            color = theme.warning
        elif self._ratio >= 0.70:
            color = theme.warning
        else:
            color = theme.accent
        foreground = QPen(QColor(color), 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(foreground)
        painter.drawArc(rect, 225 * 16, int(-270 * 16 * self._ratio))

        painter.setPen(QColor(theme.text))
        font = QFont("Cascadia Code")
        font.setPixelSize(15)
        font.setBold(True)
        painter.setFont(font)
        text = f"{self._ratio * 100:.1f}%" if self._known else "未知"
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)


class UsageCard(QFrame):
    detailsRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("UsageCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(204)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 10, 13, 10)
        layout.setSpacing(3)

        title = QLabel("上下文")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        self.ring = ContextRing()
        layout.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignHCenter)

        self.context_label = QLabel("0 / — tok")
        self.context_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.context_label.setObjectName("Muted")
        layout.addWidget(self.context_label)

        self.remaining_label = QLabel("剩余 —")
        self.remaining_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.remaining_label.setObjectName("Muted")
        layout.addWidget(self.remaining_label)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(divider)

        self.total_label = QLabel("Token 0 tok")
        self.total_label.setStyleSheet("font-weight:600;")
        layout.addWidget(self.total_label)

        self.cache_label = QLabel("缓存 —")
        self.cache_label.setObjectName("Muted")
        layout.addWidget(self.cache_label)

    def set_usage(self, usage: UsageStats) -> None:
        ratio = usage.context_ratio
        self.ring.set_ratio(ratio)
        self.context_label.setText(
            f"{compact_number(usage.context_tokens)} / {compact_number(usage.context_window)} tok"
        )
        if usage.context_window:
            remaining = max(usage.context_window - usage.context_tokens, 0)
            self.remaining_label.setText(f"剩余 {compact_number(remaining)}")
        else:
            self.remaining_label.setText("剩余 未知")
        self.total_label.setText(f"Token {compact_number(usage.total_tokens)} tok")
        cache_ratio = usage.cache_hit_ratio
        self.cache_label.setText(
            f"缓存 {cache_ratio * 100:.1f}%" if cache_ratio is not None else "缓存 —"
        )

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.detailsRequested.emit()
        super().mouseReleaseEvent(event)
