from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True, slots=True)
class ThemeSpec:
    identifier: str
    name: str
    description: str
    is_light: bool
    bg: str
    panel: str
    topbar: str
    card: str
    input_bg: str
    border: str
    border_strong: str
    text: str
    muted: str
    faint: str
    accent: str
    accent_hover: str
    accent_text: str
    accent_soft: str
    selection: str
    warning: str
    danger: str
    assistant_accent: str | None = None


THEMES: dict[str, ThemeSpec] = {
    "hacker": ThemeSpec(
        "hacker",
        "黑绿黑客",
        "黑绿霓虹终端风格",
        False,
        "#080b0a",
        "#0c120f",
        "#0b100d",
        "#0f1713",
        "#0a100d",
        "#1d3026",
        "#355c47",
        "#d8f5e5",
        "#789888",
        "#3f5c4e",
        "#31e982",
        "#238251",
        "#061009",
        "#153425",
        "#1b6b43",
        "#ffb84d",
        "#ff5f68",
    ),
    "gundam": ThemeSpec(
        "gundam",
        "高达蓝白黄",
        "白蓝装甲与黄色警示色",
        True,
        "#eef4fa",
        "#ffffff",
        "#f8fbff",
        "#ffffff",
        "#f5f9fd",
        "#c6d5e6",
        "#2b6cb0",
        "#172b4d",
        "#61758c",
        "#94a5b8",
        "#1565c0",
        "#0d47a1",
        "#ffffff",
        "#dcecff",
        "#cfe4ff",
        "#e0a800",
        "#c62828",
    ),
    "ironman": ThemeSpec(
        "ironman",
        "钢铁侠",
        "深红装甲与金色能量",
        False,
        "#0b0706",
        "#160d0b",
        "#120a08",
        "#1e1210",
        "#0e0908",
        "#4a241d",
        "#9e3c2d",
        "#fff0df",
        "#b6937d",
        "#745345",
        "#ff4b2b",
        "#ff6b3d",
        "#1a0703",
        "#3a1510",
        "#792a20",
        "#ffc247",
        "#ff3355",
    ),
    "scifi": ThemeSpec(
        "scifi",
        "科幻深空",
        "深海军蓝与青色光效",
        False,
        "#040811",
        "#081426",
        "#06101e",
        "#0b1a2e",
        "#071324",
        "#143956",
        "#1d7394",
        "#dff9ff",
        "#6fa4b8",
        "#3f6879",
        "#1de7ff",
        "#56f0ff",
        "#001116",
        "#0c3444",
        "#0d6174",
        "#ffd166",
        "#ff4d8d",
    ),
    "black_empire": ThemeSpec(
        "black_empire",
        "黑色帝国",
        "纯黑背景与纯绿终端字体",
        False,
        "#000000",
        "#000000",
        "#000000",
        "#000000",
        "#000000",
        "#0b3d1c",
        "#00ff00",
        "#00ff00",
        "#00c832",
        "#008c22",
        "#00ff00",
        "#33ff33",
        "#000000",
        "#001800",
        "#004d00",
        "#aaff00",
        "#ff4444",
    ),
    "cyberpunk2077": ThemeSpec(
        "cyberpunk2077",
        "赛博朋克2077",
        "近黑蓝城市底色、荧光黄交互与青色神经界面",
        False,
        "#070a0f",
        "#0d1119",
        "#090c12",
        "#141821",
        "#0b1018",
        "#2d3444",
        "#8a7a16",
        "#f4f1df",
        "#9e9a84",
        "#5b5a50",
        "#fcee0a",
        "#ffe95c",
        "#080800",
        "#2b2a10",
        "#6c6010",
        "#ff9d00",
        "#ff255f",
        "#00f0ff",
    ),
    "pacific_rim": ThemeSpec(
        "pacific_rim",
        "环太平洋",
        "深海机甲黑、发光青与危险警报红",
        False,
        "#03090d",
        "#07131a",
        "#050e14",
        "#0b1c25",
        "#06131a",
        "#153746",
        "#1a7187",
        "#dffbff",
        "#72a5b1",
        "#3f6670",
        "#00c8d7",
        "#3ae4ee",
        "#001416",
        "#0b3138",
        "#12616b",
        "#ffc247",
        "#ff4655",
        "#54f5e8",
    ),
    "starcraft": ThemeSpec(
        "starcraft",
        "星际争霸",
        "泰伦钢铁橙、星灵能量蓝与虫族警示色",
        False,
        "#05060a",
        "#0b0d14",
        "#08090e",
        "#12151f",
        "#0a0d15",
        "#283046",
        "#465a88",
        "#edf2ff",
        "#8f9bb5",
        "#58647d",
        "#f0a02a",
        "#ffc45e",
        "#160b00",
        "#33220e",
        "#704413",
        "#ffcf4a",
        "#ff3d71",
        "#56d8ff",
    ),
    "retro_crt": ThemeSpec(
        "retro_crt",
        "复古终端",
        "琥珀 CRT 扫描线与淡绿荧光字符",
        False,
        "#140f08",
        "#1c150c",
        "#110d07",
        "#241b0f",
        "#171107",
        "#4c3518",
        "#8a6224",
        "#ffd58a",
        "#bd8f4f",
        "#765633",
        "#ffb000",
        "#ffd166",
        "#1c1200",
        "#3a2608",
        "#765000",
        "#ffcf4a",
        "#ff5b45",
        "#9cff7a",
    ),
}


