from __future__ import annotations

import os
import re
import uuid
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from codex_cil import DISPLAY_NAME, __version__
from codex_cil.paths import (
    custom_themes_path,
    project_instructions_path,
    update_history_path,
)
from codex_cil.services.codex_config import CodexConfig
from codex_cil.services.agent_capabilities import (
    AgentCapabilitiesError,
    discover_skills,
    load_mcp_servers,
    save_mcp_server_states,
    save_skill_states,
)
from codex_cil.services.theme_store import (
    CustomThemeStore,
    THEME_COLOR_FIELDS,
    ThemeStoreError,
    validate_theme,
)
from codex_cil.services.tools_config import (
    ToolsConfigurationError,
    inspect_tools,
)
from codex_cil.ui.theme_system import THEMES, ThemeSpec
from codex_cil.ui.markdown_renderer import render_markdown
from codex_cil.ui.theme_system import current_theme


class ProjectDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("添加项目")
        self.resize(560, 190)
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("项目名称")
        form.addRow("名称", self.name)

        folder_row = QHBoxLayout()
        self.folder = QLineEdit()
        self.folder.setPlaceholderText("选择工作文件夹")
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._choose_folder)
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(browse)
        form.addRow("工作文件夹", folder_row)
        layout.addLayout(form)

        hint = QLabel("项目指令将写入工作文件夹中的 AGENTS.md。")
        hint.setObjectName("Muted")
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择项目工作文件夹")
        if folder:
            self.folder.setText(folder)
            if not self.name.text().strip():
                self.name.setText(Path(folder).name)

    def _accept(self) -> None:
        path = Path(self.folder.text().strip())
        if not path.exists() or not path.is_dir():
            QMessageBox.warning(self, "路径无效", "请选择一个存在的工作文件夹。")
            return
        self.accept()

    def values(self) -> tuple[str, str]:
        folder = Path(self.folder.text().strip()).expanduser().resolve()
        name = self.name.text().strip() or folder.name
        return name, str(folder)


