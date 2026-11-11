from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from codex_cil.models import Project, Session, SessionKind


class SectionHeader(QWidget):
    addRequested = Signal()

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 5, 5, 3)
        self.title = QLabel(title)
        self.title.setObjectName("SectionTitle")
        layout.addWidget(self.title)
        layout.addStretch(1)
        button = QPushButton("+")
        button.setFixedSize(26, 24)
        button.setToolTip(f"新建{title}")
        button.clicked.connect(self.addRequested)
        layout.addWidget(button)

    def set_count(self, count: int) -> None:
        base = self.title.text().split("  ")[0]
        self.title.setText(f"{base}  {count}")


class NavPanel(QFrame):
    projectActivated = Signal(int)
    projectSessionActivated = Signal(int)
    pureChatActivated = Signal(int)
    addProjectRequested = Signal()
    addProjectSessionRequested = Signal(int)
    addPureChatRequested = Signal()
    editInstructionsRequested = Signal(int)
    openProjectFolderRequested = Signal(int)
    deleteProjectRequested = Signal(int)
    deleteProjectSessionRequested = Signal(int)
    deletePureChatRequested = Signal(int)
    renameSessionRequested = Signal(str, int)
    forkProjectSessionRequested = Signal(int)
    exportSessionRequested = Signal(str, int)
    setPinnedRequested = Signal(str, int, bool)
    setArchivedRequested = Signal(str, int, bool)
    archivedVisibilityChanged = Signal(bool)
    conversationSearchRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.setMinimumWidth(225)
        self.setMaximumWidth(340)
        root = QVBoxLayout(self)
        root.setContentsMargins(7, 9, 7, 7)
        root.setSpacing(7)

        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索会话…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.search.returnPressed.connect(
            lambda: self.conversationSearchRequested.emit(self.search.text().strip())
        )
        self.archive_toggle = QPushButton("归档")
        self.archive_toggle.setCheckable(True)
        self.archive_toggle.setToolTip("显示或隐藏已归档会话")
        self.archive_toggle.toggled.connect(self._toggle_archived)
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.archive_toggle)
        root.addLayout(search_row)

        self.projects_header = SectionHeader("项目")
        self.projects_header.addRequested.connect(self.addProjectRequested)
        root.addWidget(self.projects_header)

        self.project_tree = QTreeWidget()
        self.project_tree.setHeaderHidden(True)
        self.project_tree.setIndentation(14)
        self.project_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.project_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.project_tree.itemClicked.connect(self._project_item_clicked)
        self.project_tree.customContextMenuRequested.connect(self._project_context_menu)
        root.addWidget(self.project_tree, 3)

        self.chats_header = SectionHeader("纯对话")
        self.chats_header.addRequested.connect(self.addPureChatRequested)
        root.addWidget(self.chats_header)

        self.chat_list = QListWidget()
        self.chat_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.chat_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chat_list.itemClicked.connect(self._chat_item_clicked)
        self.chat_list.customContextMenuRequested.connect(self._chat_context_menu)
        root.addWidget(self.chat_list, 2)

        self._projects: dict[int, Project] = {}
        self._sessions: dict[int, Session] = {}
        self._session_kinds: dict[int, SessionKind] = {}
        self._archived_visible = False

    def refresh(
        self,
        projects: Iterable[Project],
        sessions: dict[int, list[Session]],
        pure_chats: list[Session],
    ) -> None:
        self._projects = {project.id: project for project in projects}
        self._sessions = {}
        self._session_kinds = {}
        self.project_tree.clear()
        self.chat_list.clear()

        project_list = list(projects)
        self.projects_header.set_count(len(project_list))
        for project in project_list:
            item = QTreeWidgetItem([project.name])
            item.setData(0, Qt.ItemDataRole.UserRole, ("project", project.id))
            item.setToolTip(0, project.path)
            item.setExpanded(True)
            self.project_tree.addTopLevelItem(item)
            for session in sessions.get(project.id, []):
                self._sessions[session.id] = session
                self._session_kinds[session.id] = SessionKind.PROJECT
                child = QTreeWidgetItem([self._session_title(session)])
                child.setData(0, Qt.ItemDataRole.UserRole, ("session", session.id))
                child.setToolTip(0, session.title)
                item.addChild(child)

        self.chats_header.set_count(len(pure_chats))
        for chat in pure_chats:
            self._sessions[chat.id] = chat
            self._session_kinds[chat.id] = SessionKind.PURE_CHAT
            item = QListWidgetItem(self._session_title(chat))
            item.setData(Qt.ItemDataRole.UserRole, chat.id)
            item.setToolTip(chat.title)
            self.chat_list.addItem(item)
        self._apply_filter(self.search.text())

    @property
    def archived_visible(self) -> bool:
        return self._archived_visible

    def select_project_session(self, session_id: int) -> None:
        for index in range(self.project_tree.topLevelItemCount()):
            project_item = self.project_tree.topLevelItem(index)
            for child_index in range(project_item.childCount()):
                child = project_item.child(child_index)
                payload = child.data(0, Qt.ItemDataRole.UserRole)
                if payload == ("session", session_id):
                    self.project_tree.setCurrentItem(child)
                    return

    def select_pure_chat(self, session_id: int) -> None:
        for index in range(self.chat_list.count()):
            item = self.chat_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == session_id:
                self.chat_list.setCurrentItem(item)
                return

    def current_project(self) -> Project | None:
        item = self.project_tree.currentItem()
        if item is None:
            return None
        payload = item.data(0, Qt.ItemDataRole.UserRole)
        if payload and payload[0] == "session":
            session = self._sessions.get(payload[1])
            return self._projects.get(session.project_id) if session else None
        if payload and payload[0] == "project":
            return self._projects.get(payload[1])
        return None

    def _project_item_clicked(self, item: QTreeWidgetItem) -> None:
        payload = item.data(0, Qt.ItemDataRole.UserRole)
        if not payload:
            return
        if payload[0] == "project":
            self.projectActivated.emit(payload[1])
        elif payload[0] == "session":
            self.projectSessionActivated.emit(payload[1])

    def _chat_item_clicked(self, item: QListWidgetItem) -> None:
        self.pureChatActivated.emit(int(item.data(Qt.ItemDataRole.UserRole)))

    def _project_context_menu(self, position) -> None:  # type: ignore[no-untyped-def]
        item = self.project_tree.itemAt(position)
        if item is None:
            return
        payload = item.data(0, Qt.ItemDataRole.UserRole)
        if not payload:
            return
        project_id: int | None = None
        if payload[0] == "project":
            project_id = payload[1]
        elif payload[0] == "session":
            session = self._sessions.get(payload[1])
            project_id = session.project_id if session else None
        if project_id is None:
            return

        menu = QMenu(self)
        if payload[0] == "project":
            new_session = QAction("新建项目会话", menu)
            new_session.triggered.connect(
                lambda: self.addProjectSessionRequested.emit(project_id)
            )
            instructions = QAction("编辑项目指令 AGENTS.md", menu)
            instructions.triggered.connect(
                lambda: self.editInstructionsRequested.emit(project_id)
            )
            folder = QAction("打开项目文件夹", menu)
            folder.triggered.connect(
                lambda: self.openProjectFolderRequested.emit(project_id)
            )
            delete_project = QAction("删除项目…", menu)
            delete_project.triggered.connect(
                lambda: self.deleteProjectRequested.emit(project_id)
            )
            menu.addAction(new_session)
            menu.addAction(instructions)
            menu.addAction(folder)
            menu.addSeparator()
            menu.addAction(delete_project)
        else:
            rename_session = QAction("重命名会话…", menu)
            rename_session.triggered.connect(
                lambda: self.renameSessionRequested.emit(
                    SessionKind.PROJECT.value,
                    payload[1],
                )
            )
            fork_session = QAction("从此会话创建分支", menu)
            fork_session.triggered.connect(
                lambda: self.forkProjectSessionRequested.emit(payload[1])
            )
            export_session = QAction("导出会话…", menu)
            export_session.triggered.connect(
                lambda: self.exportSessionRequested.emit(
                    SessionKind.PROJECT.value,
                    payload[1],
                )
            )
            session = self._sessions.get(payload[1])
            pin_session = QAction(
                "取消置顶" if session and session.pinned else "置顶会话",
                menu,
            )
            pin_session.triggered.connect(
                lambda: self.setPinnedRequested.emit(
                    SessionKind.PROJECT.value,
                    payload[1],
                    not bool(session and session.pinned),
                )
            )
            archive_session = QAction(
                "取消归档" if session and session.archived else "归档会话",
                menu,
            )
            archive_session.triggered.connect(
                lambda: self.setArchivedRequested.emit(
                    SessionKind.PROJECT.value,
                    payload[1],
                    not bool(session and session.archived),
                )
            )
            delete_session = QAction("删除会话…", menu)
            delete_session.triggered.connect(
                lambda: self.deleteProjectSessionRequested.emit(payload[1])
            )
            menu.addAction(rename_session)
            menu.addAction(pin_session)
            menu.addAction(archive_session)
            menu.addAction(fork_session)
            menu.addAction(export_session)
            menu.addSeparator()
            menu.addAction(delete_session)
        menu.exec(self.project_tree.viewport().mapToGlobal(position))

    def _chat_context_menu(self, position) -> None:  # type: ignore[no-untyped-def]
        item = self.chat_list.itemAt(position)
        if item is None:
            return
        session_id = int(item.data(Qt.ItemDataRole.UserRole))
        menu = QMenu(self)
        rename_chat = QAction("重命名纯对话…", menu)
        rename_chat.triggered.connect(
            lambda: self.renameSessionRequested.emit(
                SessionKind.PURE_CHAT.value,
                session_id,
            )
        )
        export_chat = QAction("导出纯对话…", menu)
        export_chat.triggered.connect(
            lambda: self.exportSessionRequested.emit(
                SessionKind.PURE_CHAT.value,
                session_id,
            )
        )
        session = self._sessions.get(session_id)
        pin_chat = QAction(
            "取消置顶" if session and session.pinned else "置顶纯对话",
            menu,
        )
        pin_chat.triggered.connect(
            lambda: self.setPinnedRequested.emit(
                SessionKind.PURE_CHAT.value,
                session_id,
                not bool(session and session.pinned),
            )
        )
        archive_chat = QAction(
            "取消归档" if session and session.archived else "归档纯对话",
            menu,
        )
        archive_chat.triggered.connect(
            lambda: self.setArchivedRequested.emit(
                SessionKind.PURE_CHAT.value,
                session_id,
                not bool(session and session.archived),
            )
        )
        delete_chat = QAction("删除纯对话…", menu)
        delete_chat.triggered.connect(
            lambda: self.deletePureChatRequested.emit(session_id)
        )
        menu.addAction(rename_chat)
        menu.addAction(pin_chat)
        menu.addAction(archive_chat)
        menu.addAction(export_chat)
        menu.addSeparator()
        menu.addAction(delete_chat)
        menu.exec(self.chat_list.viewport().mapToGlobal(position))

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for project_index in range(self.project_tree.topLevelItemCount()):
            project_item = self.project_tree.topLevelItem(project_index)
            project_name = project_item.text(0).lower()
            visible_children = 0
            for child_index in range(project_item.childCount()):
                child = project_item.child(child_index)
                visible = not needle or needle in project_name or needle in child.text(0).lower()
                child.setHidden(not visible)
                if visible:
                    visible_children += 1
            project_item.setHidden(
                bool(needle)
                and needle not in project_name
                and visible_children == 0
            )
        for index in range(self.chat_list.count()):
            item = self.chat_list.item(index)
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def _toggle_archived(self, visible: bool) -> None:
        self._archived_visible = visible
        self.archivedVisibilityChanged.emit(visible)

    @staticmethod
    def _session_title(session: Session) -> str:
        prefix = ""
        if session.pinned:
            prefix += "★ "
        if session.archived:
            prefix += "[归档] "
        return prefix + session.title