class ThemeManager(QObject):
    changed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._current = THEMES["hacker"]
        self._custom_themes: dict[str, ThemeSpec] = {}

    @property
    def current(self) -> ThemeSpec:
        return self._current

    def set_custom_themes(self, themes: list[ThemeSpec]) -> None:
        self._custom_themes = {
            theme.identifier: theme for theme in themes
        }

    def all_themes(self) -> dict[str, ThemeSpec]:
        return {**THEMES, **self._custom_themes}

    def custom_themes(self) -> list[ThemeSpec]:
        return list(self._custom_themes.values())

    def get(self, identifier: str) -> ThemeSpec:
        return self.all_themes().get(identifier, THEMES["hacker"])

    def apply(self, app: QApplication, identifier: str) -> ThemeSpec:
        spec = self.get(identifier)
        app.setStyle("Fusion")
        app.setPalette(build_palette(spec))
        app.setStyleSheet(build_stylesheet(spec))
        self._current = spec
        self.changed.emit(spec)
        return spec


theme_manager = ThemeManager()


def current_theme() -> ThemeSpec:
    return theme_manager.current


def build_palette(spec: ThemeSpec) -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(spec.bg))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(spec.text))
    palette.setColor(QPalette.ColorRole.Base, QColor(spec.input_bg))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(spec.card))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(spec.card))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(spec.text))
    palette.setColor(QPalette.ColorRole.Text, QColor(spec.text))
    palette.setColor(QPalette.ColorRole.Button, QColor(spec.card))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(spec.text))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(spec.danger))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(spec.selection))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(spec.text))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(spec.faint))
    return palette


