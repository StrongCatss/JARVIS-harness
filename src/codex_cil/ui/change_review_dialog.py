from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from codex_cil.services.change_tracker import TurnChangeTracker
from codex_cil.ui.theme_system import current_theme


class ChangeReviewDialog(QDialog):
    def __init__(
        self,
        tracker: TurnChangeTracker,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.tracker = tracker
        self.setWindowTitle("本轮文件变更")
        self.resize(1080, 720)
        layout = QVBoxLayout(self)
        summary = QLabel(
            f"本轮修改 {tracker.modified_count} 个文件。"
            "取消勾选的文件不会被恢复。"
        )
        summary.setObjectName("Muted")
        layout.addWidget(summary)

        splitter = QSplitter()
        self.files = QListWidget()
        for change in tracker.files:
            item = QListWidgetItem(f"{change.kind}: {change.path}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, change.path)
            self.files.addItem(item)
        self.files.currentItemChanged.connect(self._show_selected)
        splitter.addWidget(self.files)

        self.diff = QTextBrowser()
        splitter.addWidget(self.diff)
        splitter.setSizes([280, 780])
        layout.addWidget(splitter, 1)

        actions = QHBoxLayout()
        open_file = QPushButton("打开文件")
        open_file.clicked.connect(self._open_file)
        restore_selected = QPushButton("恢复勾选文件")
        restore_selected.clicked.connect(self._restore_selected)
        restore_all = QPushButton("恢复本轮全部")
        restore_all.setObjectName("Danger")
        restore_all.clicked.connect(self._restore_all)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        actions.addWidget(open_file)
        actions.addStretch(1)
        actions.addWidget(restore_selected)
        actions.addWidget(restore_all)
        actions.addWidget(close)
        layout.addLayout(actions)
        if self.files.count():
            self.files.setCurrentRow(0)

    def _show_selected(self, current: QListWidgetItem | None, _previous=None) -> None:
        if current is None:
            self.diff.clear()
            return
        path = str(current.data(Qt.ItemDataRole.UserRole) or "")
        change = next(
            (item for item in self.tracker.files if item.path == path),
            None,
        )
        text = change.diff if change and change.diff else self.tracker.aggregate_diff
        self.diff.setHtml(_diff_html(text))

    def _checked_paths(self) -> list[str]:
        paths: list[str] = []
        for index in range(self.files.count()):
            item = self.files.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                paths.append(str(item.data(Qt.ItemDataRole.UserRole) or ""))
        return paths

    def _restore_selected(self) -> None:
        self.tracker.restore(self._checked_paths())
        self.accept()

    def _restore_all(self) -> None:
        self.tracker.restore()
        self.accept()

    def _open_file(self) -> None:
        current = self.files.currentItem()
        if current is None:
            return
        relative = str(current.data(Qt.ItemDataRole.UserRole) or "")
        target = self.tracker.project_root / Path(relative)
        if target.exists():
            import os

            os.startfile(str(target))  # type: ignore[attr-defined]


def _diff_html(diff: str) -> str:
    theme = current_theme()
    lines: list[str] = []
    for line in diff.splitlines() or ["没有可显示的差异。"]:
        escaped = html.escape(line)
        if line.startswith("+") and not line.startswith("+++"):
            color = theme.accent
        elif line.startswith("-") and not line.startswith("---"):
            color = theme.danger
        elif line.startswith("@@"):
            color = theme.warning
        else:
            color = theme.text
        lines.append(f'<span style="color:{color};">{escaped}</span>')
    return (
        f'<pre style="white-space:pre-wrap;color:{theme.text};'
        f'font-family:\'Cascadia Code\',\'Consolas\';">'
        + "\n".join(lines)
        + "</pre>"
    )
