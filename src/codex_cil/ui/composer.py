from __future__ import annotations

import os
import re
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


SKIP_DIRECTORIES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    "dist",
    "build",
}

TOOL_COMMANDS = [
    ("/status", "查看当前会话状态"),
    ("/model", "切换模型"),
    ("/permissions", "调整权限策略"),
    ("/mcp", "查看 MCP 服务器和工具"),
    ("/skills", "浏览可用 Skill"),
    ("/review", "审查当前工作区"),
    ("/compact", "压缩当前上下文"),
    ("/new", "新建当前类型会话"),
    ("/help", "查看可用命令"),
]

DEFAULT_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")
IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".webp",
    ".gif",
}


class ComposerEditor(QPlainTextEdit):
    sendRequested = Signal()

    def keyPressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.sendRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ComposerWidget(QFrame):
    messageSubmitted = Signal(str, list)
    commandRequested = Signal(str)
    stopRequested = Signal()
    inferenceSettingsChanged = Signal(object, object)
    contextModeChanged = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._project_root: Path | None = None
        self._project_mode = False
        self._attachments: list[str] = []
        self._file_cache: list[str] = []
        self._popup_start = -1
        self._reasoning_effort: str | None = None
        self._service_tier: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.popup = QListWidget()
        self.popup.setMaximumHeight(190)
        self.popup.setVisible(False)
        self.popup.itemActivated.connect(self._accept_popup_item)

        self.attachment_area = QScrollArea()
        self.attachment_area.setWidgetResizable(True)
        self.attachment_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.attachment_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.attachment_area.setFixedHeight(44)
        self.attachment_area.setVisible(False)
        self.attachment_host = QWidget()
        self.attachment_layout = QHBoxLayout(self.attachment_host)
        self.attachment_layout.setContentsMargins(0, 0, 0, 0)
        self.attachment_layout.setSpacing(5)
        self.attachment_layout.addStretch(1)
        self.attachment_area.setWidget(self.attachment_host)

        self.editor = ComposerEditor()
        self.editor.setObjectName("Composer")
        self.editor.setPlaceholderText("输入消息，Shift+Enter 换行")
        self.editor.setMinimumHeight(74)
        self.editor.setMaximumHeight(190)
        self.editor.textChanged.connect(self._text_changed)
        self.editor.sendRequested.connect(self._submit)
        self.editor.installEventFilter(self)

        footer = QHBoxLayout()
        footer.setSpacing(6)
        self.attach_button = QPushButton("@ 文件")
        self.attach_button.setToolTip("插入项目文件或目录")
        self.attach_button.clicked.connect(self._start_file_mention)
        self.image_button = QPushButton("图片")
        self.image_button.setToolTip("添加图片或截图到当前对话")
        self.image_button.clicked.connect(self._choose_images)
        self.command_button = QPushButton("/ 命令与工具")
        self.command_button.setToolTip("调用内置命令或工具")
        self.command_button.clicked.connect(self._start_command)
        self.mode_hint = QLabel("")
        self.mode_hint.setObjectName("Muted")
        self.context_combo = QComboBox()
        self.context_combo.setMinimumWidth(104)
        self.context_combo.setToolTip("发送给智能体的最近对话条数")
        for label, value in (
            ("上下文 10条", "10"),
            ("上下文 25条", "25"),
            ("上下文 50条", "50"),
            ("上下文 100条", "100"),
            ("上下文 全部", "all"),
            ("上下文 无", "none"),
        ):
            self.context_combo.addItem(label, value)
        self.context_combo.setCurrentIndex(
            self.context_combo.findData("all")
        )
        self.context_combo.currentIndexChanged.connect(
            lambda _index: self.contextModeChanged.emit(
                str(self.context_combo.currentData() or "all")
            )
        )
        self.permission_combo = QComboBox()
        self.permission_combo.setMinimumWidth(92)
        self.permission_combo.setToolTip("Agent 权限模式")
        self.permission_combo.addItem("只读", "read_only")
        self.permission_combo.addItem("计划", "plan")
        self.permission_combo.addItem("自动", "auto")
        self.permission_combo.addItem("完全访问", "full_access")
        self.permission_combo.setCurrentIndex(
            self.permission_combo.findData("auto")
        )
        self.permission_combo.setVisible(False)
        self.reasoning_combo = QComboBox()
        self.reasoning_combo.setMinimumWidth(86)
        self.reasoning_combo.setToolTip("推理等级")
        self.reasoning_combo.setVisible(False)
        self.speed_combo = QComboBox()
        self.speed_combo.setMinimumWidth(82)
        self.speed_combo.setToolTip("响应速度")
        self.speed_combo.setVisible(False)
        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("Primary")
        self.send_button.clicked.connect(self._submit)
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("Danger")
        self.stop_button.setToolTip("停止当前生成，快捷键 Esc")
        self.stop_button.clicked.connect(self.stopRequested)
        self.stop_button.setVisible(False)

        footer.addWidget(self.attach_button)
        footer.addWidget(self.image_button)
        footer.addWidget(self.command_button)
        footer.addWidget(self.mode_hint)
        footer.addStretch(1)
        footer.addWidget(self.context_combo)
        footer.addWidget(self.permission_combo)
        footer.addWidget(self.reasoning_combo)
        footer.addWidget(self.speed_combo)
        footer.addWidget(self.send_button)
        footer.addWidget(self.stop_button)

        self.reasoning_combo.currentIndexChanged.connect(self._inference_changed)
        self.speed_combo.currentIndexChanged.connect(self._inference_changed)

        layout.addWidget(self.popup)
        layout.addWidget(self.attachment_area)
        layout.addWidget(self.editor)
        layout.addLayout(footer)

    def set_mode(self, project_mode: bool, project_root: str | Path | None) -> None:
        self._project_mode = project_mode
        self._project_root = (
            Path(project_root).expanduser().resolve() if project_root else None
        )
        self.attach_button.setVisible(project_mode)
        self.command_button.setVisible(project_mode)
        self.permission_combo.setVisible(project_mode)
        self.mode_hint.setText(
            "Agent · @ 文件 · / 工具" if project_mode else "纯对话 · 无工具 · 无项目上下文"
        )
        self._file_cache = []
        if not project_mode:
            self._attachments.clear()
            self._render_attachments()
            self.popup.hide()

    def add_attachment(self, path: str) -> bool:
        candidate = Path(path).expanduser().resolve()
        if not candidate.exists():
            return False
        if not self._project_mode and not _is_image_path(candidate):
            return False
        if not self._project_mode and not candidate.is_file():
            return False
        resolved = str(candidate)
        if resolved not in self._attachments:
            self._attachments.append(resolved)
            self._render_attachments()
        return True

    @property
    def attachment_paths(self) -> list[str]:
        return list(self._attachments)

    def focus_editor(self) -> None:
        self.editor.setFocus()

    @property
    def reasoning_effort(self) -> str | None:
        return self._reasoning_effort

    @property
    def service_tier(self) -> str | None:
        return self._service_tier

    @property
    def permission_mode(self) -> str:
        return str(self.permission_combo.currentData() or "auto")

    @property
    def context_message_limit(self) -> int | None:
        value = str(self.context_combo.currentData() or "all")
        if value == "all":
            return None
        if value == "none":
            return 0
        return int(value)

    def set_context_mode(self, value: str) -> None:
        index = self.context_combo.findData(value)
        if index >= 0:
            self.context_combo.blockSignals(True)
            self.context_combo.setCurrentIndex(index)
            self.context_combo.blockSignals(False)

    def set_model_capabilities(
        self,
        efforts: list[dict],
        default_effort: str | None,
        service_tiers: list[dict],
        preferred_effort: str | None = None,
        default_service_tier: str | None = None,
    ) -> None:
        self.reasoning_combo.blockSignals(True)
        self.reasoning_combo.clear()
        supported_efforts = [
            str(item.get("reasoningEffort"))
            for item in efforts
            if item.get("reasoningEffort")
        ]
        for item in efforts:
            effort = item.get("reasoningEffort")
            if not effort:
                continue
            self.reasoning_combo.addItem(
                f"推理 {_effort_label(str(effort))}",
                str(effort),
            )
            index = self.reasoning_combo.count() - 1
            description = str(item.get("description") or "")
            if description:
                self.reasoning_combo.setItemData(
                    index,
                    description,
                    Qt.ItemDataRole.ToolTipRole,
                )
        selected_effort = (
            preferred_effort
            if preferred_effort in supported_efforts
            else default_effort
        )
        if selected_effort:
            selected_index = self.reasoning_combo.findData(selected_effort)
            if selected_index >= 0:
                self.reasoning_combo.setCurrentIndex(selected_index)
        self.reasoning_combo.setVisible(self.reasoning_combo.count() > 0)

        self.speed_combo.blockSignals(True)
        self.speed_combo.clear()
        if service_tiers:
            self.speed_combo.addItem("速度 标准", None)
            for tier in service_tiers:
                tier_id = tier.get("id")
                if not tier_id:
                    continue
                self.speed_combo.addItem(
                    f"速度 {tier.get('name') or tier_id}",
                    str(tier_id),
                )
                index = self.speed_combo.count() - 1
                description = str(tier.get("description") or "")
                if description:
                    self.speed_combo.setItemData(
                        index,
                        description,
                        Qt.ItemDataRole.ToolTipRole,
                    )
            if default_service_tier:
                index = self.speed_combo.findData(default_service_tier)
                if index >= 0:
                    self.speed_combo.setCurrentIndex(index)
        self.speed_combo.setVisible(self.speed_combo.count() > 0)
        self.reasoning_combo.blockSignals(False)
        self.speed_combo.blockSignals(False)
        self._inference_changed()

    def set_fallback_reasoning_efforts(
        self,
        preferred_effort: str | None,
    ) -> None:
        """模型目录无法匹配时仍提供通用的推理等级选择。"""

        efforts = [
            {
                "reasoningEffort": effort,
                "description": f"通用推理等级：{_effort_label(effort)}",
            }
            for effort in DEFAULT_REASONING_EFFORTS
        ]
        self.set_model_capabilities(
            efforts=efforts,
            default_effort=preferred_effort or "medium",
            service_tiers=[],
            preferred_effort=preferred_effort,
        )

    def set_busy(self, busy: bool) -> None:
        self.send_button.setVisible(not busy)
        self.stop_button.setVisible(busy)
        self.stop_button.setEnabled(busy)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[no-untyped-def]
        if watched is self.editor and event.type() == QEvent.Type.KeyPress:
            if self.popup.isVisible():
                if event.key() == Qt.Key.Key_Down:
                    self.popup.setCurrentRow(
                        min(self.popup.currentRow() + 1, self.popup.count() - 1)
                    )
                    return True
                if event.key() == Qt.Key.Key_Up:
                    self.popup.setCurrentRow(max(self.popup.currentRow() - 1, 0))
                    return True
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
                    self._accept_popup_item(self.popup.currentItem())
                    return True
                if event.key() == Qt.Key.Key_Escape:
                    self.popup.hide()
                    return True
        return super().eventFilter(watched, event)

    def dragEnterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.mimeData().hasUrls() and (
            self._project_mode
            or any(
                url.isLocalFile()
                and _is_image_path(Path(url.toLocalFile()))
                for url in event.mimeData().urls()
            )
        ):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        added = False
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.exists():
                self.add_attachment(str(path))
                added = True
        if added:
            event.acceptProposedAction()
            self.focus_editor()
            return
        super().dropEvent(event)

    def _submit(self) -> None:
        text = self.editor.toPlainText().strip()
        if not text:
            return
        if self.popup.isVisible():
            return
        attachments = list(self._attachments)
        self.messageSubmitted.emit(text, attachments)
        self.editor.clear()
        self._attachments.clear()
        self._render_attachments()
        self.popup.hide()

    def _text_changed(self) -> None:
        if not self._project_mode:
            self.popup.hide()
            return
        token = self._current_token()
        if token is None:
            self.popup.hide()
            return
        prefix, query, start = token
        self._popup_start = start
        if prefix == "@":
            self._show_files(query)
        else:
            self._show_commands(query)

    def _current_token(self) -> tuple[str, str, int] | None:
        cursor = self.editor.textCursor()
        position = cursor.position()
        prefix_text = self.editor.toPlainText()[:position]
        match = re.search(r"([@/])([^\s@/]*)$", prefix_text)
        if not match:
            return None
        return match.group(1), match.group(2), position - len(match.group(0))

    def _show_files(self, query: str) -> None:
        if not self._project_root:
            self.popup.hide()
            return
        if not self._file_cache:
            self._file_cache = _iter_project_paths(self._project_root)
        needle = query.lower()
        matches = [path for path in self._file_cache if needle in path.lower()][:80]
        self.popup.clear()
        for relative in matches:
            item = QListWidgetItem(relative)
            item.setData(Qt.ItemDataRole.UserRole, ("file", relative))
            self.popup.addItem(item)
        if self.popup.count():
            self.popup.setCurrentRow(0)
            self.popup.show()
        else:
            self.popup.hide()

    def _show_commands(self, query: str) -> None:
        needle = query.lower().lstrip("/")
        commands = [
            (command, description)
            for command, description in TOOL_COMMANDS
            if needle in command.lower() or needle in description.lower()
        ]
        self.popup.clear()
        for command, description in commands:
            item = QListWidgetItem(f"{command}    {description}")
            item.setData(Qt.ItemDataRole.UserRole, ("command", command))
            self.popup.addItem(item)
        if self.popup.count():
            self.popup.setCurrentRow(0)
            self.popup.show()
        else:
            self.popup.hide()

    def _accept_popup_item(self, item: QListWidgetItem | None) -> None:
        if item is None or self._popup_start < 0:
            return
        payload = item.data(Qt.ItemDataRole.UserRole)
        kind, value = payload
        cursor = self.editor.textCursor()
        cursor.setPosition(self._popup_start)
        cursor.setPosition(
            self.editor.textCursor().position(),
            cursor.MoveMode.KeepAnchor,
        )
        if kind == "file":
            cursor.insertText(f"@{value} ")
            self.add_attachment(str(self._project_root / value) if self._project_root else value)
        else:
            cursor.insertText(f"{value} ")
            self.commandRequested.emit(value.lstrip("/"))
        self.editor.setTextCursor(cursor)
        self.popup.hide()
        self.editor.setFocus()

    def _start_file_mention(self) -> None:
        if not self._project_mode:
            return
        self.editor.insertPlainText("@")
        self.editor.setFocus()

    def _choose_images(self) -> None:
        paths, _selected_filter = QFileDialog.getOpenFileNames(
            self,
            "添加图片到对话",
            "",
            "图片 (*.png *.jpg *.jpeg *.bmp *.webp *.gif);;所有文件 (*)",
        )
        added = False
        for path in paths:
            added = self.add_attachment(path) or added
        if added:
            self.focus_editor()

    def _start_command(self) -> None:
        if not self._project_mode:
            return
        self.editor.insertPlainText("/")
        self.editor.setFocus()

    def _inference_changed(self) -> None:
        self._reasoning_effort = self.reasoning_combo.currentData()
        self._service_tier = self.speed_combo.currentData()
        self.inferenceSettingsChanged.emit(
            self._reasoning_effort,
            self._service_tier,
        )

    def _render_attachments(self) -> None:
        while self.attachment_layout.count() > 1:
            item = self.attachment_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for path in self._attachments:
            label = Path(path).name
            button = QPushButton(f"{label} ×")
            button.setToolTip(path)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            if _is_image_path(Path(path)):
                reader = QImageReader(path)
                reader.setAutoTransform(True)
                source_size = reader.size()
                if source_size.isValid():
                    source_size.scale(
                        QSize(48, 32),
                        Qt.AspectRatioMode.KeepAspectRatio,
                    )
                    reader.setScaledSize(source_size)
                image = reader.read()
                if not image.isNull():
                    button.setIcon(QIcon(QPixmap.fromImage(image)))
                    button.setIconSize(QSize(32, 28))
            button.clicked.connect(lambda _checked=False, p=path: self._remove_attachment(p))
            self.attachment_layout.insertWidget(self.attachment_layout.count() - 1, button)
        self.attachment_area.setVisible(bool(self._attachments))

    def _remove_attachment(self, path: str) -> None:
        self._attachments = [item for item in self._attachments if item != path]
        self._render_attachments()


def _iter_project_paths(root: Path) -> list[str]:
    results: list[str] = []
    try:
        for current, dirs, files in os.walk(root):
            dirs[:] = sorted(
                directory for directory in dirs if directory not in SKIP_DIRECTORIES
            )
            current_path = Path(current)
            for directory in dirs:
                path = current_path / directory
                results.append(str(path.relative_to(root)).replace("\\", "/"))
            for filename in sorted(files):
                path = current_path / filename
                results.append(str(path.relative_to(root)).replace("\\", "/"))
                if len(results) >= 2000:
                    return results
    except OSError:
        pass
    return results


def _effort_label(effort: str) -> str:
    labels = {
        "minimal": "最低",
        "low": "低",
        "medium": "中",
        "high": "高",
        "xhigh": "超高",
        "max": "最大",
        "ultra": "Ultra",
    }
    return labels.get(effort, effort)


def _is_image_path(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_SUFFIXES