STYLE_TEMPLATE = """
* {
    font-family: "Microsoft YaHei UI", "Segoe UI";
    font-size: 13px;
    color: @TEXT@;
}
QMainWindow, QWidget#AppRoot, QDialog, QMessageBox, QInputDialog {
    background: @BG@;
    color: @TEXT@;
}
QDialog QLabel, QMessageBox QLabel, QInputDialog QLabel {
    color: @TEXT@;
}
QDialog QPlainTextEdit, QDialog QTextEdit, QDialog QTextBrowser {
    background: @INPUT@;
    border: 1px solid @BORDER@;
    border-radius: 6px;
}
QMenuBar {
    background: @BG@;
    color: @TEXT@;
    border-bottom: 1px solid @BORDER@;
}
QMenuBar::item {
    background: transparent;
    color: @TEXT@;
    padding: 4px 9px;
}
QMenuBar::item:selected, QMenuBar::item:pressed {
    background: @ACCENT_SOFT@;
    color: @ACCENT@;
}
QWidget#TopBar {
    background: @TOPBAR@;
    border-bottom: 1px solid @BORDER@;
}
QLabel#Brand {
    color: @ACCENT@;
    font-family: "Microsoft YaHei UI", "Segoe UI";
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 2px;
}
QLabel#ChatBrand {
    color: @ACCENT@;
    font-family: "Microsoft YaHei UI", "Segoe UI";
    font-size: 21px;
    font-weight: 700;
    letter-spacing: 2px;
}
QLabel#Muted, QLabel[muted="true"] {
    color: @MUTED@;
}
QLabel#Faint {
    color: @FAINT@;
}
QLabel#SectionTitle {
    color: @MUTED@;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
}
QLabel#Pill {
    background: @ACCENT_SOFT@;
    border: 1px solid @BORDER_STRONG@;
    border-radius: 10px;
    color: @ACCENT@;
    padding: 3px 9px;
}
QLabel#PillWarning {
    background: @CARD@;
    border: 1px solid @WARNING@;
    border-radius: 10px;
    color: @WARNING@;
    padding: 3px 9px;
}
QLabel[severity="danger"] {
    color: @DANGER@;
}
QFrame#Panel {
    background: @PANEL@;
    border: 1px solid @BORDER@;
}
QFrame#Card {
    background: @CARD@;
    border: 1px solid @BORDER@;
    border-radius: 8px;
}
QFrame#UsageCard {
    background: @CARD@;
    border: 1px solid @BORDER@;
    border-radius: 9px;
}
QTreeView, QListWidget, QListView, QPlainTextEdit, QTextBrowser {
    background: transparent;
    border: 0;
    selection-background-color: @SELECTION@;
    selection-color: @TEXT@;
}
QTreeView::item, QListWidget::item {
    min-height: 26px;
    padding: 2px 5px;
    border-radius: 5px;
}
QTreeView::item:hover, QListWidget::item:hover {
    background: @ACCENT_SOFT@;
}
QLineEdit {
    background: @INPUT@;
    border: 1px solid @BORDER@;
    border-radius: 6px;
    padding: 7px 9px;
    selection-background-color: @SELECTION@;
}
QLineEdit:focus, QPlainTextEdit:focus {
    border-color: @ACCENT@;
}
QPlainTextEdit#Composer {
    background: @INPUT@;
    border: 1px solid @BORDER_STRONG@;
    border-radius: 8px;
    padding: 9px;
    font-size: 14px;
}
QTextBrowser#Transcript {
    background: @BG@;
    border: 1px solid @BORDER@;
    border-radius: 8px;
    padding: 10px;
    font-size: 14px;
}
QPlainTextEdit#TerminalOutput, QPlainTextEdit#CodeEditor {
    background: @INPUT@;
    border: 1px solid @BORDER@;
    border-radius: 6px;
    padding: 7px;
    font-family: "Cascadia Code", "Consolas";
}
QPushButton, QToolButton {
    background: @CARD@;
    border: 1px solid @BORDER@;
    border-radius: 6px;
    padding: 6px 10px;
}
QPushButton:hover, QToolButton:hover {
    background: @ACCENT_SOFT@;
    border-color: @BORDER_STRONG@;
}
QPushButton:pressed, QToolButton:pressed {
    background: @SELECTION@;
}
QPushButton:checked, QToolButton:checked {
    background: @ACCENT_SOFT@;
    border-color: @ACCENT@;
    color: @ACCENT@;
    font-weight: 600;
}
QPushButton#Primary {
    background: @ACCENT@;
    border-color: @ACCENT@;
    color: @ACCENT_TEXT@;
    font-weight: 600;
}
QPushButton#Primary:hover {
    background: @ACCENT_HOVER@;
}
QPushButton#Danger {
    border-color: @DANGER@;
    color: @DANGER@;
}
QTabWidget::pane {
    border: 1px solid @BORDER@;
    background: @BG@;
}
QTabBar::tab {
    background: @PANEL@;
    border: 1px solid @BORDER@;
    border-bottom: 0;
    padding: 7px 15px;
    margin-right: 2px;
    color: @MUTED@;
}
QTabBar::tab:selected {
    background: @CARD@;
    color: @ACCENT@;
}
QSplitter::handle {
    background: @BORDER@;
    width: 1px;
    height: 1px;
}
QSplitter::handle:hover {
    background: @ACCENT@;
}
QScrollBar:vertical {
    background: transparent;
    width: 9px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: @BORDER_STRONG@;
    min-height: 30px;
    border-radius: 4px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QComboBox {
    background: @INPUT@;
    border: 1px solid @BORDER@;
    border-radius: 6px;
    padding: 6px 9px;
}
QComboBox QAbstractItemView {
    background: @CARD@;
    border: 1px solid @BORDER_STRONG@;
    selection-background-color: @SELECTION@;
}
QMenu {
    background: @CARD@;
    border: 1px solid @BORDER_STRONG@;
    padding: 5px;
}
QMenu::item {
    padding: 7px 24px 7px 10px;
    border-radius: 4px;
}
QMenu::item:selected {
    background: @ACCENT_SOFT@;
    color: @ACCENT@;
}
QToolTip {
    background: @CARD@;
    color: @TEXT@;
    border: 1px solid @BORDER_STRONG@;
    border-radius: 5px;
    padding: 5px 8px;
}
QStatusBar {
    background: @TOPBAR@;
    border-top: 1px solid @BORDER@;
    color: @MUTED@;
}
QLabel#VersionLabel {
    font-size: 11px;
    padding: 0 8px;
    background: transparent;
}
QDockWidget {
    color: @MUTED@;
}
QDockWidget::title {
    background: @TOPBAR@;
    border: 1px solid @BORDER@;
    padding: 6px;
}
"""


def build_stylesheet(spec: ThemeSpec) -> str:
    values = {
        "@BG@": spec.bg,
        "@PANEL@": spec.panel,
        "@TOPBAR@": spec.topbar,
        "@CARD@": spec.card,
        "@INPUT@": spec.input_bg,
        "@BORDER@": spec.border,
        "@BORDER_STRONG@": spec.border_strong,
        "@TEXT@": spec.text,
        "@MUTED@": spec.muted,
        "@FAINT@": spec.faint,
        "@ACCENT@": spec.accent,
        "@ACCENT_HOVER@": spec.accent_hover,
        "@ACCENT_TEXT@": spec.accent_text,
        "@ACCENT_SOFT@": spec.accent_soft,
        "@SELECTION@": spec.selection,
        "@WARNING@": spec.warning,
        "@DANGER@": spec.danger,
    }
    stylesheet = STYLE_TEMPLATE
    for token, value in values.items():
        stylesheet = stylesheet.replace(token, value)
    return stylesheet


def apply_theme(app: QApplication, identifier: str) -> ThemeSpec:
    return theme_manager.apply(app, identifier)
