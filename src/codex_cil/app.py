from __future__ import annotations

import argparse
import os
import sys

from PySide6.QtCore import QCoreApplication, QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from codex_cil import DISPLAY_NAME, __version__
from codex_cil.database import Database
from codex_cil.ui.main_window import MainWindow
from codex_cil.ui.theme_system import apply_theme


def create_application(argv: list[str] | None = None) -> QApplication:
    QCoreApplication.setApplicationName(DISPLAY_NAME)
    QCoreApplication.setApplicationVersion(__version__)
    QCoreApplication.setOrganizationName("JARVIS")
    app = QApplication(argv or sys.argv)
    apply_theme(app, "hacker")
    app.setFont(QFont("Microsoft YaHei UI", 9))
    return app


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="贾维斯个人桌面管家")
    parser.add_argument(
        "--version",
        action="store_true",
        help="打印版本号并退出",
    )
    parser.add_argument(
        "--smoke-test",
        nargs="?",
        const=3500,
        type=int,
        metavar="MILLISECONDS",
        help="启动主窗口和浏览器后在指定时间退出，用于打包验证",
    )
    return parser.parse_known_args(argv)[0]


def main() -> None:
    args = parse_args()
    if args.version:
        print(__version__)
        return
    if os.name == "nt":
        os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = create_application()
    database = Database()
    window = MainWindow(database)
    window.show()
    if args.smoke_test is not None:
        QTimer.singleShot(800, window._open_browser)
        QTimer.singleShot(max(args.smoke_test, 1500), window.close)
    raise SystemExit(app.exec())
