from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from codex_cil.ui.commands import CommandRegistry, CommandSpec


def test_window_shortcuts_trigger_commands(qtbot) -> None:  # type: ignore[no-untyped-def]
    widget = QWidget()
    qtbot.addWidget(widget)
    widget.show()
    widget.activateWindow()
    widget.setFocus()
    qtbot.waitExposed(widget)
    qtbot.wait(100)
    calls: list[str] = []
    registry = CommandRegistry(widget)
    registry.register(
        CommandSpec("terminal", "Terminal", "Ctrl+`", lambda: calls.append("terminal"))
    )
    registry.register(
        CommandSpec("explorer", "Explorer", "Ctrl+E", lambda: calls.append("explorer"))
    )

    QTest.keyClick(widget, Qt.Key.Key_QuoteLeft, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(widget, Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier)

    assert calls == ["terminal", "explorer"]
