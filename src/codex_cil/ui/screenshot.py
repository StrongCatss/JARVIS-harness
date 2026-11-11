from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QScreen,
)
from PySide6.QtWidgets import QWidget

from codex_cil.paths import app_data_dir


def ordered_screens() -> list[QScreen]:
    screens = list(QGuiApplication.screens())

    def sort_key(screen: QScreen) -> tuple[int, int]:
        match = re.search(r"DISPLAY(\d+)", screen.name(), re.IGNORECASE)
        if match:
            return 0, int(match.group(1))
        return 1, screens.index(screen)

    return sorted(screens, key=sort_key)


def capture_screen(index: int) -> QPixmap | None:
    screens = ordered_screens()
    if index < 0 or index >= len(screens):
        return None
    pixmap = screens[index].grabWindow(0)
    return None if pixmap.isNull() else pixmap


def virtual_screen_geometry() -> QRect:
    geometry = QRect()
    for screen in ordered_screens():
        geometry = geometry.united(screen.geometry())
    return geometry


def capture_virtual_desktop() -> QPixmap | None:
    if os.name == "nt":
        image = _capture_windows_virtual_desktop()
        if not image.isNull():
            return QPixmap.fromImage(image)
    return _capture_qt_virtual_desktop()


def save_screenshot(
    pixmap: QPixmap,
    directory: str | Path | None = None,
) -> Path:
    target_directory = (
        Path(directory).expanduser().resolve()
        if directory is not None
        else app_data_dir() / "screenshots"
    )
    target_directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    path = target_directory / f"JARVIS-screenshot-{stamp}.png"
    if not pixmap.save(str(path), "PNG"):
        raise OSError(f"无法保存截图：{path}")
    return path


class ScreenshotOverlay(QWidget):
    captured = Signal(QPixmap)
    cancelled = Signal()

    def __init__(
        self,
        screenshot: QPixmap,
        virtual_geometry: QRect,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setGeometry(virtual_geometry)
        self._source = screenshot
        self._display = screenshot.scaled(
            virtual_geometry.size(),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._start: QPoint | None = None
        self._selection = QRect()
        self._finished = False

    def show_overlay(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        del event
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self._display)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 118))
        selection = self._selection.normalized()
        if selection.width() > 0 and selection.height() > 0:
            painter.drawPixmap(selection, self._display, selection)
            painter.setPen(QPen(QColor("#31e982"), 2))
            painter.drawRect(selection.adjusted(0, 0, -1, -1))
            size_text = f"{selection.width()} × {selection.height()}"
            label = selection.adjusted(6, 6, 6, 6)
            painter.fillRect(
                QRect(label.left(), label.top(), 110, 24),
                QColor(8, 15, 12, 210),
            )
            painter.setPen(QColor("#d9ffe8"))
            painter.drawText(
                QRect(label.left() + 7, label.top() + 3, 100, 18),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                size_text,
            )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._cancel()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = event.position().toPoint()
            self._selection = QRect(self._start, self._start)
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._start is None:
            return
        self._selection = QRect(
            self._start,
            event.position().toPoint(),
        ).normalized()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._start is None:
            return
        self._selection = QRect(
            self._start,
            event.position().toPoint(),
        ).normalized()
        self._start = None
        if self._selection.width() < 4 or self._selection.height() < 4:
            self._selection = QRect()
            self.update()
            return
        source_rect = self._source_rect(self._selection)
        cropped = self._source.copy(source_rect)
        self._finished = True
        self.captured.emit(cropped)
        self.close()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if not self._finished:
            self._finished = True
            self.cancelled.emit()
        super().closeEvent(event)

    def _cancel(self) -> None:
        self._finished = True
        self.cancelled.emit()
        self.close()

    def _source_rect(self, selection: QRect) -> QRect:
        if self.width() <= 0 or self.height() <= 0:
            return QRect()
        scale_x = self._source.width() / self.width()
        scale_y = self._source.height() / self.height()
        return QRect(
            round(selection.left() * scale_x),
            round(selection.top() * scale_y),
            max(1, round(selection.width() * scale_x)),
            max(1, round(selection.height() * scale_y)),
        ).intersected(self._source.rect())