class InstructionsDialog(QDialog):
    def __init__(self, project_name: str, project_root: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root)
        self.path = project_instructions_path(self.project_root)
        self.setWindowTitle(f"项目指令 · {project_name}")
        self.resize(860, 650)

        layout = QVBoxLayout(self)
        path_label = QLabel(str(self.path))
        path_label.setObjectName("Muted")
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(path_label)

        self.editor = QPlainTextEdit()
        self.editor.setObjectName("CodeEditor")
        self.editor.setPlaceholderText(
            "# 项目说明\n\n"
            "## 工作约定\n\n"
            "- 先阅读相关文件，再修改代码。\n"
            "- 修改后运行项目测试。\n"
        )
        if self.path.exists():
            self.editor.setPlainText(self.path.read_text(encoding="utf-8", errors="replace"))
        layout.addWidget(self.editor, 1)

        actions = QHBoxLayout()
        open_folder = QPushButton("打开项目文件夹")
        open_folder.clicked.connect(lambda: os.startfile(str(self.project_root)))  # type: ignore[attr-defined]
        actions.addWidget(open_folder)
        actions.addStretch(1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        actions.addWidget(buttons)
        layout.addLayout(actions)

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(self.editor.toPlainText(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self.accept()


class UpdateHistoryDialog(QDialog):
    def __init__(
        self,
        history_path: str | Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.history_path = (
            Path(history_path).expanduser().resolve()
            if history_path is not None
            else update_history_path()
        )
        self.setWindowTitle("更新记录")
        self.resize(900, 720)
        layout = QVBoxLayout(self)

        title = QLabel(f"{DISPLAY_NAME} 更新记录")
        title.setObjectName("Brand")
        layout.addWidget(title)

        path_label = QLabel(str(self.history_path))
        path_label.setObjectName("Muted")
        path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(path_label)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.setOpenLinks(True)
        layout.addWidget(self.browser, 1)

        actions = QHBoxLayout()
        open_file = QPushButton("打开 Markdown 文件")
        open_file.clicked.connect(self._open_markdown_file)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.accept)
        actions.addWidget(open_file)
        actions.addStretch(1)
        actions.addWidget(close_button)
        layout.addLayout(actions)

        self._load_history()

    def _load_history(self) -> None:
        theme = current_theme()
        self.browser.document().setDefaultStyleSheet(
            f"""
            body {{ color: {theme.text}; background: {theme.input_bg}; }}
            h1, h2, h3 {{ color: {theme.accent}; }}
            a {{ color: {theme.assistant_accent or theme.accent}; }}
            code {{
                color: {theme.warning};
                background: {theme.card};
                font-family: "Cascadia Code", "Consolas";
            }}
            blockquote {{ color: {theme.muted}; }}
            """
        )
        try:
            markdown_text = self.history_path.read_text(encoding="utf-8")
        except OSError as exc:
            self.browser.setPlainText(
                f"无法读取更新记录：{self.history_path}\n\n{exc}"
            )
            return
        self.browser.setHtml(render_markdown(markdown_text, theme))

    def _open_markdown_file(self) -> None:
        if not self.history_path.exists():
            QMessageBox.warning(
                self,
                "更新记录不存在",
                str(self.history_path),
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.history_path)))


class SettingsDialog(QDialog):
    def __init__(
        self,
        config: CodexConfig,
        codex_binary: str,
        context_window: int,
        skill_roots: list[str | Path] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.capabilities_changed = False
        self.setWindowTitle("设置")
        self.resize(780, 650)
        layout = QVBoxLayout(self)
        version_row = QHBoxLayout()
        version_row.addWidget(QLabel(f"{DISPLAY_NAME} {__version__}"))
        version_row.addStretch(1)
        self.update_history_button = QPushButton("更新记录")
        self.update_history_button.clicked.connect(self._open_update_history)
        version_row.addWidget(self.update_history_button)
        layout.addLayout(version_row)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        general = QWidget()
        general_layout = QVBoxLayout(general)
        form = QFormLayout()
        self.codex = QLineEdit(codex_binary)
        self.context_window = QLineEdit(str(context_window or 0))
        self.context_window.setValidator(QIntValidator(0, 100_000_000, self))
        form.addRow("核心可执行文件", self.codex)
        form.addRow("纯对话上下文窗口", self.context_window)
        self.config_path = QLineEdit(str(config.config_path))
        self.config_path.setReadOnly(True)
        form.addRow("核心配置", self.config_path)
        self.model = QLineEdit(config.model or "未配置")
        self.model.setReadOnly(True)
        form.addRow("默认模型", self.model)
        self.provider = QLineEdit(
            f"{config.provider.name} ({config.provider_id})"
        )
        self.provider.setReadOnly(True)
        form.addRow("模型 Provider", self.provider)
        self.base_url = QLineEdit(config.provider.base_url or "未配置")
        self.base_url.setReadOnly(True)
        form.addRow("Provider Base URL", self.base_url)
        general_layout.addLayout(form)

        key_state = (
            f"认证：{config.auth_description}"
            if config.provider.has_authentication
            else "认证：无需认证"
        )
        key_label = QLabel(key_state)
        key_label.setObjectName("Pill" if config.is_configured else "PillWarning")
        general_layout.addWidget(key_label)
        if config.load_error:
            error = QLabel(config.load_error)
            error.setWordWrap(True)
            error.setProperty("severity", "danger")
            general_layout.addWidget(error)

        open_config = QPushButton("打开 config.toml")
        open_config.clicked.connect(self._open_config)
        general_layout.addWidget(open_config)
        pricing = QLabel(
            "费用估算：DeepSeek V4.1 Flash 高峰价（输入 ¥2、缓存命中 ¥0.04、输出 ¥8 / 百万 Token）"
        )
        pricing.setWordWrap(True)
        pricing.setObjectName("Muted")
        general_layout.addWidget(pricing)
        general_layout.addStretch(1)
        self.tabs.addTab(general, "常规")

        mcp_tab = QWidget()
        mcp_layout = QVBoxLayout(mcp_tab)
        mcp_hint = QLabel(
            "逐项启用或禁用 ~/.codex/config.toml 中的 MCP Server。"
            "保存后会重启智能核心并重新加载配置。"
            "离线优先模式开启时，非国内 MCP 仍会被离线策略禁用。"
        )
        mcp_hint.setWordWrap(True)
        mcp_hint.setObjectName("Muted")
        mcp_layout.addWidget(mcp_hint)
        self.mcp_list = QListWidget()
        try:
            self.mcp_servers = load_mcp_servers(config.config_path)
        except AgentCapabilitiesError as exc:
            self.mcp_servers = []
            mcp_layout.addWidget(QLabel(str(exc)))
        for server in self.mcp_servers:
            item = QListWidgetItem(
                f"{server.name}  ·  {server.transport}\n{server.target}"
            )
            item.setFlags(
                item.flags() | Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(
                Qt.CheckState.Checked
                if server.enabled
                else Qt.CheckState.Unchecked
            )
            item.setData(Qt.ItemDataRole.UserRole, server.name)
            item.setToolTip(
                f"{server.name}\n传输：{server.transport}\n目标：{server.target}"
            )
            self.mcp_list.addItem(item)
        mcp_layout.addWidget(self.mcp_list, 1)
        if not self.mcp_servers:
            empty = QLabel("config.toml 中尚未配置 MCP Server。")
            empty.setObjectName("Muted")
            mcp_layout.addWidget(empty)
        self.tabs.addTab(mcp_tab, f"MCP ({len(self.mcp_servers)})")

        skills_tab = QWidget()
        skills_layout = QVBoxLayout(skills_tab)
        skills_hint = QLabel(
            "管理用户、系统、插件和 Tools 离线 Skill。"
            "取消勾选会写入 [[skills.config]]，不会删除技能文件。"
        )
        skills_hint.setWordWrap(True)
        skills_hint.setObjectName("Muted")
        skills_layout.addWidget(skills_hint)
        self.skill_search = QLineEdit()
        self.skill_search.setPlaceholderText("搜索 Skill 名称、说明或路径…")
        skills_layout.addWidget(self.skill_search)
        self.skill_list = QListWidget()
        roots = skill_roots or [
            config.codex_home / "skills",
            config.codex_home / "plugins" / "cache",
        ]
        self.skills = discover_skills(roots, config.config_path)
        for skill in self.skills:
            item = QListWidgetItem(
                f"[{skill.scope}] {skill.name}\n"
                f"{skill.description or '暂无说明'}"
            )
            item.setFlags(
                item.flags() | Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(
                Qt.CheckState.Checked
                if skill.enabled
                else Qt.CheckState.Unchecked
            )
            item.setData(Qt.ItemDataRole.UserRole, str(skill.path))
            item.setToolTip(str(skill.path))
            self.skill_list.addItem(item)
        self.skill_search.textChanged.connect(self._filter_skills)
        skills_layout.addWidget(self.skill_list, 1)
        if not self.skills:
            empty_skills = QLabel("没有发现可管理的 Skill。")
            empty_skills.setObjectName("Muted")
            skills_layout.addWidget(empty_skills)
        self.tabs.addTab(skills_tab, f"Skills ({len(self.skills)})")
        self._initial_mcp_states = {
            server.name: server.enabled for server in self.mcp_servers
        }
        self._initial_skill_states = {
            skill.path: skill.enabled for skill in self.skills
        }

        shortcuts_tab = QWidget()
        shortcuts_layout = QVBoxLayout(shortcuts_tab)
        shortcut_search = QLineEdit()
        shortcut_search.setPlaceholderText("搜索快捷键…")
        shortcuts_layout.addWidget(shortcut_search)
        shortcuts = QListWidget()
        shortcut_rows = [
            "Ctrl+`        打开/收起终端",
            "Ctrl+E        打开/收起资源管理器",
            "Ctrl+Shift+B  打开内置浏览器",
            "Ctrl+Shift+D  查看本轮文件变更",
            "Ctrl+Shift+Z  框选截图并加入对话",
            "Ctrl+Shift+1  截取显示器 1 并加入对话",
            "Ctrl+Shift+2  截取显示器 2 并加入对话",
            "Ctrl+Shift+P  打开全局命令面板",
            "Ctrl+B        打开/收起左侧栏",
            "Ctrl+,        打开设置",
            "Ctrl+Shift+,  配置离线 Tools 目录",
            "Ctrl+N        新建当前类型会话",
            "Ctrl+Shift+N  新建项目",
        ]
        shortcuts.addItems(shortcut_rows)
        shortcut_search.textChanged.connect(
            lambda text: [
                shortcuts.item(index).setHidden(
                    bool(text.strip())
                    and text.strip().lower()
                    not in shortcuts.item(index).text().lower()
                )
                for index in range(shortcuts.count())
            ]
        )
        shortcuts_layout.addWidget(shortcuts, 1)
        self.tabs.addTab(shortcuts_tab, "快捷键")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _open_config(self) -> None:
        path = Path(self.config_path.text())
        if not path.exists():
            QMessageBox.warning(self, "配置文件不存在", str(path))
            return
        os.startfile(str(path))  # type: ignore[attr-defined]

    def _open_update_history(self) -> None:
        UpdateHistoryDialog(parent=self).exec()

    def _filter_skills(self, text: str) -> None:
        needle = text.strip().lower()
        for index in range(self.skill_list.count()):
            item = self.skill_list.item(index)
            searchable = f"{item.text()} {item.toolTip()}".lower()
            item.setHidden(bool(needle) and needle not in searchable)

    def accept(self) -> None:
        mcp_states: dict[str, bool] = {}
        for index in range(self.mcp_list.count()):
            item = self.mcp_list.item(index)
            name = str(item.data(Qt.ItemDataRole.UserRole))
            enabled = item.checkState() == Qt.CheckState.Checked
            if enabled != self._initial_mcp_states.get(name, True):
                mcp_states[name] = enabled
        skill_states: dict[Path, bool] = {}
        for index in range(self.skill_list.count()):
            item = self.skill_list.item(index)
            path = Path(str(item.data(Qt.ItemDataRole.UserRole)))
            enabled = item.checkState() == Qt.CheckState.Checked
            if enabled != self._initial_skill_states.get(path, True):
                skill_states[path] = enabled
        try:
            if mcp_states:
                save_mcp_server_states(self.config.config_path, mcp_states)
            if skill_states:
                save_skill_states(self.config.config_path, skill_states)
        except AgentCapabilitiesError as exc:
            QMessageBox.critical(self, "保存 Agent 配置失败", str(exc))
            return
        self.capabilities_changed = bool(mcp_states or skill_states)
        super().accept()


class ThemeEditorDialog(QDialog):
    def __init__(
        self,
        themes: list[ThemeSpec],
        base_theme: ThemeSpec,
        store: CustomThemeStore | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("自定义配色")
        self.resize(900, 760)
        self.setMinimumSize(760, 640)
        self._themes = list(themes)
        self._base_theme = base_theme
        self._store = store or CustomThemeStore(custom_themes_path())
        self._editing_identifier = ""
        self._loading = False
        self.color_inputs: dict[str, QLineEdit] = {}
        self.color_buttons: dict[str, QPushButton] = {}
        self.saved_theme: ThemeSpec | None = None

        layout = QVBoxLayout(self)
        title = QLabel("自定义主题")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        top_form = QFormLayout()
        self.theme_combo = QComboBox()
        top_form.addRow("编辑配色", self.theme_combo)
        self.name = QLineEdit()
        self.name.setMaxLength(40)
        top_form.addRow("主题名称", self.name)
        self.description = QLineEdit()
        top_form.addRow("说明", self.description)
        self.is_light = QCheckBox("浅色主题（用于代码高亮和对比度判断）")
        top_form.addRow("", self.is_light)
        layout.addLayout(top_form)

        path_label = QLabel(f"配置文件：{self._store.path}")
        path_label.setObjectName("Muted")
        path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(path_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        color_host = QWidget()
        color_grid = QGridLayout(color_host)
        color_grid.setContentsMargins(2, 2, 2, 2)
        color_grid.setHorizontalSpacing(10)
        color_grid.setVerticalSpacing(7)
        for row, (field_name, label) in enumerate(THEME_COLOR_FIELDS):
            color_grid.addWidget(QLabel(label), row, 0)
            editor = QLineEdit()
            editor.setMaxLength(7)
            editor.setPlaceholderText("#RRGGBB")
            editor.textChanged.connect(
                lambda _text, name=field_name: self._update_swatch(name)
            )
            self.color_inputs[field_name] = editor
            color_grid.addWidget(editor, row, 1)

            button = QPushButton()
            button.setMinimumWidth(116)
            button.clicked.connect(
                lambda _checked=False, name=field_name: self._choose_color(name)
            )
            self.color_buttons[field_name] = button
            color_grid.addWidget(button, row, 2)
        color_grid.setColumnStretch(1, 1)
        scroll.setWidget(color_host)
        layout.addWidget(scroll, 1)

        self.preview = QLabel()
        self.preview.setTextFormat(Qt.TextFormat.RichText)
        self.preview.setWordWrap(True)
        self.preview.setMinimumHeight(90)
        layout.addWidget(self.preview)

        self.status_label = QLabel("新建配色会基于当前主题。")
        self.status_label.setObjectName("Muted")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        actions = QHBoxLayout()
        self.import_button = QPushButton("导入…")
        self.import_button.clicked.connect(self._import_theme)
        self.export_button = QPushButton("导出…")
        self.export_button.clicked.connect(self._export_theme)
        self.delete_button = QPushButton("删除")
        self.delete_button.setObjectName("Danger")
        self.delete_button.clicked.connect(self._delete_theme)
        actions.addWidget(self.import_button)
        actions.addWidget(self.export_button)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)
        self.save_button = QPushButton("保存并应用")
        self.save_button.setObjectName("Primary")
        self.save_button.clicked.connect(self._save_theme)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(self.save_button)
        actions.addWidget(cancel)
        layout.addLayout(actions)

        self.theme_combo.currentIndexChanged.connect(self._theme_selected)
        self._refresh_theme_combo()
        self._select_initial_theme()

    def _refresh_theme_combo(self, selected: str | None = None) -> None:
        self.theme_combo.blockSignals(True)
        self.theme_combo.clear()
        self.theme_combo.addItem("新建自定义配色", None)
        for theme in self._themes:
            self.theme_combo.addItem(theme.name, theme.identifier)
        if selected:
            index = self.theme_combo.findData(selected)
            self.theme_combo.setCurrentIndex(index if index >= 0 else 0)
        self.theme_combo.blockSignals(False)

    def _select_initial_theme(self) -> None:
        matching = next(
            (
                theme
                for theme in self._themes
                if theme.identifier == self._base_theme.identifier
            ),
            None,
        )
        if matching:
            index = self.theme_combo.findData(matching.identifier)
            self.theme_combo.setCurrentIndex(index)
            self._load_theme(matching, matching.identifier)
        else:
            self.theme_combo.setCurrentIndex(0)
            self._load_theme(self._base_theme, "")

    def _theme_selected(self, index: int) -> None:
        identifier = self.theme_combo.itemData(index)
        if not identifier:
            self._load_theme(self._base_theme, "")
            return
        theme = next(
            (item for item in self._themes if item.identifier == identifier),
            None,
        )
        if theme:
            self._load_theme(theme, theme.identifier)

    def _load_theme(self, theme: ThemeSpec, identifier: str) -> None:
        self._loading = True
        self._editing_identifier = identifier
        self.name.setText(
            theme.name if identifier else f"{theme.name} 副本"
        )
        self.description.setText(theme.description)
        self.is_light.setChecked(theme.is_light)
        for field_name, _label in THEME_COLOR_FIELDS:
            value = getattr(theme, field_name)
            if field_name == "assistant_accent" and not value:
                value = theme.warning
            self.color_inputs[field_name].setText(str(value).upper())
            self._update_swatch(field_name)
        self.delete_button.setEnabled(bool(identifier))
        self._loading = False
        self._update_preview()

    def _choose_color(self, field_name: str) -> None:
        initial = QColor(self.color_inputs[field_name].text().strip())
        selected = QColorDialog.getColor(
            initial if initial.isValid() else QColor("#ffffff"),
            self,
            "选择颜色",
        )
        if selected.isValid():
            self.color_inputs[field_name].setText(selected.name().upper())

    def _update_swatch(self, field_name: str) -> None:
        value = self.color_inputs[field_name].text().strip()
        color = QColor(value)
        button = self.color_buttons[field_name]
        if not color.isValid():
            button.setText("颜色无效")
            button.setStyleSheet("")
            return
        brightness = (
            color.red() * 299 + color.green() * 587 + color.blue() * 114
        ) / 1000
        foreground = "#000000" if brightness > 150 else "#ffffff"
        button.setText(color.name().upper())
        button.setStyleSheet(
            f"background:{color.name()};color:{foreground};"
            f"border:1px solid {color.name()};"
        )
        if not self._loading:
            self._update_preview()

    def _update_preview(self) -> None:
        values = {
            field_name: self.color_inputs[field_name].text().strip() or "#000000"
            for field_name, _label in THEME_COLOR_FIELDS
        }
        self.preview.setText(
            f"""
            <div style="background:{values['bg']};padding:7px;">
              <div style="background:{values['accent']};color:{values['accent_text']};
                          padding:4px 8px;font-weight:700;">你</div>
              <div style="background:{values['accent_soft']};color:{values['accent']};
                          padding:7px 8px;">用户消息预览</div>
              <div style="background:{values['input_bg']};color:{values['assistant_accent']};
                          padding:4px 8px;font-weight:700;">{DISPLAY_NAME}</div>
              <div style="background:{values['card']};color:{values['text']};
                          padding:7px 8px;">智能体消息预览</div>
            </div>
            """
        )

    def _draft_theme(self) -> ThemeSpec:
        colors = {
            field_name: self.color_inputs[field_name].text().strip().upper()
            for field_name, _label in THEME_COLOR_FIELDS
        }
        return ThemeSpec(
            identifier=self._editing_identifier or self._new_identifier(),
            name=self.name.text().strip(),
            description=self.description.text().strip() or "用户自定义配色",
            is_light=self.is_light.isChecked(),
            **colors,
        )

    def _save_theme(self) -> None:
        try:
            theme = self._draft_theme()
            validate_theme(theme)
            existing = next(
                (
                    index
                    for index, item in enumerate(self._themes)
                    if item.identifier == theme.identifier
                ),
                None,
            )
            if existing is None:
                self._themes.append(theme)
            else:
                self._themes[existing] = theme
            self._store.save(self._themes)
        except ThemeStoreError as exc:
            QMessageBox.warning(self, "无法保存配色", str(exc))
            return
        self.saved_theme = theme
        self.accept()

    def _delete_theme(self) -> None:
        identifier = self._editing_identifier
        if not identifier:
            return
        answer = QMessageBox.question(
            self,
            "删除自定义配色",
            f"确定删除“{self.name.text().strip()}”吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._themes = [
            theme for theme in self._themes if theme.identifier != identifier
        ]
        try:
            self._store.save(self._themes)
        except ThemeStoreError as exc:
            QMessageBox.critical(self, "删除失败", str(exc))
            return
        self._refresh_theme_combo()
        self.theme_combo.setCurrentIndex(0)
        self._load_theme(self._base_theme, "")
        self.status_label.setText("配色已删除。")

    def _export_theme(self) -> None:
        try:
            draft = self._draft_theme()
            validate_theme(draft)
        except ThemeStoreError as exc:
            QMessageBox.warning(self, "无法导出", str(exc))
            return
        safe_name = re.sub(r'[<>:"/\\|?*]+', "_", draft.name or "theme")
        destination, _filter = QFileDialog.getSaveFileName(
            self,
            "导出自定义配色",
            str(self._store.path.parent / f"{safe_name}.jarvis-theme.json"),
            "JARVIS 主题 (*.json)",
        )
        if not destination:
            return
        try:
            self._store.export_theme(draft, destination)
        except ThemeStoreError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self.status_label.setText(f"已导出：{destination}")

    def _import_theme(self) -> None:
        source, _filter = QFileDialog.getOpenFileName(
            self,
            "导入自定义配色",
            str(self._store.path.parent),
            "JARVIS 主题 (*.json)",
        )
        if not source:
            return
        try:
            imported = self._store.import_themes(source)
        except ThemeStoreError as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        existing_ids = {theme.identifier for theme in self._themes} | set(THEMES)
        last_identifier = ""
        for theme in imported:
            identifier = theme.identifier
            if identifier in existing_ids:
                identifier = self._new_identifier()
                theme = replace(theme, identifier=identifier)
            existing_ids.add(identifier)
            self._themes.append(theme)
            last_identifier = identifier
        self._refresh_theme_combo(last_identifier)
        self._load_theme(self._themes[-1], self._themes[-1].identifier)
        self.status_label.setText(
            f"已导入 {len(imported)} 个配色，点击“保存并应用”后写入配置。"
        )

    @staticmethod
    def _new_identifier() -> str:
        return f"custom_{uuid.uuid4().hex[:12]}"


class ToolsConfigDialog(QDialog):
    def __init__(
        self,
        tools_root: str,
        config_path: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config_path = Path(config_path)
        self.setWindowTitle("数据配置")
        self.resize(760, 480)

        layout = QVBoxLayout(self)
        title = QLabel("Agent 离线工具目录")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        hint = QLabel(
            "选择 Tools 根目录。应用后会校验离线文档、技能和国内来源 MCP，"
            "并同步到当前 Codex 配置。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        layout.addWidget(hint)

        path_row = QHBoxLayout()
        self.tools_path = QLineEdit(tools_root)
        self.tools_path.setPlaceholderText("选择 Tools 文件夹")
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._choose_folder)
        validate = QPushButton("检查")
        validate.clicked.connect(lambda: self._refresh_details(True))
        path_row.addWidget(self.tools_path, 1)
        path_row.addWidget(browse)
        path_row.addWidget(validate)
        layout.addLayout(path_row)

        form = QFormLayout()
        self.config_display = QLineEdit(str(self.config_path))
        self.config_display.setReadOnly(True)
        form.addRow("Codex 配置", self.config_display)
        layout.addLayout(form)

        self.status_label = QLabel()
        self.status_label.setObjectName("Pill")
        layout.addWidget(self.status_label)
        self.mcp_enabled = QCheckBox("启用国内来源 MCP")
        self.mcp_enabled.setChecked(True)
        layout.addWidget(self.mcp_enabled)

        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(210)
        layout.addWidget(self.details, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh_details(False)

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择 Tools 离线资料目录")
        if folder:
            self.tools_path.setText(folder)
            self._refresh_details(False)

    def _refresh_details(self, show_errors: bool) -> bool:
        try:
            tools = inspect_tools(self.tools_path.text().strip())
        except (OSError, ToolsConfigurationError) as exc:
            self.status_label.setText("资料不完整")
            self.status_label.setObjectName("PillWarning")
            self.status_label.style().unpolish(self.status_label)
            self.status_label.style().polish(self.status_label)
            self.details.setPlainText(str(exc))
            if show_errors:
                QMessageBox.warning(self, "无法应用", str(exc))
            return False

        self.status_label.setText("检查通过")
        self.status_label.setObjectName("Pill")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        skills = sorted(
            path.parent.name
            for path in tools.skills_root.glob("*/SKILL.md")
            if path.is_file()
        )
        self.details.setPlainText(
            "\n".join(
                [
                    f"Tools 根目录：{tools.root}",
                    f"离线 OpenAI 文档：{tools.docs_root}",
                    f"技能根目录：{tools.skills_root}",
                    f"openai-docs：{tools.openai_skill.parent}",
                    f"国内来源优先：{tools.china_skill.parent}",
                    f"国内来源 MCP：{tools.mcp_executable}",
                    f"可用 Skills：{', '.join(skills) if skills else '无'}",
                    f"资料清单：{tools.catalog}",
                    f"OpenAI 技能：{tools.openai_skill}",
                    f"国内来源技能：{tools.china_skill}",
                    "",
                    "应用时将只更新 mcp_servers.china-sources，",
                    "模型、Provider、API Key 和其他 MCP 配置保持不变。",
                ]
            )
        )
        return True

    def _accept(self) -> None:
        if self._refresh_details(True):
            self.accept()

    def values(self) -> str:
        return str(inspect_tools(self.tools_path.text().strip()).root)
