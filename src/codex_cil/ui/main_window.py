from __future__ import annotations

import base64
import mimetypes
import os
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QTimer, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDockWidget,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from codex_cil import DISPLAY_NAME, __version__
from codex_cil.database import Database
from codex_cil.models import Project, Session, SessionKind, UsageStats
from codex_cil.paths import (
    app_data_dir,
    custom_themes_path,
    project_instructions_path,
    tools_dir,
)
from codex_cil.services.codex_gateway import CodexGateway
from codex_cil.services.change_tracker import TurnChangeTracker
from codex_cil.services.codex_config import CodexConfig
from codex_cil.services.offline_docs import OfflineDocsIndex
from codex_cil.services.offline_policy import offline_policy_text
from codex_cil.services.pure_chat import PureChatService
from codex_cil.services.theme_store import CustomThemeStore, ThemeStoreError
from codex_cil.services.tools_config import (
    ToolsConfigurationError,
    activate_tools_environment,
    configure_codex_mcp,
    inspect_tools,
)
from codex_cil.ui.browser_panel import BrowserWorkspace
from codex_cil.ui.chat_panel import ChatEntry, ChatPanel
from codex_cil.ui.change_review_dialog import ChangeReviewDialog
from codex_cil.ui.commands import CommandRegistry, CommandSpec
from codex_cil.ui.command_palette import CommandPaletteDialog
from codex_cil.ui.dialogs import (
    InstructionsDialog,
    ProjectDialog,
    SettingsDialog,
    ThemeEditorDialog,
    ToolsConfigDialog,
)
from codex_cil.ui.explorer_panel import ExplorerPanel
from codex_cil.ui.inspector_panel import InspectorPanel
from codex_cil.ui.nav_panel import NavPanel
from codex_cil.ui.screenshot import (
    ScreenshotOverlay,
    capture_screen,
    capture_virtual_desktop,
    save_screenshot,
    virtual_screen_geometry,
)
from codex_cil.ui.search_dialog import ConversationSearchDialog
from codex_cil.ui.server_request_dialog import ServerRequestDialog
from codex_cil.ui.terminal_panel import TerminalPanel
from codex_cil.ui.theme_system import THEMES, apply_theme, current_theme, theme_manager

_CHAT_HISTORY_PAGE_SIZE = 200
_PURE_CHAT_DEFAULT_CONTEXT_TOKENS = 32_000
_PURE_CHAT_CONTEXT_RATIO = 0.75
_PURE_CHAT_CHARS_PER_TOKEN = 2
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}


def _is_image_path(path: Path) -> bool:
    return path.suffix.lower() in _IMAGE_SUFFIXES and path.is_file()


def _image_data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _approval_policy_for_mode(permission_mode: str) -> str:
    return (
        "never"
        if permission_mode in {"auto", "full_access"}
        else "on-request"
    )


