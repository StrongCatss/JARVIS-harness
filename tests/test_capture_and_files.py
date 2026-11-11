from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap

from codex_cil.ui.composer import ComposerWidget
from codex_cil.ui.explorer_panel import _copy_path_to_directory
from codex_cil.ui.screenshot import (
    _capture_windows_virtual_desktop,
    save_screenshot,
)


def test_explorer_copy_generates_unique_destination(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("JARVIS", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()

    first = _copy_path_to_directory(source, target)
    second = _copy_path_to_directory(source, target)

    assert first.read_text(encoding="utf-8") == "JARVIS"
    assert second.read_text(encoding="utf-8") == "JARVIS"
    assert first.name == "source.txt"
    assert second.name == "source - 复制.txt"


def test_screenshot_is_saved_as_png(
    qtbot,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    pixmap = QPixmap(12, 8)
    pixmap.fill(QColor("#123524"))

    path = save_screenshot(pixmap, tmp_path)

    assert path.exists()
    assert path.suffix == ".png"
    assert QPixmap(str(path)).size().width() == 12
    assert QPixmap(str(path)).size().height() == 8


def test_windows_virtual_desktop_handles_64bit_gdi_bitmap(qtbot) -> None:  # type: ignore[no-untyped-def]
    if os.name != "nt":
        return
    image = _capture_windows_virtual_desktop()
    assert not image.isNull()
    assert image.width() > 0
    assert image.height() > 0


def test_composer_accepts_images_in_pure_chat_and_rejects_regular_files(
    qtbot,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    composer = ComposerWidget()
    qtbot.addWidget(composer)
    composer.set_mode(False, None)
    image = tmp_path / "screen.png"
    pixmap = QPixmap(8, 8)
    pixmap.fill(Qt.GlobalColor.green)
    assert pixmap.save(str(image), "PNG")
    regular = tmp_path / "notes.txt"
    regular.write_text("text", encoding="utf-8")

    assert composer.add_attachment(str(image))
    assert not composer.add_attachment(str(regular))
    assert composer.attachment_paths == [str(image.resolve())]
