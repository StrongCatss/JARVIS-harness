from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QWidget


@dataclass(slots=True)
class CommandSpec:
    identifier: str
    label: str
    shortcut: str
    callback: Callable[[], None]
    checkable: bool = False


class CommandRegistry:
    def __init__(self, window: QWidget) -> None:
        self.window = window
        self.actions: dict[str, QAction] = {}

    def register(self, spec: CommandSpec) -> QAction:
        action = QAction(spec.label, self.window)
        action.setObjectName(spec.identifier)
        action.setShortcut(QKeySequence(spec.shortcut))
        action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        action.setCheckable(spec.checkable)
        action.triggered.connect(lambda _checked=False: spec.callback())
        self.window.addAction(action)
        self.actions[spec.identifier] = action
        return action

    def trigger(self, identifier: str) -> None:
        action = self.actions.get(identifier)
        if action is not None:
            action.trigger()

    def set_checked(self, identifier: str, checked: bool) -> None:
        action = self.actions.get(identifier)
        if action is not None:
            action.blockSignals(True)
            action.setChecked(checked)
            action.blockSignals(False)