def _build_pure_chat_payload(
    messages: list[Message],
    context_window: int | None,
    *,
    extra_context_chars: int = 0,
) -> tuple[list[dict[str, str]], int]:
    history = [
        message
        for message in messages
        if message.role in {"user", "assistant"} and message.content.strip()
    ]
    window = context_window or _PURE_CHAT_DEFAULT_CONTEXT_TOKENS
    budget_tokens = max(2_000, int(window * _PURE_CHAT_CONTEXT_RATIO))
    budget_chars = max(
        4_000,
        budget_tokens * _PURE_CHAT_CHARS_PER_TOKEN - extra_context_chars,
    )
    selected: list[dict[str, str]] = []
    used_tokens = 0
    for message in reversed(history):
        content = message.content
        estimated = max(1, (len(content) + 1) // 2)
        if selected and used_tokens + estimated > budget_tokens:
            break
        if not selected and len(content) > budget_chars:
            content = "【较早内容已裁剪】\n" + content[-budget_chars:]
        selected.append({"role": message.role, "content": content})
        used_tokens += max(1, (len(content) + 1) // 2)
    selected.reverse()
    return selected, max(len(history) - len(selected), 0)


class MainWindow(QMainWindow):
    def __init__(self, database: Database) -> None:
        super().__init__()
        self.db = database
        self.current_project: Project | None = None
        self.current_session: Session | None = None
        self.current_kind = SessionKind.PURE_CHAT
        self._busy = False
        self._streaming_text = ""
        self._streaming_started = False
        self._current_turn_id: str | None = None
        self._browser_workspace: BrowserWorkspace | None = None
        self._oldest_loaded_message_id: int | None = None
        self._has_earlier_messages = False
        self._turn_change_tracker: TurnChangeTracker | None = None
        self._turn_started_at: datetime | None = None
        self._item_started_at: dict[str, datetime] = {}
        self._command_outputs: dict[str, str] = {}
        self._file_paths_by_item: dict[str, list[str]] = {}
        self._active_thread_id: str | None = None
        self._project_approval_cache: set[str] = set()
        self._approval_cache_project_id: int | None = None
        self._screenshot_overlay: ScreenshotOverlay | None = None
        self._draft_timer = QTimer(self)
        self._draft_timer.setSingleShot(True)
        self._draft_timer.setInterval(1000)
        self._draft_timer.timeout.connect(self._save_stream_draft)
        self.theme_store = CustomThemeStore(custom_themes_path())
        self._theme_store_error = self._load_custom_themes()
        self._backup_database_if_needed()
        self.offline_mode = self.db.get_setting("offline_mode", "0") == "1"
        self.tools_layout = None
        self._tools_warning = ""
        configured_tools = self.db.get_setting("tools_dir", "").strip()
        for candidate in (configured_tools, str(tools_dir())):
            if not candidate:
                continue
            try:
                self.tools_layout = inspect_tools(candidate)
            except (OSError, ToolsConfigurationError) as exc:
                if candidate == configured_tools:
                    self._tools_warning = str(exc)
                continue
            activate_tools_environment(self.tools_layout)
            break
        self.tools_root = (
            self.tools_layout.root
            if self.tools_layout
            else Path(os.getenv("CODEX_CIL_TOOLS_DIR") or tools_dir())
        )
        self.offline_docs = OfflineDocsIndex(
            self.tools_layout.docs_root if self.tools_layout else None
        )

        self.setWindowTitle(DISPLAY_NAME)
        self.resize(1480, 930)
        self.setMinimumSize(1120, 720)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            self._application = app
            app.installEventFilter(self)
            saved_theme = self.db.get_setting("app_theme", "hacker") or "hacker"
            if saved_theme not in THEMES:
                saved_theme = "hacker"
                self.db.set_setting("app_theme", saved_theme)
            apply_theme(app, saved_theme)

        self.codex_config = CodexConfig.load()
        self.pure_chat = PureChatService(self.codex_config, self)
        self.gateway = CodexGateway(self)
        if self.tools_layout:
            self.gateway.configure_skill_roots((self.tools_layout.skills_root,))
        self.gateway.connect_started_hook()
        self.gateway.statusChanged.connect(self._gateway_status)
        self.gateway.notification.connect(self._codex_notification)
        self.gateway.serverRequest.connect(self._server_request)
        self.gateway.protocolInfo.connect(self._protocol_info)
        self.gateway.requestFailed.connect(self._gateway_error)
        self.gateway.initialized.connect(self._load_model_catalog)
        self.pure_chat.responseStarted.connect(
            self._pure_chat_response_started
        )
        self.pure_chat.reasoningStarted.connect(
            self._pure_chat_reasoning_started
        )
        self.pure_chat.reasoningSummaryDelta.connect(
            self._pure_chat_reasoning_summary_delta
        )
        self.pure_chat.reasoningTextDelta.connect(
            self._pure_chat_reasoning_text_delta
        )
        self.pure_chat.textDelta.connect(self._pure_chat_text_delta)
        self.pure_chat.responseReady.connect(self._pure_chat_response)
        self.pure_chat.requestCancelled.connect(self._pure_chat_cancelled)
        self.pure_chat.requestFailed.connect(self._pure_chat_error)

        self._build_ui()
        self.chat.composer.set_context_mode(
            self.db.get_setting("context_mode", "all") or "all"
        )
        self.chat.composer.set_fallback_reasoning_efforts(
            self.codex_config.reasoning_effort
        )
        self._build_commands()
        self._build_menu()
        self.db.normalize_default_session_titles()
        self._refresh_navigation()
        self._configure_offline_gateway()
        self.gateway.start()
        self._restore_or_create_initial_session()
        if self._theme_store_error:
            self.statusBar().showMessage(self._theme_store_error, 12000)
        if self._tools_warning:
            self.statusBar().showMessage(
                "已保存的 Tools 路径不可用，请打开“数据配置”重新选择。",
                12000,
            )

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("AppRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.top_bar = self._create_top_bar()
        root_layout.addWidget(self.top_bar)

        self.body_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.nav = NavPanel()
        self.explorer = ExplorerPanel()
        self.explorer.setVisible(False)

        self.work_tabs = QTabWidget()
        self.work_tabs.setTabsClosable(False)
        self.work_tabs.setDocumentMode(True)
        self.work_tabs.tabBar().setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.work_tabs.tabBar().customContextMenuRequested.connect(
            self._work_tab_context_menu
        )
        self.chat = ChatPanel()
        self.work_tabs.addTab(self.chat, "对话")

        self.inspector = InspectorPanel()
        self.inspector.setVisible(False)

        self.body_splitter.addWidget(self.nav)
        self.body_splitter.addWidget(self.explorer)
        self.body_splitter.addWidget(self.work_tabs)
        self.body_splitter.addWidget(self.inspector)
        self.body_splitter.setStretchFactor(0, 0)
        self.body_splitter.setStretchFactor(1, 0)
        self.body_splitter.setStretchFactor(2, 1)
        self.body_splitter.setStretchFactor(3, 0)
        self.body_splitter.setSizes([250, 280, 880, 280])
        root_layout.addWidget(self.body_splitter, 1)
        self.setCentralWidget(root)

        self.terminal = TerminalPanel()
        self.terminal_dock = QDockWidget("PowerShell", self)
        self.terminal_dock.setObjectName("TerminalDock")
        self.terminal_dock.setWidget(self.terminal)
        self.terminal_dock.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.terminal_dock)
        self.terminal_dock.hide()

        self.nav.addProjectRequested.connect(self._add_project)
        self.nav.addProjectSessionRequested.connect(self._new_project_session)
        self.nav.addPureChatRequested.connect(self._new_pure_chat)
        self.nav.projectActivated.connect(self._project_selected)
        self.nav.projectSessionActivated.connect(self._project_session_selected)
        self.nav.pureChatActivated.connect(self._pure_chat_selected)
        self.nav.editInstructionsRequested.connect(self._edit_project_instructions)
        self.nav.openProjectFolderRequested.connect(self._open_project_folder)
        self.nav.deleteProjectRequested.connect(self._delete_project)
        self.nav.deleteProjectSessionRequested.connect(self._delete_project_session)
        self.nav.deletePureChatRequested.connect(self._delete_pure_chat)
        self.nav.renameSessionRequested.connect(self._rename_session)
        self.nav.forkProjectSessionRequested.connect(self._fork_project_session)
        self.nav.exportSessionRequested.connect(self._export_session)
        self.nav.setPinnedRequested.connect(self._set_session_pinned)
        self.nav.setArchivedRequested.connect(self._set_session_archived)
        self.nav.archivedVisibilityChanged.connect(
            lambda _visible: self._refresh_navigation()
        )
        self.nav.conversationSearchRequested.connect(
            self._search_conversations
        )
        self.explorer.fileActivated.connect(self._open_file_external)
        self.explorer.addFileToChatRequested.connect(self._add_file_to_chat)
        self.chat.messageSubmitted.connect(self._send_message)
        self.chat.commandRequested.connect(self._run_slash_command)
        self.chat.stopRequested.connect(self._cancel_current_turn)
        self.chat.detailsRequested.connect(self._show_usage_details)
        self.chat.earlierRequested.connect(self._load_earlier_messages)
        self.chat.composer.contextModeChanged.connect(
            lambda value: self.db.set_setting("context_mode", value)
        )
        self.inspector.cancelTurnRequested.connect(self._cancel_current_turn)
        self.inspector.steerRequested.connect(self._steer_current_turn)
        self.statusBar().showMessage("就绪")
        self.version_label = QLabel()
        self.version_label.setObjectName("VersionLabel")
        self.version_label.setTextFormat(Qt.TextFormat.RichText)
        self.version_label.setToolTip(f"{DISPLAY_NAME} 软件版本与个人智能管家说明")
        self.statusBar().addPermanentWidget(self.version_label)
        self._update_version_label()
        theme_manager.changed.connect(self._theme_changed)

    def _create_top_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("TopBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 7, 12, 7)
        layout.setSpacing(8)

        brand = QLabel(DISPLAY_NAME)
        brand.setObjectName("Brand")
        layout.addWidget(brand)

        self.mode_label = QLabel("纯对话")
        self.mode_label.setObjectName("PillWarning")
        layout.addWidget(self.mode_label)

        self.location_label = QLabel("未选择项目")
        self.location_label.setObjectName("Muted")
        layout.addWidget(self.location_label)
        layout.addStretch(1)

        self.gateway_label = QLabel(f"{DISPLAY_NAME} 启动中…")
        self.gateway_label.setObjectName("Muted")
        layout.addWidget(self.gateway_label)

        self.new_session_button = QPushButton("新会话")
        self.new_session_button.clicked.connect(self._new_current_session)
        self.instructions_button = QPushButton("项目指令")
        self.instructions_button.clicked.connect(self._edit_current_project_instructions)
        self.changes_button = QPushButton("本轮变更")
        self.changes_button.setEnabled(False)
        self.changes_button.clicked.connect(self._show_turn_changes)
        self.explorer_button = QPushButton("资源")
        self.explorer_button.clicked.connect(self._toggle_explorer)
        self.browser_button = QPushButton("浏览器")
        self.browser_button.clicked.connect(self._open_browser)
        self.inspector_button = QPushButton("检查器")
        self.inspector_button.clicked.connect(self._toggle_inspector)
        self.offline_button = QPushButton("离线优先")
        self.offline_button.setCheckable(True)
        self.offline_button.setChecked(self.offline_mode)
        self.offline_button.setToolTip(
            "优先使用 Tools 离线文档；禁用国际网页搜索和非国内 MCP"
        )
        self.offline_button.toggled.connect(self._toggle_offline_mode)
        self.tools_config_button = QPushButton("数据配置")
        self.tools_config_button.setToolTip(
            "选择 Tools 目录并同步离线文档、技能和国内来源 MCP"
        )
        self.tools_config_button.clicked.connect(self._open_tools_config)
        self.theme_button = QToolButton()
        self.theme_button.setText("主题")
        self.theme_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.theme_menu = QMenu(self)
        self.theme_actions: dict[str, QAction] = {}
        self._rebuild_theme_menu()
        self.theme_button.setMenu(self.theme_menu)
        self.settings_button = QPushButton("设置")
        self.settings_button.clicked.connect(self._open_settings)
        for button in (
            self.new_session_button,
            self.instructions_button,
            self.changes_button,
            self.explorer_button,
            self.browser_button,
            self.inspector_button,
            self.tools_config_button,
            self.offline_button,
            self.theme_button,
            self.settings_button,
        ):
            layout.addWidget(button)
        return bar

    def _build_commands(self) -> None:
        self.commands = CommandRegistry(self)
        specs = [
            CommandSpec("terminal.toggle", "打开/收起终端", "Ctrl+`", self._toggle_terminal),
            CommandSpec("explorer.toggle", "打开/收起资源管理器", "Ctrl+E", self._toggle_explorer),
            CommandSpec("browser.open", "打开内置浏览器", "Ctrl+Shift+B", self._open_browser),
            CommandSpec("browser.address", "聚焦浏览器地址栏", "Ctrl+L", self._focus_browser_address),
            CommandSpec("changes.review", "查看本轮文件变更", "Ctrl+Shift+D", self._show_turn_changes),
            CommandSpec(
                "screenshot.region",
                "框选截图并加入对话",
                "Ctrl+Shift+Z",
                self._capture_region_screenshot,
            ),
            CommandSpec(
                "screenshot.screen1",
                "截取显示器 1 并加入对话",
                "Ctrl+Shift+1",
                lambda: self._capture_display_screenshot(0),
            ),
            CommandSpec(
                "screenshot.screen2",
                "截取显示器 2 并加入对话",
                "Ctrl+Shift+2",
                lambda: self._capture_display_screenshot(1),
            ),
            CommandSpec("turn.cancel", "停止当前回合", "Esc", self._cancel_current_turn),
            CommandSpec("nav.toggle", "打开/收起左侧栏", "Ctrl+B", self._toggle_nav),
            CommandSpec("settings.open", "打开设置", "Ctrl+,", self._open_settings),
            CommandSpec(
                "command.palette",
                "打开全局命令面板",
                "Ctrl+Shift+P",
                self._show_command_palette,
            ),
            CommandSpec(
                "search.conversation",
                "搜索对话内容",
                "Ctrl+Shift+F",
                self._focus_conversation_search,
            ),
            CommandSpec(
                "tools.configure",
                "配置离线 Tools 目录",
                "Ctrl+Shift+,",
                self._open_tools_config,
            ),
            CommandSpec(
                "approval.clear",
                "清除当前项目审批记忆",
                "",
                self._clear_project_approval_cache,
            ),
            CommandSpec("session.new", "新建当前类型会话", "Ctrl+N", self._new_current_session),
            CommandSpec("project.new", "新建项目", "Ctrl+Shift+N", self._add_project),
            CommandSpec("inspector.toggle", "打开/收起检查器", "Ctrl+Shift+I", self._toggle_inspector),
        ]
        for spec in specs:
            self.commands.register(spec)
        for identifier in (
            "screenshot.region",
            "screenshot.screen1",
            "screenshot.screen2",
        ):
            self.commands.actions[identifier].setShortcutContext(
                Qt.ShortcutContext.ApplicationShortcut
            )

    def _build_menu(self) -> None:
        view_menu = self.menuBar().addMenu("视图")
        view_menu.addAction(self.commands.actions["terminal.toggle"])
        view_menu.addAction(self.commands.actions["explorer.toggle"])
        view_menu.addAction(self.commands.actions["browser.open"])
        view_menu.addAction(self.commands.actions["changes.review"])
        view_menu.addSeparator()
        view_menu.addAction(self.commands.actions["screenshot.region"])
        view_menu.addAction(self.commands.actions["screenshot.screen1"])
        view_menu.addAction(self.commands.actions["screenshot.screen2"])
        view_menu.addSeparator()
        view_menu.addAction(self.commands.actions["nav.toggle"])
        view_menu.addAction(self.commands.actions["inspector.toggle"])

        file_menu = self.menuBar().addMenu("文件")
        file_menu.addAction(self.commands.actions["project.new"])
        file_menu.addAction(self.commands.actions["session.new"])
        file_menu.addSeparator()
        file_menu.addAction(self.commands.actions["tools.configure"])
        file_menu.addAction(self.commands.actions["approval.clear"])
        file_menu.addAction(self.commands.actions["settings.open"])

        self.screenshot_button = QToolButton(self)
        self.screenshot_button.setObjectName("ScreenshotButton")
        self.screenshot_button.setText("截图")
        self.screenshot_button.setToolTip(
            "框选截图 Ctrl+Shift+Z；显示器 1 Ctrl+Shift+1；显示器 2 Ctrl+Shift+2"
        )
        self.screenshot_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.MenuButtonPopup
        )
        screenshot_menu = QMenu(self.screenshot_button)
        screenshot_menu.addAction(self.commands.actions["screenshot.region"])
        screenshot_menu.addAction(self.commands.actions["screenshot.screen1"])
        screenshot_menu.addAction(self.commands.actions["screenshot.screen2"])
        self.screenshot_button.setMenu(screenshot_menu)
        self.screenshot_button.setDefaultAction(
            self.commands.actions["screenshot.region"]
        )
        self.screenshot_button.setText("截图")
        self.screenshot_button.setToolTip(
            "框选截图 Ctrl+Shift+Z；显示器 1 Ctrl+Shift+1；显示器 2 Ctrl+Shift+2"
        )
        self.menuBar().setCornerWidget(
            self.screenshot_button,
            Qt.Corner.TopRightCorner,
        )

    def eventFilter(self, watched, event) -> bool:  # type: ignore[no-untyped-def]
        if event.type() == QEvent.Type.KeyPress and isinstance(watched, QWidget):
            if watched is self or self.isAncestorOf(watched):
                modifiers = event.modifiers()
                for key, command_id in (
                    (Qt.Key.Key_Z, "screenshot.region"),
                    (Qt.Key.Key_1, "screenshot.screen1"),
                    (Qt.Key.Key_2, "screenshot.screen2"),
                ):
                    if (
                        event.key() == key
                        and modifiers & Qt.KeyboardModifier.ControlModifier
                        and modifiers & Qt.KeyboardModifier.ShiftModifier
                        and not modifiers & Qt.KeyboardModifier.AltModifier
                    ):
                        self.commands.trigger(command_id)
                        event.accept()
                        return True
        return super().eventFilter(watched, event)

    def _refresh_navigation(self) -> None:
        projects = self.db.list_projects()
        include_archived = self.nav.archived_visible
        grouped = {
            project.id: self.db.list_sessions(
                SessionKind.PROJECT,
                project.id,
                include_archived=include_archived,
            )
            for project in projects
        }
        chats = self.db.list_sessions(
            SessionKind.PURE_CHAT,
            include_archived=include_archived,
        )
        self.nav.refresh(projects, grouped, chats)

    def _restore_or_create_initial_session(self) -> None:
        projects = self.db.list_projects()
        if projects:
            self._project_selected(projects[0].id)
            return
        chats = self.db.list_sessions(SessionKind.PURE_CHAT)
        if chats:
            self._pure_chat_selected(chats[0].id)
        else:
            self._new_pure_chat()

    def _add_project(self) -> None:
        dialog = ProjectDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, path = dialog.values()
        try:
            project = self.db.add_project(name, path)
            instruction = project_instructions_path(project.path)
            if not instruction.exists():
                answer = QMessageBox.question(
                    self,
                    "创建项目指令",
                    "该文件夹没有 AGENTS.md。是否现在创建项目专用指令？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if answer == QMessageBox.StandardButton.Yes:
                    InstructionsDialog(project.name, project.path, self).exec()
        except Exception as exc:
            QMessageBox.critical(self, "添加项目失败", str(exc))
            return
        self._refresh_navigation()
        self._project_selected(project.id)

    def _project_selected(self, project_id: int) -> None:
        try:
            project = self.db.get_project(project_id)
        except KeyError:
            return
        self.current_project = project
        self._load_project_approval_cache(project.id)
        self.location_label.setText(project.path)
        self.instructions_button.setEnabled(True)
        self.explorer.set_root(project.path)
        self.terminal.set_root(project.path)
        self.db.touch_project(project.id)
        sessions = self.db.list_sessions(SessionKind.PROJECT, project.id)
        if sessions:
            self._project_session_selected(sessions[0].id)
        else:
            self._new_project_session(project.id)

    def _new_project_session(self, project_id: int) -> None:
        try:
            project = self.db.get_project(project_id)
        except KeyError:
            return
        session = self.db.create_session(
            SessionKind.PROJECT,
            title=DISPLAY_NAME,
            project_id=project.id,
        )
        self._refresh_navigation()
        self._project_session_selected(session.id)

    def _delete_project(self, project_id: int) -> None:
        try:
            project = self.db.get_project(project_id)
        except KeyError:
            return
        sessions = self.db.list_sessions(SessionKind.PROJECT, project_id)
        answer = QMessageBox.question(
            self,
            "删除项目",
            f"确定删除项目“{project.name}”吗？\n\n"
            "将删除应用中的项目记录和全部项目会话，并向智能核心请求删除对应会话。\n"
            "项目文件夹和 AGENTS.md 不会被删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        was_current = bool(
            self.current_project and self.current_project.id == project_id
        )
        for session in sessions:
            self._request_delete_codex_thread(session.backend_id)
        self.db.delete_project(project_id)
        if was_current:
            self.current_project = None
            self.current_session = None
        self._refresh_navigation()
        if was_current:
            self._select_fallback_session()
        else:
            self._reselect_current_session()

    def _delete_project_session(self, session_id: int) -> None:
        try:
            session = self.db.get_session(session_id, SessionKind.PROJECT)
        except KeyError:
            return
        answer = QMessageBox.question(
            self,
            "删除项目会话",
            f"确定删除项目会话“{session.title}”吗？\n\n"
            "该操作会删除应用记录，并向智能核心请求永久删除对应会话。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        was_current = bool(
            self.current_session
            and self.current_kind == SessionKind.PROJECT
            and self.current_session.id == session_id
        )
        project_id = session.project_id
        self._request_delete_codex_thread(session.backend_id)
        self.db.delete_session(session_id, SessionKind.PROJECT)
        if was_current:
            self.current_session = None
        self._refresh_navigation()
        if was_current and project_id is not None:
            try:
                self._project_selected(project_id)
            except KeyError:
                self._select_fallback_session()
        elif was_current:
            self._select_fallback_session()
        else:
            self._reselect_current_session()

    def _delete_pure_chat(self, session_id: int) -> None:
        try:
            session = self.db.get_session(session_id, SessionKind.PURE_CHAT)
        except KeyError:
            return
        answer = QMessageBox.question(
            self,
            "删除纯对话",
            f"确定删除纯对话“{session.title}”吗？\n\n此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        was_current = bool(
            self.current_session
            and self.current_kind == SessionKind.PURE_CHAT
            and self.current_session.id == session_id
        )
        self.db.delete_session(session_id, SessionKind.PURE_CHAT)
        if was_current:
            self.current_session = None
        self._refresh_navigation()
        if was_current:
            self._select_fallback_session()
        else:
            self._reselect_current_session()

    def _request_delete_codex_thread(self, thread_id: str | None) -> None:
        if thread_id and self.gateway.is_ready:
            self.gateway.request("thread/delete", {"threadId": thread_id})

    def _select_fallback_session(self) -> None:
        projects = self.db.list_projects()
        if projects:
            self._project_selected(projects[0].id)
            return
        chats = self.db.list_sessions(SessionKind.PURE_CHAT)
        if chats:
            self._pure_chat_selected(chats[0].id)
            return
        self.current_project = None
        self.current_session = None
        self._new_pure_chat()

    def _reselect_current_session(self) -> None:
        if self.current_session is None:
            self._select_fallback_session()
            return
        if self.current_kind == SessionKind.PROJECT:
            self._project_session_selected(self.current_session.id)
        else:
            self._pure_chat_selected(self.current_session.id)

    def _load_session_message_page(
        self,
        kind: SessionKind,
        session_id: int,
        *,
        before_id: int | None = None,
        prepend: bool = False,
    ) -> None:
        page, has_more = self.db.list_messages_page(
            kind,
            session_id,
            limit=_CHAT_HISTORY_PAGE_SIZE,
            before_id=before_id,
        )
        entries = [
            ChatEntry.from_stored(
                message.role,
                message.content,
                message.metadata_json,
            )
            for message in page
        ]
        if prepend:
            self.chat.prepend_messages(entries)
        else:
            self.chat.set_messages(entries)
        if page:
            self._oldest_loaded_message_id = page[0].id
        self._has_earlier_messages = has_more
        self.chat.set_has_earlier(has_more)

    def _load_earlier_messages(self) -> None:
        if (
            self.current_session is None
            or not self._has_earlier_messages
            or self._oldest_loaded_message_id is None
        ):
            return
        self._load_session_message_page(
            self.current_kind,
            self.current_session.id,
            before_id=self._oldest_loaded_message_id,
            prepend=True,
        )

    def _project_session_selected(self, session_id: int) -> None:
        try:
            session = self.db.get_session(session_id, SessionKind.PROJECT)
            project = self.db.get_project(session.project_id or 0)
        except KeyError:
            return
        self.current_project = project
        self._load_project_approval_cache(project.id)
        self.current_session = session
        self.current_kind = SessionKind.PROJECT
        self._active_thread_id = None
        self.location_label.setText(project.path)
        self.mode_label.setText("项目 Agent")
        self.mode_label.setObjectName("Pill")
        self._restyle_mode_label()
        self.instructions_button.setEnabled(True)
        self.explorer.set_root(project.path)
        self.terminal.set_root(project.path)
        self.chat.set_session(session.title, SessionKind.PROJECT, project.path)
        if not self._busy:
            self.chat.set_agent_status("idle")
        self.inspector.reset_turn()
        self._oldest_loaded_message_id = None
        self._has_earlier_messages = False
        self._load_session_message_page(SessionKind.PROJECT, session.id)
        self._restore_stream_draft(session.id)
        usage = self.db.load_usage(SessionKind.PROJECT, session.id)
        self.chat.set_usage(usage)
        self.inspector.set_session(session.title, SessionKind.PROJECT, project.path)
        self.inspector.set_backend_state(
            f"已建立 ({session.backend_id})"
            if session.backend_id
            else "尚未建立"
        )
        self.inspector.set_usage(usage)
        self.nav.select_project_session(session.id)
        self.work_tabs.setCurrentWidget(self.chat)
        self.chat.focus_composer()

    def _new_pure_chat(self) -> None:
        session = self.db.create_session(
            SessionKind.PURE_CHAT,
            title=DISPLAY_NAME,
            model=None,
        )
        self._refresh_navigation()
        self._pure_chat_selected(session.id)

    def _pure_chat_selected(self, session_id: int) -> None:
        try:
            session = self.db.get_session(session_id, SessionKind.PURE_CHAT)
        except KeyError:
            return
        self.current_session = session
        self.current_kind = SessionKind.PURE_CHAT
        self._active_thread_id = None
        self.current_project = None
        self.location_label.setText("无项目上下文")
        self.mode_label.setText("纯对话")
        self.mode_label.setObjectName("PillWarning")
        self._restyle_mode_label()
        self.instructions_button.setEnabled(False)
        self.chat.set_session(session.title, SessionKind.PURE_CHAT, None)
        if not self._busy:
            self.chat.set_agent_status("idle")
        self.inspector.reset_turn()
        self._oldest_loaded_message_id = None
        self._has_earlier_messages = False
        self._load_session_message_page(SessionKind.PURE_CHAT, session.id)
        self._restore_stream_draft(session.id)
        usage = self.db.load_usage(SessionKind.PURE_CHAT, session.id)
        self.chat.set_usage(usage)
        self.inspector.set_session(session.title, SessionKind.PURE_CHAT, None)
        self.inspector.set_backend_state("纯对话不使用 Codex 会话")
        self.inspector.set_usage(usage)
        self.nav.select_pure_chat(session.id)
        self.work_tabs.setCurrentWidget(self.chat)
        self.chat.focus_composer()

    def _new_current_session(self) -> None:
        if self.current_kind == SessionKind.PROJECT and self.current_project:
            self._new_project_session(self.current_project.id)
        else:
            self._new_pure_chat()

    def _send_message(self, text: str, attachments: list[str]) -> None:
        if self.current_session is None:
            if self.current_kind == SessionKind.PROJECT:
                self._new_project_session(self.current_project.id if self.current_project else 0)
            else:
                self._new_pure_chat()
        if self.current_session is None or self._busy:
            return

        self.db.add_message(
            self.current_kind,
            self.current_session.id,
            "user",
            text,
            {"attachments": attachments},
        )
        self._maybe_name_session(text)
        self.chat.add_message("user", text)
        self._set_busy(True)
        self.chat.set_agent_status("responding")
        offline_context = self._offline_prompt_context(text)

        if self.current_kind == SessionKind.PROJECT:
            self._send_project_message(text, attachments, offline_context)
        else:
            self._send_pure_message(offline_context, attachments)

    def _send_project_message(
        self,
        text: str,
        attachments: list[str],
        offline_context: str = "",
    ) -> None:
        if self.current_project is None or self.current_session is None:
            self._show_error("当前项目信息不可用。")
            return
        prompt = text
        if offline_context:
            prompt = f"{offline_context}\n\n【用户任务】\n{prompt}"
        image_paths = [
            str(Path(path).resolve())
            for path in attachments
            if _is_image_path(Path(path))
        ]
        if image_paths:
            prompt += "\n\n【附加截图】\n" + "\n".join(
                f"- {Path(path).name}" for path in image_paths
            )
        relative_paths: list[str] = []
        for attachment in attachments:
            try:
                relative = Path(attachment).resolve().relative_to(Path(self.current_project.path))
                relative_paths.append(str(relative).replace("\\", "/"))
            except ValueError:
                continue
        if relative_paths:
            prompt += "\n\n引用文件：\n" + "\n".join(f"@{path}" for path in relative_paths)

        if not self.gateway.is_ready:
            self._show_error(f"{DISPLAY_NAME} 智能核心尚未连接，请检查核心安装和登录状态。")
            return
        context_limit = self.chat.composer.context_message_limit
        session_id = self.current_session.id
        if context_limit is None and self.current_session.backend_id:
            self._start_codex_turn(
                self.current_session.backend_id,
                prompt,
                image_paths,
            )
            return
        if context_limit is not None:
            history, _has_more = self.db.list_messages_page(
                SessionKind.PROJECT,
                session_id,
                limit=max(context_limit + 1, 1),
            )
            history = history[:-1] if history else []
            prompt = self._project_prompt_with_history(
                prompt,
                history,
                context_limit,
            )
        elif not self.current_session.backend_id:
            history = self.db.list_messages(
                SessionKind.PROJECT,
                session_id,
            )
            if history:
                history = history[:-1]
            prompt = self._project_prompt_with_history(
                prompt,
                history,
                None,
            )

        def thread_ready(result: dict[str, Any]) -> None:
            thread = result.get("thread") or {}
            thread_id = thread.get("id")
            if not thread_id:
                self._show_error("智能核心未返回有效会话标识。")
                return
            if context_limit is None:
                self.db.update_session_backend_id(session_id, str(thread_id))
                session = self.db.get_session(session_id, SessionKind.PROJECT)
                self.current_session = session
            self._start_codex_turn(str(thread_id), prompt, image_paths)

        self.gateway.start_thread(
            cwd=self.current_project.path,
            callback=thread_ready,
            model=self.current_session.model,
        )

    @staticmethod
    def _project_prompt_with_history(
        current_prompt: str,
        history: list,
        context_limit: int | None,
    ) -> str:
        if not history:
            return current_prompt
        lines = [
            "【本轮上下文范围】",
            (
                f"只使用以下最近 {len(history)} 条历史消息；"
                "不要假设存在更早上下文。"
                if context_limit is not None
                else "以下是当前会话的全部历史消息。"
            ),
            "",
        ]
        for message in history:
            label = "用户" if message.role == "user" else "智能体"
            lines.extend([f"{label}：", message.content, ""])
        lines.extend(["【当前用户任务】", current_prompt])
        return "\n".join(lines)

    def _start_codex_turn(
        self,
        thread_id: str,
        prompt: str,
        image_paths: list[str] | None = None,
    ) -> None:
        if self.current_project is None:
            return
        self._streaming_text = ""
        self._streaming_started = False
        self._active_thread_id = thread_id
        self.chat.begin_assistant_stream()
        self.inspector.add_activity("模型请求开始")

        def turn_started(result: dict[str, Any]) -> None:
            turn = result.get("turn") or {}
            self._current_turn_id = turn.get("id")
            self._turn_started_at = datetime.now()
            self._item_started_at = {}
            self._command_outputs = {}
            self.inspector.reset_turn()
            if self.current_project and self.current_session:
                self._turn_change_tracker = TurnChangeTracker(
                    self.current_project.path,
                    app_data_dir() / "change-checkpoints",
                    self.current_session.id,
                    str(self._current_turn_id or "turn"),
                )
                self.changes_button.setEnabled(False)

        permission_mode = self.chat.composer.permission_mode
        if self.offline_mode:
            sandbox_policy = {
                "type": "readOnly" if permission_mode == "read_only" else "workspaceWrite",
                "writableRoots": [self.current_project.path],
                "networkAccess": False,
            }
        elif permission_mode == "read_only":
            sandbox_policy = {"type": "readOnly", "networkAccess": False}
        elif permission_mode == "full_access":
            sandbox_policy = {"type": "dangerFullAccess"}
        else:
            sandbox_policy = {
                "type": "workspaceWrite",
                "writableRoots": [self.current_project.path],
                "networkAccess": False,
            }
        approval_policy = _approval_policy_for_mode(permission_mode)
        collaboration_mode = None
        if permission_mode == "plan":
            collaboration_mode = {
                "mode": "plan",
                "settings": {
                    "model": (
                        self.current_session.model
                        or self.codex_config.model
                    ),
                    "reasoning_effort": self.chat.composer.reasoning_effort,
                },
            }
        self.gateway.start_turn(
            thread_id=thread_id,
            text=prompt,
            cwd=self.current_project.path,
            callback=turn_started,
            effort=self.chat.composer.reasoning_effort,
            service_tier=self.chat.composer.service_tier,
            sandbox_policy=sandbox_policy,
            approval_policy=approval_policy,
            collaboration_mode=collaboration_mode,
            image_paths=image_paths,
        )

    def _send_pure_message(
        self,
        offline_context: str = "",
        attachments: list[str] | None = None,
    ) -> None:
        if self.current_session is None:
            return
        self.codex_config = CodexConfig.load()
        self.pure_chat.config = self.codex_config
        if not self.codex_config.is_configured:
            detail = self.codex_config.load_error or (
                f"{self.codex_config.auth_description}，模型 {self.codex_config.model or '未配置'}"
            )
            self._show_error(
                "无法使用 ~/.codex/config.toml 中的模型配置："
                f"{detail}"
            )
            return
        context_limit = self.chat.composer.context_message_limit
        if context_limit is None:
            messages = self.db.list_messages(
                SessionKind.PURE_CHAT,
                self.current_session.id,
            )
            has_more = False
        else:
            messages, has_more = self.db.list_messages_page(
                SessionKind.PURE_CHAT,
                self.current_session.id,
                limit=max(context_limit, 1),
            )
        context_window = int(
            self.db.get_setting("pure_chat_context_window", "0") or "0"
        )
        payload, trimmed_count = _build_pure_chat_payload(
            messages,
            context_window or None,
            extra_context_chars=len(offline_context),
        )
        if has_more:
            self.statusBar().showMessage(
                f"已按上下文策略只发送最近 {context_limit} 条消息。",
                6000,
            )
        elif trimmed_count:
            self.statusBar().showMessage(
                f"已按上下文预算裁剪 {trimmed_count} 条较早消息。",
                6000,
            )
        if offline_context:
            for item in reversed(payload):
                if item["role"] == "user":
                    item["content"] = (
                        f"{offline_context}\n\n【用户任务】\n{item['content']}"
                    )
                    break
        image_attachments = [
            path
            for path in attachments or []
            if _is_image_path(Path(path))
        ]
        if image_attachments:
            try:
                content_parts: list[dict[str, Any]] = [
                    {
                        "type": "input_text",
                        "text": str(payload[-1]["content"]),
                    }
                ]
                content_parts.extend(
                    {
                        "type": "input_image",
                        "image_url": _image_data_url(Path(path)),
                        "detail": "auto",
                    }
                    for path in image_attachments
                )
            except OSError as exc:
                self._show_error(f"无法读取图片附件：{exc}")
                return
            payload[-1]["content"] = content_parts
        self.chat.begin_assistant_stream()
        self.inspector.add_activity("纯对话流式请求开始")
        self.pure_chat.send(
            payload,
            reasoning_effort=self.chat.composer.reasoning_effort,
            service_tier=self.chat.composer.service_tier,
        )

    def _configure_offline_gateway(self) -> None:
        disabled = tuple(
            name
            for name in self.codex_config.mcp_server_ids
            if name != "china-sources"
        )
        self.gateway.configure_offline_mode(self.offline_mode, disabled)

    def _toggle_offline_mode(self, checked: bool) -> None:
        if self._busy:
            self.offline_button.blockSignals(True)
            self.offline_button.setChecked(not checked)
            self.offline_button.blockSignals(False)
            self.statusBar().showMessage("当前回合运行中，完成后再切换离线模式。", 6000)
            return
        self.offline_mode = checked
        self.db.set_setting("offline_mode", "1" if checked else "0")
        self.codex_config = CodexConfig.load()
        self._configure_offline_gateway()
        self.gateway.restart()
        if checked:
            if self.offline_docs.available:
                self.statusBar().showMessage("离线优先已启用：正在准备本地文档索引…", 8000)
                try:
                    self.offline_docs.ensure_index()
                except Exception as exc:
                    self.statusBar().showMessage(f"离线文档索引失败: {exc}", 12000)
            else:
                self.statusBar().showMessage("未找到 Tools 离线文档目录。", 10000)
        else:
            self.statusBar().showMessage("离线优先已关闭。", 5000)

    def _offline_prompt_context(self, query: str) -> str:
        if not self.offline_mode:
            return ""
        try:
            docs_context = self.offline_docs.build_prompt_context(query)
        except Exception as exc:
            docs_context = f"【离线资料检索失败】\n{exc}"
        policy = offline_policy_text()
        self.inspector.add_activity("离线模式：检索 Tools 文档")
        return (
            "【J.A.R.V.I.S. 离线模式】\n"
            f"Tools 数据目录：{self.tools_root}\n"
            f"离线技能目录：{self.tools_root / 'skills'}\n"
            f"{policy}\n\n{docs_context}"
        )

    def _server_request(
        self,
        request_id: int,
        method: str,
        params: dict[str, Any],
    ) -> None:
        event_thread_id = params.get("threadId")
        current_thread_id = self._active_thread_id
        if event_thread_id and current_thread_id and event_thread_id != current_thread_id:
            self._cancel_inactive_server_request(request_id, method, params)
            return

        fingerprint = self._approval_fingerprint(method, params)
        if fingerprint and fingerprint in self._project_approval_cache:
            self.gateway.respond(
                request_id,
                self._cached_approval_response(method, params),
            )
            self.inspector.add_activity(f"已使用项目审批记忆：{method}")
            return

        self.inspector.add_activity(f"等待审批：{method}")
        self.chat.set_agent_status("responding", "等待审批")
        dialog = ServerRequestDialog(method, params, self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        if accepted and dialog.result_payload is not None:
            if dialog.approval_choice == "project" and fingerprint:
                self._project_approval_cache.add(fingerprint)
                self._persist_project_approval_cache()
                self.inspector.add_activity("已记住本项目审批规则")
            if dialog.rejection_reason:
                self.inspector.add_activity(
                    f"拒绝原因：{dialog.rejection_reason}"
                )
            self.gateway.respond(request_id, dialog.result_payload)
            self.inspector.add_activity(f"审批已提交：{method}")
            return
        fallback = self._cancelled_server_response(method)
        self.gateway.respond(request_id, fallback)
        self.inspector.add_activity(f"审批已拒绝：{method}")

    def _approval_fingerprint(
        self,
        method: str,
        params: dict[str, Any],
    ) -> str:
        if method in {
            "item/commandExecution/requestApproval",
            "execCommandApproval",
        }:
            network = params.get("networkApprovalContext") or {}
            target = (
                f"{network.get('protocol') or ''}://{network.get('host') or ''}"
                if network
                else str(params.get("command") or params.get("cwd") or "")
            )
            return f"{method}:{target.strip()}" if target.strip() else ""
        if method in {
            "item/fileChange/requestApproval",
            "applyPatchApproval",
        }:
            item_id = str(params.get("itemId") or "")
            paths = sorted(self._file_paths_by_item.get(item_id, []))
            target = "|".join(paths) or str(params.get("grantRoot") or "")
            return f"{method}:{target.strip()}" if target.strip() else ""
        if method == "item/permissions/requestApproval":
            return method + ":" + json.dumps(
                params.get("permissions") or {},
                ensure_ascii=False,
                sort_keys=True,
            )
        return ""

    @staticmethod
    def _cached_approval_response(
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        if method in {"execCommandApproval", "applyPatchApproval"}:
            return {"decision": "approved_for_session"}
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            return {"decision": "acceptForSession"}
        if method == "item/permissions/requestApproval":
            return {
                "permissions": params.get("permissions") or {},
                "scope": "session",
            }
        return {}

    def _load_project_approval_cache(self, project_id: int) -> None:
        self._approval_cache_project_id = project_id
        raw = self.db.get_setting(f"approval_cache:{project_id}", "") or ""
        try:
            values = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            values = []
        self._project_approval_cache = set(
            str(value) for value in values
        )

    def _persist_project_approval_cache(self) -> None:
        if self._approval_cache_project_id is None:
            return
        self.db.set_setting(
            f"approval_cache:{self._approval_cache_project_id}",
            json.dumps(
                sorted(self._project_approval_cache),
                ensure_ascii=False,
            ),
        )

    def _clear_project_approval_cache(self) -> None:
        if self._approval_cache_project_id is None:
            self.statusBar().showMessage("当前没有项目审批记忆。", 5000)
            return
        self._project_approval_cache.clear()
        self._persist_project_approval_cache()
        self.statusBar().showMessage("已清除当前项目的审批记忆。", 5000)

    def _cancel_inactive_server_request(
        self,
        request_id: int,
        method: str,
        _params: dict[str, Any],
    ) -> None:
        self.gateway.respond(
            request_id,
            self._cancelled_server_response(method),
        )

    @staticmethod
    def _cancelled_server_response(method: str) -> dict[str, Any]:
        if method in {"execCommandApproval", "applyPatchApproval"}:
            return {"decision": "abort"}
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            return {"decision": "cancel"}
        if method == "item/permissions/requestApproval":
            return {"permissions": {}, "scope": "turn"}
        if method == "item/tool/requestUserInput":
            return {"answers": {}}
        if method == "mcpServer/elicitation/request":
            return {"action": "cancel", "content": None}
        return {}

    def _backup_database_if_needed(self) -> None:
        if not self.db.path.exists():
            return
        backup = self.db.path.with_name(f"{self.db.path.stem}-backup.db")
        if (
            backup.exists()
            and datetime.now() - datetime.fromtimestamp(backup.stat().st_mtime)
            < timedelta(days=1)
        ):
            return
        try:
            self.db.backup_to(backup)
        except Exception:
            pass

    def _draft_key(self, kind: SessionKind, session_id: int) -> str:
        return f"stream_draft:{kind.value}:{session_id}"

    def _save_stream_draft(self) -> None:
        if self.current_session is None:
            return
        snapshot = self.chat.last_assistant_snapshot()
        if not (
            snapshot.get("content")
            or snapshot.get("reasoning_summary")
            or snapshot.get("reasoning_raw")
        ):
            return
        self.db.set_setting(
            self._draft_key(self.current_kind, self.current_session.id),
            json.dumps(snapshot, ensure_ascii=False),
        )

    def _clear_stream_draft(self) -> None:
        self._draft_timer.stop()
        if self.current_session is not None:
            self.db.delete_setting(
                self._draft_key(self.current_kind, self.current_session.id)
            )

    def _restore_stream_draft(self, session_id: int) -> None:
        raw = self.db.get_setting(self._draft_key(self.current_kind, session_id))
        if not raw:
            return
        try:
            snapshot = json.loads(raw)
        except json.JSONDecodeError:
            self.db.delete_setting(self._draft_key(self.current_kind, session_id))
            return
        entry = ChatEntry.from_stored(
            "assistant",
            str(snapshot.get("content") or ""),
            json.dumps(snapshot, ensure_ascii=False),
        )
        if entry.content or entry.has_reasoning:
            self.chat.add_entry(entry)
            self.inspector.add_activity("已恢复上次未完成的流式草稿")
            self.db.delete_setting(self._draft_key(self.current_kind, session_id))

    def _codex_notification(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params") or {}
        event_thread_id = params.get("threadId")
        current_thread_id = self._active_thread_id
        if event_thread_id and current_thread_id and event_thread_id != current_thread_id:
            return
        if method == "item/agentMessage/delta":
            delta = str(params.get("delta") or params.get("text") or "")
            if delta:
                if not self._streaming_started:
                    self._streaming_started = True
                self._streaming_text += delta
                self.chat.append_assistant_stream(delta)
                self._draft_timer.start()
            return
        if method == "item/reasoning/summaryTextDelta":
            self.chat.append_reasoning_delta(
                str(params.get("delta") or ""),
                index=int(params.get("summaryIndex") or 0),
                item_id=str(params.get("itemId") or "reasoning"),
            )
            self._draft_timer.start()
            return
        if method == "item/reasoning/summaryPartAdded":
            self.chat.begin_reasoning_stream(
                str(params.get("itemId") or "reasoning")
            )
            self._draft_timer.start()
            return
        if method == "item/reasoning/textDelta":
            self.chat.append_reasoning_delta(
                str(params.get("delta") or ""),
                raw=True,
                index=int(params.get("contentIndex") or 0),
                item_id=str(params.get("itemId") or "reasoning"),
            )
            self._draft_timer.start()
            return
        if method == "item/started":
            item = params.get("item") or {}
            item_type = item.get("type")
            item_id = str(item.get("id") or "")
            if item_id:
                self._item_started_at[item_id] = datetime.now()
            if item_type == "reasoning":
                self.chat.begin_reasoning_stream(
                    str(item.get("id") or "reasoning")
                )
                self._draft_timer.start()
            elif item_type == "commandExecution":
                command = str(item.get("command") or "")
                self.inspector.set_current_activity(f"命令：{command}")
                self.inspector.add_activity(f"命令：{command}")
            elif item_type == "fileChange":
                paths = [
                    change.get("path", "")
                    for change in item.get("changes", [])
                    if isinstance(change, dict)
                ]
                if item_id:
                    self._file_paths_by_item[item_id] = [
                        str(path) for path in paths if path
                    ]
                self._track_file_changes(item.get("changes") or [])
                self.inspector.add_activity(f"文件变更：{', '.join(paths)}")
            elif item_type in {"mcpToolCall", "collabToolCall"}:
                tool_name = str(item.get("tool") or item.get("name") or "")
                self.inspector.set_current_activity(
                    f"{item_type}：{tool_name}"
                )
                self.inspector.add_activity(
                    f"{item_type}：{tool_name}"
                )
            return
        if method == "item/completed":
            item = params.get("item") or {}
            item_id = str(item.get("id") or "")
            started = self._item_started_at.pop(item_id, None)
            duration = (
                (datetime.now() - started).total_seconds()
                if started
                else None
            )
            if duration is not None:
                self.inspector.add_activity(
                    f"{item.get('type') or 'item'} 完成，耗时 {duration:.2f} 秒"
                )
            if item.get("type") == "commandExecution":
                output = self._command_outputs.pop(item_id, "").strip()
                if output:
                    self.inspector.add_activity(
                        "命令输出："
                        + output[-1200:].replace("\r", "")
                    )
            if item.get("type") == "reasoning":
                self.chat.complete_reasoning(
                    summary=[
                        str(text)
                        for text in item.get("summary") or []
                        if str(text).strip()
                    ],
                    content=[
                        str(text)
                        for text in item.get("content") or []
                        if str(text).strip()
                    ],
                    item_id=str(item.get("id") or "reasoning"),
                )
            elif item.get("type") == "fileChange":
                self._track_file_changes(item.get("changes") or [])
                if item_id:
                    self._file_paths_by_item.pop(item_id, None)
            if item.get("type") in {
                "commandExecution",
                "mcpToolCall",
                "collabToolCall",
                "fileChange",
            }:
                self.inspector.set_current_activity("")
            return
        if method == "item/commandExecution/outputDelta":
            item_id = str(params.get("itemId") or "")
            delta = str(params.get("delta") or "")
            if item_id and delta:
                output = (self._command_outputs.get(item_id, "") + delta)[-20000:]
                self._command_outputs[item_id] = output
            return
        if method == "turn/diff/updated":
            if self._turn_change_tracker is not None:
                self._turn_change_tracker.set_aggregate_diff(
                    str(params.get("diff") or "")
                )
            return
        if method == "turn/plan/updated":
            self.inspector.set_plan(
                str(params.get("explanation") or ""),
                list(params.get("plan") or []),
            )
            return
        if method == "thread/tokenUsage/updated":
            usage = self._parse_token_usage(params.get("tokenUsage") or {})
            if self.current_session is not None:
                self.db.save_usage(
                    SessionKind.PROJECT,
                    self.current_session.id,
                    usage,
                )
            self.chat.set_usage(usage)
            self.inspector.set_usage(usage)
            return
        if method == "turn/completed":
            turn = params.get("turn") or {}
            completed_turn_id = turn.get("id")
            if (
                self._current_turn_id
                and completed_turn_id
                and completed_turn_id != self._current_turn_id
            ):
                return
            self._finish_project_turn(str(turn.get("status") or "completed"))
            return
        if method == "error":
            self._show_error(str(params.get("message") or params))

    def _finish_project_turn(self, turn_status: str = "completed") -> None:
        if self.current_session is None:
            self._set_busy(False)
            return
        content = self.chat.finish_assistant_stream().strip()
        metadata = self.chat.last_assistant_metadata()
        if content:
            self.db.add_message(
                SessionKind.PROJECT,
                self.current_session.id,
                "assistant",
                content,
                metadata,
            )
        self._clear_stream_draft()
        duration = (
            (datetime.now() - self._turn_started_at).total_seconds()
            if self._turn_started_at
            else 0
        )
        self.inspector.add_activity("模型请求结束")
        self.inspector.add_activity(f"回合完成，耗时 {duration:.1f} 秒")
        self._current_turn_id = None
        self._active_thread_id = None
        self._streaming_text = ""
        self._streaming_started = False
        self._turn_started_at = None
        self._set_busy(False)
        if turn_status == "failed":
            self.chat.set_agent_status("error")
        elif turn_status == "interrupted":
            self.chat.set_agent_status("cancelled")
        else:
            self.chat.set_agent_status("done")

    def _pure_chat_response_started(self) -> None:
        if self.current_session is None or self.current_kind != SessionKind.PURE_CHAT:
            return
        self.chat.set_agent_status("responding")

    def _pure_chat_reasoning_started(self, item_id: str) -> None:
        if self.current_session is None or self.current_kind != SessionKind.PURE_CHAT:
            return
        self.chat.begin_reasoning_stream(item_id)
        self._draft_timer.start()

    def _pure_chat_reasoning_summary_delta(
        self,
        delta: str,
        index: int,
        item_id: str,
    ) -> None:
        if self.current_session is None or self.current_kind != SessionKind.PURE_CHAT:
            return
        self.chat.append_reasoning_delta(
            delta,
            index=index,
            item_id=item_id,
        )
        self._draft_timer.start()

    def _pure_chat_reasoning_text_delta(
        self,
        delta: str,
        index: int,
        item_id: str,
    ) -> None:
        if self.current_session is None or self.current_kind != SessionKind.PURE_CHAT:
            return
        self.chat.append_reasoning_delta(
            delta,
            raw=True,
            index=index,
            item_id=item_id,
        )
        self._draft_timer.start()

    def _pure_chat_text_delta(self, delta: str) -> None:
        if self.current_session is None or self.current_kind != SessionKind.PURE_CHAT:
            return
        self._streaming_started = True
        self._streaming_text += delta
        self.chat.append_assistant_stream(delta)
        self._draft_timer.start()

    def _pure_chat_response(
        self,
        text: str,
        usage: dict[str, int],
        reasoning: dict[str, str],
    ) -> None:
        if self.current_session is None:
            return
        if reasoning:
            summary = reasoning.get("reasoning_summary", "")
            raw = reasoning.get("reasoning_raw", "")
            self.chat.complete_reasoning(
                summary=[summary] if summary else [],
                content=[raw] if raw else [],
            )
        snapshot = self.chat.last_assistant_snapshot()
        final_text = text or str(snapshot.get("content") or "")
        content = self.chat.finish_assistant_stream(final_text).strip()
        metadata = self.chat.last_assistant_metadata()
        if content:
            self.db.add_message(
                SessionKind.PURE_CHAT,
                self.current_session.id,
                "assistant",
                content,
                metadata,
            )
        previous = self.db.load_usage(SessionKind.PURE_CHAT, self.current_session.id)
        current = UsageStats(
            input_tokens=usage.get("input_tokens", 0),
            cached_input_tokens=usage.get("cached_input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            reasoning_output_tokens=usage.get("reasoning_output_tokens", 0),
            total_tokens=previous.total_tokens + usage.get("total_tokens", 0),
            context_tokens=usage.get("context_tokens", 0),
            context_window=previous.context_window
            or int(self.db.get_setting("pure_chat_context_window", "0") or "0"),
        )
        self.db.save_usage(SessionKind.PURE_CHAT, self.current_session.id, current)
        self.chat.set_usage(current)
        self.inspector.set_usage(current)
        self._clear_stream_draft()
        self._streaming_text = ""
        self._streaming_started = False
        self.inspector.add_activity("纯对话流式回答完成")
        self._set_busy(False)
        self.chat.set_agent_status("done")

    def _pure_chat_cancelled(self) -> None:
        if self.current_session is None:
            self._set_busy(False)
            return
        content = self.chat.finish_assistant_stream().strip()
        metadata = self.chat.last_assistant_metadata()
        if content or metadata:
            self.db.add_message(
                SessionKind.PURE_CHAT,
                self.current_session.id,
                "assistant",
                content or "（已停止）",
                metadata,
            )
        else:
            self.chat.discard_empty_assistant()
        self._clear_stream_draft()
        self._streaming_text = ""
        self._streaming_started = False
        self.inspector.add_activity("纯对话生成已停止")
        self._set_busy(False)
        self.chat.set_agent_status("cancelled")
        self.statusBar().showMessage("已停止生成。", 5000)

    def _cancel_current_turn(self) -> None:
        if not self._busy or self.current_session is None:
            self.statusBar().showMessage("当前没有正在生成的回答。", 5000)
            return
        self.chat.set_agent_status("responding", "正在停止")
        self.chat.composer.stop_button.setEnabled(False)
        if self.current_kind == SessionKind.PURE_CHAT:
            self.inspector.add_activity("正在停止纯对话生成")
            self.statusBar().showMessage("已请求停止生成。", 5000)
            self.pure_chat.cancel()
            return
        if (
            not self.current_session.backend_id
            or not self._current_turn_id
        ):
            self.chat.composer.stop_button.setEnabled(True)
            self.chat.set_agent_status("responding")
            self.statusBar().showMessage(
                "项目回合仍在初始化，暂时无法停止。",
                5000,
            )
            return
        self.gateway.interrupt_turn(
            self.current_session.backend_id,
            self._current_turn_id,
        )
        self.inspector.add_activity("正在停止当前回合")
        self.statusBar().showMessage("已请求停止当前回合。", 5000)

    def _steer_current_turn(self, text: str) -> None:
        clean = text.strip()
        if not clean:
            return
        if (
            not self._busy
            or self.current_session is None
            or self.current_kind != SessionKind.PROJECT
            or not self.current_session.backend_id
        ):
            self.statusBar().showMessage(
                "当前没有正在运行的项目回合，可直接发送新消息。",
                6000,
            )
            return
        self.gateway.steer_turn(
            self.current_session.backend_id,
            clean,
            self._current_turn_id,
        )
        self.db.add_message(
            SessionKind.PROJECT,
            self.current_session.id,
            "user",
            f"补充要求：{clean}",
        )
        self.chat.add_message("user", f"补充要求：{clean}")
        self.inspector.add_activity("已向当前回合发送补充要求")

    def _pure_chat_error(self, message: str) -> None:
        self._clear_stream_draft()
        self._streaming_text = ""
        self._streaming_started = False
        self._show_error(message)

    def _gateway_error(self, message: str) -> None:
        self.gateway_label.setText(f"{DISPLAY_NAME} 异常")
        self.statusBar().showMessage(message, 12000)
        self.chat.set_agent_status("error")
        if self._busy and self.current_kind == SessionKind.PROJECT:
            self._show_error(message)

    def _gateway_status(self, message: str) -> None:
        self.gateway_label.setText(message)
        self.statusBar().showMessage(message, 5000)
        self.inspector.set_backend_state(message)
        if any(keyword in message for keyword in ("断开", "失败", "未运行")):
            self.chat.set_agent_status("error")

    def _protocol_info(self, info: dict[str, Any]) -> None:
        self.inspector.set_protocol_info(info)

    def _load_model_catalog(self) -> None:
        self.gateway.request(
            "model/list",
            {"limit": 100, "includeHidden": True},
            self._apply_model_catalog,
        )

    def _apply_model_catalog(self, result: dict[str, Any]) -> None:
        models = result.get("data") or []
        selected = next(
            (
                model
                for model in models
                if model.get("id") == self.codex_config.model
                or model.get("model") == self.codex_config.model
            ),
            None,
        )
        if selected is None:
            self.chat.composer.set_fallback_reasoning_efforts(
                self.codex_config.reasoning_effort
            )
            return
        efforts = list(selected.get("supportedReasoningEfforts") or [])
        if not efforts:
            self.chat.composer.set_fallback_reasoning_efforts(
                self.codex_config.reasoning_effort
                or selected.get("defaultReasoningEffort")
            )
            return
        self.chat.composer.set_model_capabilities(
            efforts=efforts,
            default_effort=selected.get("defaultReasoningEffort"),
            service_tiers=list(selected.get("serviceTiers") or []),
            preferred_effort=self.codex_config.reasoning_effort,
            default_service_tier=selected.get("defaultServiceTier"),
        )

    def _show_error(self, message: str) -> None:
        self.chat.discard_empty_assistant()
        self.chat.add_message("error", message)
        if self.current_session is not None:
            self.db.add_message(self.current_kind, self.current_session.id, "error", message)
        self._set_busy(False)
        self.chat.set_agent_status("error")

    def _track_file_changes(self, changes: list[dict[str, Any]]) -> None:
        if self._turn_change_tracker is None or not changes:
            return
        self._turn_change_tracker.track_changes(changes)
        self.changes_button.setEnabled(
            self._turn_change_tracker.modified_count > 0
        )
        self.inspector.set_modified_count(
            self._turn_change_tracker.modified_count
        )

    def _show_turn_changes(self) -> None:
        if (
            self._turn_change_tracker is None
            or self._turn_change_tracker.modified_count == 0
        ):
            self.statusBar().showMessage("当前回合没有可查看的文件变更。", 5000)
            return
        dialog = ChangeReviewDialog(self._turn_change_tracker, self)
        dialog.exec()
        self.inspector.add_activity(
            f"文件变更审阅完成：{self._turn_change_tracker.modified_count} 个文件"
        )

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.chat.set_busy(busy)

    def _toggle_terminal(self) -> None:
        visible = not self.terminal_dock.isVisible()
        self.terminal_dock.setVisible(visible)
        if visible:
            root = self.current_project.path if self.current_project else str(Path.cwd())
            self.terminal.set_root(root)
            self.terminal.start()
            self.terminal.input.setFocus()

    def _toggle_explorer(self) -> None:
        visible = not self.explorer.isVisible()
        if visible and not self.current_project:
            folder = QFileDialog.getExistingDirectory(self, "选择要浏览的文件夹")
            if not folder:
                return
            self.explorer.set_root(folder)
        elif visible and self.current_project:
            self.explorer.set_root(self.current_project.path)
        self.explorer.setVisible(visible)
        if visible:
            self.body_splitter.setSizes([250, 280, 820, 250])

    def _capture_region_screenshot(self) -> None:
        screenshot = capture_virtual_desktop()
        virtual_geometry = virtual_screen_geometry()
        if screenshot is None or virtual_geometry.isEmpty():
            QMessageBox.warning(self, "截图失败", "无法读取当前桌面画面。")
            return
        overlay = ScreenshotOverlay(screenshot, virtual_geometry)
        overlay.captured.connect(self._screenshot_captured)
        overlay.cancelled.connect(self._clear_screenshot_overlay)
        overlay.destroyed.connect(self._clear_screenshot_overlay)
        self._screenshot_overlay = overlay
        overlay.show_overlay()

    def _capture_display_screenshot(self, index: int) -> None:
        screenshot = capture_screen(index)
        if screenshot is None:
            QMessageBox.warning(
                self,
                "截图失败",
                f"未检测到显示器 {index + 1}。请确认该显示器已连接并启用。",
            )
            return
        self._screenshot_captured(screenshot)

    def _screenshot_captured(self, pixmap) -> None:  # type: ignore[no-untyped-def]
        self._screenshot_overlay = None
        try:
            path = save_screenshot(pixmap)
        except OSError as exc:
            QMessageBox.warning(self, "截图失败", str(exc))
            return
        self.work_tabs.setCurrentWidget(self.chat)
        if not self.chat.append_attachment(str(path)):
            QMessageBox.warning(self, "截图失败", "截图已保存，但无法加入当前对话。")
            return
        self.chat.focus_composer()
        self.statusBar().showMessage(f"截图已加入对话：{path}", 5000)

    def _clear_screenshot_overlay(self, *_args) -> None:  # type: ignore[no-untyped-def]
        self._screenshot_overlay = None

    def _open_browser(self) -> None:
        if self._browser_workspace is None:
            self._browser_workspace = BrowserWorkspace()
            self._browser_workspace.closeRequested.connect(
                lambda: self._close_browser()
            )
            self.work_tabs.addTab(self._browser_workspace, "浏览器")
        self.work_tabs.setCurrentWidget(self._browser_workspace)

    def _close_browser(self) -> None:
        browser = self._browser_workspace
        if browser is None:
            return
        index = self.work_tabs.indexOf(browser)
        if index >= 0:
            self.work_tabs.removeTab(index)
        self._browser_workspace = None
        browser.deleteLater()
        self.work_tabs.setCurrentWidget(self.chat)

    def _work_tab_context_menu(self, position) -> None:  # type: ignore[no-untyped-def]
        if not self._is_browser_tab_position(position):
            return
        tab_bar = self.work_tabs.tabBar()
        menu = QMenu(self)
        close_action = menu.addAction("关闭浏览器")
        selected = menu.exec(tab_bar.mapToGlobal(position))
        if selected is close_action:
            self._close_browser()

    def _is_browser_tab_position(self, position) -> bool:  # type: ignore[no-untyped-def]
        tab_bar = self.work_tabs.tabBar()
        index = tab_bar.tabAt(position)
        return (
            index >= 0
            and self.work_tabs.widget(index) is self._browser_workspace
        )

    def _focus_browser_address(self) -> None:
        self._open_browser()
        if self._browser_workspace:
            self._browser_workspace.focus_address()

    def _set_session_pinned(
        self,
        kind_value: str,
        session_id: int,
        pinned: bool,
    ) -> None:
        SessionKind(kind_value)
        self.db.set_session_pinned(session_id, pinned)
        self._refresh_navigation()
        self._reselect_current_session()

    def _set_session_archived(
        self,
        kind_value: str,
        session_id: int,
        archived: bool,
    ) -> None:
        kind = SessionKind(kind_value)
        self.db.set_session_archived(session_id, archived)
        self._refresh_navigation()
        if (
            archived
            and not self.nav.archived_visible
            and self.current_session
            and self.current_session.id == session_id
            and self.current_kind == kind
        ):
            self._select_fallback_session()

    def _rename_session(self, kind_value: str, session_id: int) -> None:
        kind = SessionKind(kind_value)
        try:
            session = self.db.get_session(session_id, kind)
        except KeyError:
            return
        name, accepted = QInputDialog.getText(
            self,
            "重命名会话",
            "新名称：",
            text=session.title,
        )
        if not accepted or not name.strip():
            return
        self.db.update_session_title(session_id, name.strip())
        self._refresh_navigation()
        self._reselect_current_session()

    def _fork_project_session(self, session_id: int) -> None:
        try:
            session = self.db.get_session(session_id, SessionKind.PROJECT)
        except KeyError:
            return
        if not session.backend_id or not self.gateway.is_ready:
            self.statusBar().showMessage("Codex 会话尚未就绪，暂时无法创建分支。", 6000)
            return

        def forked(result: dict[str, Any]) -> None:
            thread = result.get("thread") or {}
            backend_id = thread.get("id")
            if not backend_id:
                self.statusBar().showMessage("Codex 未返回分支会话标识。", 6000)
                return
            new_session = self.db.create_session(
                SessionKind.PROJECT,
                f"{session.title} · 分支",
                project_id=session.project_id,
                model=session.model,
            )
            self.db.update_session_backend_id(new_session.id, str(backend_id))
            for message in self.db.list_messages(SessionKind.PROJECT, session.id):
                try:
                    metadata = json.loads(message.metadata_json or "{}")
                except json.JSONDecodeError:
                    metadata = {}
                self.db.add_message(
                    SessionKind.PROJECT,
                    new_session.id,
                    message.role,
                    message.content,
                    metadata,
                )
            self._refresh_navigation()
            self._project_session_selected(new_session.id)

        self.gateway.request(
            "thread/fork",
            {"threadId": session.backend_id},
            forked,
        )

    def _export_session(self, kind_value: str, session_id: int) -> None:
        kind = SessionKind(kind_value)
        try:
            session = self.db.get_session(session_id, kind)
        except KeyError:
            return
        messages = self.db.list_messages(kind, session_id)
        safe_title = "".join(
            character if character.isalnum() or character in "-_ " else "_"
            for character in session.title
        ).strip() or "JARVIS-session"
        destination, selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出会话",
            str(Path.home() / f"{safe_title}.md"),
            "Markdown (*.md);;JSON (*.json)",
        )
        if not destination:
            return
        path = Path(destination)
        if "JSON" in selected_filter or path.suffix.lower() == ".json":
            payload = {
                "title": session.title,
                "kind": kind.value,
                "messages": [
                    {
                        "role": message.role,
                        "content": message.content,
                        "metadata": message.metadata_json,
                        "created_at": message.created_at,
                    }
                    for message in messages
                ],
            }
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        else:
            lines = [f"# {session.title}", ""]
            for message in messages:
                lines.extend(
                    [
                        f"## {message.role}",
                        "",
                        message.content,
                        "",
                    ]
                )
            path.write_text("\n".join(lines), encoding="utf-8")
        self.statusBar().showMessage(f"会话已导出：{path}", 6000)

    def _toggle_nav(self) -> None:
        self.nav.setVisible(not self.nav.isVisible())
        if self.nav.isVisible():
            self.body_splitter.setSizes([250, 280 if self.explorer.isVisible() else 0, 900, 0])

    def _toggle_inspector(self) -> None:
        self.inspector.setVisible(not self.inspector.isVisible())
        if self.inspector.isVisible():
            self.inspector.set_active_tab(1)

    def _set_theme(self, theme_id: str) -> None:
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return
        apply_theme(app, theme_id)
        self.db.set_setting("app_theme", theme_id)
        self._sync_theme_actions()
        self.statusBar().showMessage(
            f"已切换主题：{current_theme().name}",
            4000,
        )

    def _show_command_palette(self) -> None:
        dialog = CommandPaletteDialog(
            list(self.commands.actions.values()),
            self,
        )
        dialog.exec()

    def _focus_conversation_search(self) -> None:
        self.nav.setVisible(True)
        self.nav.search.setFocus()
        self.nav.search.selectAll()

    def _search_conversations(self, query: str) -> None:
        if not query:
            return
        results = self.db.search_messages(query)
        if not results:
            self.statusBar().showMessage("没有找到匹配的对话内容。", 5000)
            return
        dialog = ConversationSearchDialog(query, results, self)
        dialog.sessionSelected.connect(self._select_search_result)
        dialog.exec()

    def _select_search_result(self, kind_value: str, session_id: int) -> None:
        if SessionKind(kind_value) == SessionKind.PROJECT:
            self._project_session_selected(session_id)
        else:
            self._pure_chat_selected(session_id)

    def _load_custom_themes(self) -> str:
        try:
            theme_manager.set_custom_themes(self.theme_store.load())
        except ThemeStoreError as exc:
            theme_manager.set_custom_themes([])
            return f"自定义配色加载失败：{exc}"
        return ""

    def _rebuild_theme_menu(self) -> None:
        self.theme_menu.clear()
        self.theme_actions = {}
        for theme in theme_manager.all_themes().values():
            action = QAction(theme.name, self.theme_menu)
            action.setCheckable(True)
            action.setToolTip(theme.description)
            action.triggered.connect(
                lambda _checked=False, theme_id=theme.identifier: self._set_theme(
                    theme_id
                )
            )
            self.theme_menu.addAction(action)
            self.theme_actions[theme.identifier] = action
        self.theme_menu.addSeparator()
        settings_action = QAction("配色设置…", self.theme_menu)
        settings_action.setShortcut("Ctrl+Shift+T")
        settings_action.triggered.connect(self._open_theme_editor)
        self.theme_menu.addAction(settings_action)
        self._sync_theme_actions()

    def _open_theme_editor(self) -> None:
        dialog = ThemeEditorDialog(
            theme_manager.custom_themes(),
            current_theme(),
            self.theme_store,
            self,
        )
        result = dialog.exec()
        load_error = self._load_custom_themes()
        self._rebuild_theme_menu()
        available = theme_manager.all_themes()
        if current_theme().identifier not in available:
            self._set_theme("hacker")
        if result == QDialog.DialogCode.Accepted and dialog.saved_theme:
            self._set_theme(dialog.saved_theme.identifier)
        if load_error:
            self.statusBar().showMessage(load_error, 12000)

    def _sync_theme_actions(self) -> None:
        current = current_theme().identifier
        for theme_id, action in self.theme_actions.items():
            action.setChecked(theme_id == current)

    def _theme_changed(self, _theme) -> None:  # type: ignore[no-untyped-def]
        self._update_version_label()

    def _update_version_label(self) -> None:
        theme = current_theme()
        self.version_label.setText(
            f'<span style="color:{theme.muted};">v{__version__}</span>'
            f'<span style="color:{theme.faint};"> · {DISPLAY_NAME} · Personal Harness</span>'
        )

    def _open_settings(self) -> None:
        self._open_settings_tab(0)

    def _open_settings_tab(self, tab_index: int) -> None:
        if self._busy:
            self.statusBar().showMessage(
                "当前回合运行中，完成后再修改 MCP 与 Skill 配置。",
                6000,
            )
            return
        previous_binary = self.gateway.codex_binary()
        self.codex_config = CodexConfig.load()
        context_window = int(
            self.db.get_setting("pure_chat_context_window", "0") or "0"
        )
        dialog = SettingsDialog(
            self.codex_config,
            self.gateway.codex_binary(),
            context_window,
            skill_roots=[
                self.codex_config.codex_home / "skills",
                self.codex_config.codex_home / "plugins" / "cache",
                *(
                    [self.tools_layout.skills_root]
                    if self.tools_layout is not None
                    else []
                ),
            ],
            parent=self,
        )
        dialog.tabs.setCurrentIndex(tab_index)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.db.set_setting("codex_binary", dialog.codex.text().strip())
        self.db.set_setting(
            "pure_chat_context_window",
            dialog.context_window.text().strip() or "0",
        )
        os.environ["CODEX_CIL_CODEX_BIN"] = dialog.codex.text().strip()
        self.codex_config = CodexConfig.load()
        self.pure_chat.config = self.codex_config
        binary_changed = previous_binary != dialog.codex.text().strip()
        if dialog.capabilities_changed or binary_changed:
            self._configure_offline_gateway()
            self.gateway.restart()
            self.statusBar().showMessage(
                "设置已保存，MCP 与 Skill 配置正在重新加载。",
                6000,
            )
        else:
            self.statusBar().showMessage("设置已保存", 5000)

    def _open_tools_config(self) -> None:
        if self._busy:
            self.statusBar().showMessage("当前回合运行中，完成后再配置 Tools。", 6000)
            return
        dialog = ToolsConfigDialog(
            str(self.tools_root),
            str(self.codex_config.config_path),
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            layout = inspect_tools(dialog.values())
            configure_codex_mcp(
                layout,
                self.codex_config.config_path,
                enabled=dialog.mcp_enabled.isChecked(),
            )
        except (OSError, ToolsConfigurationError) as exc:
            QMessageBox.critical(self, "数据配置失败", str(exc))
            return

        activate_tools_environment(layout)
        self.db.set_setting("tools_dir", str(layout.root))
        self._tools_warning = ""
        self.tools_layout = layout
        self.tools_root = layout.root
        self.offline_docs = OfflineDocsIndex(layout.docs_root)
        try:
            self.offline_docs.ensure_index()
        except Exception as exc:
            QMessageBox.warning(
                self,
                "离线索引",
                f"Tools 路径已保存，但离线文档索引失败：\n{exc}",
            )
        self.codex_config = CodexConfig.load()
        self.pure_chat.config = self.codex_config
        self.gateway.configure_skill_roots((layout.skills_root,))
        self._configure_offline_gateway()
        self.gateway.restart()
        self.statusBar().showMessage(
            f"数据配置已生效：{layout.root}",
            8000,
        )

    def _edit_project_instructions(self, project_id: int) -> None:
        try:
            project = self.db.get_project(project_id)
        except KeyError:
            return
        InstructionsDialog(project.name, project.path, self).exec()

    def _edit_current_project_instructions(self) -> None:
        if self.current_project:
            self._edit_project_instructions(self.current_project.id)
        else:
            QMessageBox.information(self, "项目指令", "纯对话没有项目指令。请先选择一个项目。")

    def _open_project_folder(self, project_id: int) -> None:
        try:
            project = self.db.get_project(project_id)
        except KeyError:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(project.path))

    def _open_file_external(self, path: str) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _add_file_to_chat(self, path: str) -> None:
        if self.current_kind != SessionKind.PROJECT:
            QMessageBox.information(self, "纯对话", "纯对话不读取项目文件。")
            return
        self.chat.append_attachment(path)
        self.work_tabs.setCurrentWidget(self.chat)

    def _show_usage_details(self) -> None:
        self.inspector.setVisible(True)
        self.inspector.set_active_tab(1)

    def _run_slash_command(self, command: str) -> None:
        if command == "new":
            self._new_current_session()
        elif command == "model":
            config = CodexConfig.load()
            QMessageBox.information(
                self,
                "模型配置",
                "模型参数由核心配置统一管理：\n\n"
                f"配置：{config.config_path}\n"
                f"模型：{config.model or '未配置'}\n"
                f"Provider：{config.provider.name} ({config.provider_id})\n"
                f"Base URL：{config.provider.base_url or '未配置'}\n"
                f"认证：{config.auth_description}",
            )
        elif command == "mcp":
            self._open_settings_tab(1)
        elif command == "skills":
            self._open_settings_tab(2)
        elif command == "compact":
            if self.current_session and self.current_session.backend_id:
                self.gateway.request(
                    "thread/compact/start",
                    {"threadId": self.current_session.backend_id},
                )
                self.inspector.add_activity("已请求压缩上下文")
            else:
                self.chat.add_message("system", "当前会话没有可压缩的智能核心上下文。")
        elif command == "help":
            self.chat.add_message(
                "system",
                "可用命令：/status /model /permissions /mcp /skills /review /compact /new /help",
            )
        elif command == "status":
            self._show_session_status()
        else:
            self.chat.add_message("system", f"命令 /{command} 已预留，当前版本尚未接入。")

    def _show_session_status(self) -> None:
        if self.current_session is None:
            return
        self.codex_config = CodexConfig.load()
        self.chat.add_message(
            "system",
            "\n".join(
                [
                    f"会话：{self.current_session.title}",
                    f"模式：{self.current_kind.value}",
                    f"模型：{self.codex_config.model or self.current_session.model or '配置文件默认值'}",
                    f"Provider：{self.codex_config.provider_id}",
                    f"核心状态：{self.gateway_label.text()}",
                    f"项目：{self.current_project.path if self.current_project else '无'}",
                ]
            ),
        )

    def _restyle_mode_label(self) -> None:
        self.mode_label.style().unpolish(self.mode_label)
        self.mode_label.style().polish(self.mode_label)

    def _parse_token_usage(self, raw: dict[str, Any]) -> UsageStats:
        total = raw.get("total") or {}
        last = raw.get("last") or {}
        input_tokens = int(total.get("inputTokens") or 0)
        cached_tokens = int(total.get("cachedInputTokens") or 0)
        output_tokens = int(total.get("outputTokens") or 0)
        last_input = int(last.get("inputTokens") or 0)
        last_output = int(last.get("outputTokens") or 0)
        return UsageStats(
            input_tokens=input_tokens,
            cached_input_tokens=cached_tokens,
            output_tokens=output_tokens,
            reasoning_output_tokens=int(total.get("reasoningOutputTokens") or 0),
            total_tokens=int(total.get("totalTokens") or input_tokens + output_tokens),
            context_tokens=last_input + last_output,
            context_window=raw.get("modelContextWindow"),
        )

    def _maybe_name_session(self, text: str) -> None:
        if self.current_session is None:
            return
        current = self.current_session.title.strip()
        if not (
            current in {DISPLAY_NAME, "J.A.R.V.I.S.", "新对话", "新纯对话"}
            or current.endswith("新对话")
            or current.endswith("新纯对话")
        ):
            return
        compact = " ".join(text.split())
        new_title = compact[:34] + ("…" if len(compact) > 34 else "")
        if not new_title:
            return
        self.db.update_session_title(self.current_session.id, new_title)
        self.current_session = self.db.get_session(
            self.current_session.id,
            self.current_kind,
        )
        self.chat.title.setText(new_title)
        self.chat.title.setVisible(True)
        self.inspector.set_session(
            new_title,
            self.current_kind,
            self.current_project.path if self.current_project else None,
        )
        self._refresh_navigation()
        if self.current_kind == SessionKind.PROJECT:
            self.nav.select_project_session(self.current_session.id)
        else:
            self.nav.select_pure_chat(self.current_session.id)

    def _focus_browser_or_chat(self) -> None:
        if self.work_tabs.currentWidget() is self._browser_workspace:
            self._focus_browser_address()
        else:
            self.chat.focus_composer()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        app = getattr(self, "_application", None)
        if isinstance(app, QApplication):
            app.removeEventFilter(self)
        self.gateway.stop()
        self.terminal.shutdown()
        self.db.close()
        super().closeEvent(event)
