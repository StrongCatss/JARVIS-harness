from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import (
    QDir,
    QMimeData,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileSystemModel,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)


class WorkspaceFilterModel(QSortFilterProxyModel):
    _IGNORED_NAMES = {
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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setRecursiveFilteringEnabled(True)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        index = model.index(source_row, 0, source_parent)
        name = model.fileName(index)
        if name in self._IGNORED_NAMES:
            return False
        return super().filterAcceptsRow(source_row, source_parent)


class ExplorerPanel(QFrame):
    fileActivated = Signal(str)
    addFileToChatRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.setMinimumWidth(240)
        self.setMaximumWidth(380)
        self._root: Path | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 9, 7, 7)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("资源管理器")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        refresh_button = QPushButton("↻")
        refresh_button.setFixedSize(26, 24)
        refresh_button.setToolTip("刷新文件树")
        refresh_button.clicked.connect(self.refresh)
        header.addWidget(refresh_button)
        layout.addLayout(header)

        self.path_label = QLabel("未选择项目")
        self.path_label.setObjectName("Muted")
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)

        self.search = QLineEdit()
        self.search.setPlaceholderText("筛选文件…")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)

        self.model = QFileSystemModel(self)
        self.model.setFilter(
            QDir.Filter.AllDirs
            | QDir.Filter.Files
            | QDir.Filter.NoDotAndDotDot
        )
        self.proxy = WorkspaceFilterModel(self)
        self.proxy.setSourceModel(self.model)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)

        self.tree = QTreeView()
        self.tree.setModel(self.proxy)
        self.tree.setHeaderHidden(True)
        self.tree.setAnimated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(15)
        self.tree.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.doubleClicked.connect(self._activation_index)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        for column in range(1, 4):
            self.tree.hideColumn(column)
        layout.addWidget(self.tree, 1)

        self.copy_action = QAction("复制", self.tree)
        self.copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        self.copy_action.setShortcutContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self.copy_action.triggered.connect(self.copy_selection)
        self.paste_action = QAction("粘贴", self.tree)
        self.paste_action.setShortcut(QKeySequence.StandardKey.Paste)
        self.paste_action.setShortcutContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self.paste_action.triggered.connect(self.paste_clipboard)
        self.delete_action = QAction("删除", self.tree)
        self.delete_action.setShortcut(QKeySequence.StandardKey.Delete)
        self.delete_action.setShortcutContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self.delete_action.triggered.connect(self.delete_selection)
        self.new_folder_action = QAction("新建文件夹", self.tree)
        self.new_folder_action.triggered.connect(self.create_folder)
        for action in (
            self.copy_action,
            self.paste_action,
            self.delete_action,
            self.new_folder_action,
        ):
            self.tree.addAction(action)

    @property
    def root(self) -> Path | None:
        return self._root

    def set_root(self, path: str | Path | None) -> None:
        if path is None:
            self._root = None
            self.path_label.setText("未选择项目")
            self.tree.setModel(None)
            return
        root = Path(path).expanduser().resolve()
        self._root = root
        self.path_label.setText(str(root))
        source_index = self.model.setRootPath(str(root))
        proxy_index = self.proxy.mapFromSource(source_index)
        self.tree.setRootIndex(proxy_index)
        self.tree.setModel(self.proxy)

    def refresh(self) -> None:
        if self._root:
            current = self._root
            self.set_root(None)
            self.set_root(current)

    def _activation_index(self, proxy_index: QModelIndex) -> None:
        source_index = self.proxy.mapToSource(proxy_index)
        path = Path(self.model.filePath(source_index))
        if path.is_file():
            self.fileActivated.emit(str(path))

    def _context_menu(self, position) -> None:  # type: ignore[no-untyped-def]
        proxy_index = self.tree.indexAt(position)
        path = self._path_for_index(proxy_index)
        menu = QMenu(self)
        if path is not None and path.is_file():
            add_to_chat = QAction("加入当前项目对话", menu)
            add_to_chat.triggered.connect(
                lambda: self.addFileToChatRequested.emit(str(path))
            )
            menu.addAction(add_to_chat)
        if path is not None:
            open_action = QAction("使用系统程序打开", menu)
            open_action.triggered.connect(
                lambda: os.startfile(str(path))  # type: ignore[attr-defined]
            )
            reveal_action = QAction("在资源管理器中显示", menu)
            reveal_action.triggered.connect(lambda: self._reveal(path))
            menu.addAction(open_action)
            menu.addAction(reveal_action)
            menu.addSeparator()
            menu.addAction(self.copy_action)
            menu.addAction(self.delete_action)
        else:
            menu.addAction(self.paste_action)
        menu.addSeparator()
        menu.addAction(self.new_folder_action)
        self.paste_action.setEnabled(self._clipboard_paths() != [])
        self.delete_action.setEnabled(path is not None)
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def copy_selection(self) -> None:
        paths = self.selected_paths()
        if not paths:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
        QGuiApplication.clipboard().setMimeData(mime)

    def paste_clipboard(self) -> None:
        target = self._paste_target()
        sources = self._clipboard_paths()
        if target is None or not sources:
            return
        try:
            for source in sources:
                _copy_path_to_directory(source, target)
        except OSError as exc:
            QMessageBox.warning(self, "粘贴失败", str(exc))
            return
        self.refresh()

    def delete_selection(self) -> None:
        paths = self.selected_paths()
        if not paths:
            return
        names = "\n".join(path.name for path in paths[:8])
        if len(paths) > 8:
            names += f"\n……以及另外 {len(paths) - 8} 项"
        answer = QMessageBox.question(
            self,
            "删除文件",
            f"将以下内容移入回收站？\n\n{names}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            _move_paths_to_recycle_bin(paths)
        except OSError as exc:
            QMessageBox.warning(self, "删除失败", str(exc))
            return
        self.refresh()

    def create_folder(self) -> None:
        target = self._paste_target()
        if target is None:
            return
        name, accepted = QInputDialog.getText(
            self,
            "新建文件夹",
            f"在 {target} 中创建文件夹：",
        )
        clean = name.strip()
        if not accepted or not clean:
            return
        if clean in {".", ".."} or any(
            character in clean for character in '\\/:*?"<>|'
        ):
            QMessageBox.warning(self, "新建文件夹", "文件夹名称包含无效字符。")
            return
        folder = target / clean
        try:
            folder.mkdir()
        except FileExistsError:
            QMessageBox.warning(self, "新建文件夹", "同名文件或文件夹已经存在。")
            return
        except OSError as exc:
            QMessageBox.warning(self, "新建文件夹失败", str(exc))
            return
        self.refresh()

    def selected_paths(self) -> list[Path]:
        paths: list[Path] = []
        for proxy_index in self.tree.selectionModel().selectedRows(0):
            path = self._path_for_index(proxy_index)
            if path is not None and path.exists():
                paths.append(path)
        return paths

    def _path_for_index(self, proxy_index: QModelIndex) -> Path | None:
        if not proxy_index.isValid():
            return None
        source_index = self.proxy.mapToSource(proxy_index)
        raw = self.model.filePath(source_index)
        return Path(raw) if raw else None

    def _paste_target(self) -> Path | None:
        selected = self.tree.currentIndex()
        path = self._path_for_index(selected)
        if path is None:
            return self._root
        return path if path.is_dir() else path.parent

    @staticmethod
    def _clipboard_paths() -> list[Path]:
        mime = QGuiApplication.clipboard().mimeData()
        if mime is None or not mime.hasUrls():
            return []
        return [
            Path(url.toLocalFile())
            for url in mime.urls()
            if url.isLocalFile() and Path(url.toLocalFile()).exists()
        ]

    @staticmethod
    def _reveal(path: Path) -> None:
        if path.is_dir():
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["explorer.exe", "/select,", str(path)])


