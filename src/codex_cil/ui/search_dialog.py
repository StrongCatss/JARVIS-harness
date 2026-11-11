from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)


class ConversationSearchDialog(QDialog):
    sessionSelected = Signal(str, int)

    def __init__(
        self,
        query: str,
        results: list[dict[str, Any]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"会话全文搜索 · {query}")
        self.resize(780, 520)
        layout = QVBoxLayout(self)
        self.summary = QLabel(f"找到 {len(results)} 条匹配消息")
        self.summary.setObjectName("Muted")
        layout.addWidget(self.summary)
        self.results = QListWidget()
        for result in results:
            content = " ".join(str(result.get("content") or "").split())
            preview = content[:160] + ("…" if len(content) > 160 else "")
            item = QListWidgetItem(
                f"{result.get('title')} · {result.get('role')}\n{preview}"
            )
            item.setData(
                Qt.ItemDataRole.UserRole,
                (str(result.get("kind")), int(result.get("session_id"))),
            )
            item.setToolTip(content[:1000])
            self.results.addItem(item)
        self.results.itemActivated.connect(self._activate)
        layout.addWidget(self.results, 1)
        if self.results.count():
            self.results.setCurrentRow(0)

    def _activate(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        kind, session_id = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
        self.sessionSelected.emit(str(kind), int(session_id))