def _capture_windows_virtual_desktop() -> QImage:
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.wintypes.DWORD),
            ("biWidth", ctypes.wintypes.LONG),
            ("biHeight", ctypes.wintypes.LONG),
            ("biPlanes", ctypes.wintypes.WORD),
            ("biBitCount", ctypes.wintypes.WORD),
            ("biCompression", ctypes.wintypes.DWORD),
            ("biSizeImage", ctypes.wintypes.DWORD),
            ("biXPelsPerMeter", ctypes.wintypes.LONG),
            ("biYPelsPerMeter", ctypes.wintypes.LONG),
            ("biClrUsed", ctypes.wintypes.DWORD),
            ("biClrImportant", ctypes.wintypes.DWORD),
        ]

    class RGBQUAD(ctypes.Structure):
        _fields_ = [
            ("rgbBlue", ctypes.c_ubyte),
            ("rgbGreen", ctypes.c_ubyte),
            ("rgbRed", ctypes.c_ubyte),
            ("rgbReserved", ctypes.c_ubyte),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [
            ("bmiHeader", BITMAPINFOHEADER),
            ("bmiColors", RGBQUAD * 1),
        ]

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    gdi32 = ctypes.windll.gdi32  # type: ignore[attr-defined]
    user32.GetDC.restype = ctypes.wintypes.HDC
    user32.GetDC.argtypes = [ctypes.wintypes.HWND]
    user32.ReleaseDC.argtypes = [
        ctypes.wintypes.HWND,
        ctypes.wintypes.HDC,
    ]
    gdi32.CreateCompatibleDC.restype = ctypes.wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [ctypes.wintypes.HDC]
    gdi32.CreateCompatibleBitmap.restype = ctypes.wintypes.HBITMAP
    gdi32.CreateCompatibleBitmap.argtypes = [
        ctypes.wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
    ]
    gdi32.SelectObject.restype = ctypes.wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = [
        ctypes.wintypes.HDC,
        ctypes.wintypes.HGDIOBJ,
    ]
    gdi32.BitBlt.argtypes = [
        ctypes.wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.wintypes.DWORD,
    ]
    gdi32.GetDIBits.argtypes = [
        ctypes.wintypes.HDC,
        ctypes.wintypes.HBITMAP,
        ctypes.wintypes.UINT,
        ctypes.wintypes.UINT,
        ctypes.c_void_p,
        ctypes.POINTER(BITMAPINFO),
        ctypes.wintypes.UINT,
    ]

    left = user32.GetSystemMetrics(76)
    top = user32.GetSystemMetrics(77)
    width = user32.GetSystemMetrics(78)
    height = user32.GetSystemMetrics(79)
    if width <= 0 or height <= 0:
        return QImage()

    screen_dc = user32.GetDC(None)
    if not screen_dc:
        return QImage()
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
    old_bitmap = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not gdi32.BitBlt(
            memory_dc,
            0,
            0,
            width,
            height,
            screen_dc,
            left,
            top,
            0x00CC0020,
        ):
            return QImage()
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        stride = width * 4
        buffer = ctypes.create_string_buffer(stride * height)
        copied = gdi32.GetDIBits(
            memory_dc,
            bitmap,
            0,
            height,
            buffer,
            ctypes.byref(info),
            0,
        )
        if copied != height:
            return QImage()
        return QImage(
            bytes(buffer),
            width,
            height,
            stride,
            QImage.Format.Format_RGB32,
        ).copy()
    finally:
        gdi32.SelectObject(memory_dc, old_bitmap)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(None, screen_dc)


def _capture_qt_virtual_desktop() -> QPixmap | None:
    screens = ordered_screens()
    if not screens:
        return None
    geometry = QRect()
    for screen in screens:
        geometry = geometry.united(screen.geometry())
    canvas = QPixmap(geometry.size())
    canvas.fill(Qt.GlobalColor.black)
    painter = QPainter(canvas)
    try:
        for screen in screens:
            shot = screen.grabWindow(0)
            if shot.isNull():
                continue
            target = screen.geometry().translated(-geometry.topLeft())
            painter.drawPixmap(target, shot, shot.rect())
    finally:
        painter.end()
    return None if canvas.isNull() else canvas