def _copy_path_to_directory(source: Path, target: Path) -> Path:
    source = source.resolve()
    target = target.resolve()
    if source.is_dir():
        try:
            target.relative_to(source)
        except ValueError:
            pass
        else:
            raise OSError("不能把文件夹粘贴到它自身或子目录中。")
    destination = _unique_destination(
        target / source.name,
        is_directory=source.is_dir(),
    )
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        shutil.copy2(source, destination)
    return destination


def _unique_destination(path: Path, *, is_directory: bool = False) -> Path:
    if not path.exists():
        return path
    stem = path.name if is_directory else path.stem
    suffix = "" if is_directory else path.suffix
    for index in range(1, 10_000):
        label = " - 复制" if index == 1 else f" - 复制 ({index})"
        candidate = path.with_name(f"{stem}{label}{suffix}")
        if not candidate.exists():
            return candidate
    raise OSError(f"无法为 {path.name} 生成可用的目标名称。")


def _move_paths_to_recycle_bin(paths: list[Path]) -> None:
    if os.name != "nt":
        raise OSError("当前系统不支持回收站删除。")

    class SHFileOpStructW(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.wintypes.HWND),
            ("wFunc", ctypes.wintypes.UINT),
            ("pFrom", ctypes.wintypes.LPCWSTR),
            ("pTo", ctypes.wintypes.LPCWSTR),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", ctypes.wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.wintypes.LPCWSTR),
        ]

    operation = SHFileOpStructW()
    operation.hwnd = None
    operation.wFunc = 3
    operation.pFrom = "\0".join(str(path.resolve()) for path in paths) + "\0\0"
    operation.pTo = None
    operation.fFlags = 0x0040 | 0x0010 | 0x0004
    result = ctypes.windll.shell32.SHFileOperationW(  # type: ignore[attr-defined]
        ctypes.byref(operation)
    )
    if result != 0:
        raise OSError(f"Windows 回收站操作失败，错误代码 {result}。")
