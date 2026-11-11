from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)


class CommandPaletteDialog(QDialog):
    def __init__(
        self,
        actions: list[QAction],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.actions = actions
        self.setWindowTitle("命令面板")
        self.resize(640, 480)
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索命令…")
        layout.addWidget(self.search)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        self.search.textChanged.connect(self._filter)
        self.list.itemActivated.connect(self._activate)
        self._populate()

    def _populate(self) -> None:
        self.list.clear()
        for action in self.actions:
            shortcut = action.shortcut().toString()
            text = action.text() + (f"    {shortcut}" if shortcut else "")
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, action)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _filter(self, text: str) -> None:
        needle = text.strip().lower()
        for index in range(self.list.count()):
            item = self.list.item(index)
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def _activate(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        action = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
        if isinstance(action, QAction):
            action.trigger()
