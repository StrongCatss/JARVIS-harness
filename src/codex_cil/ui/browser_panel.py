from __future__ import annotations

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

DEFAULT_BROWSER_URL = "https://www.baidu.com"


class BrowserTab(QWidget):
    titleChanged = Signal(str)
    closeRequested = Signal()

    def __init__(self, url: str = "about:blank", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        toolbar = QFrame()
        toolbar.setObjectName("Panel")
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(6, 5, 6, 5)
        bar.setSpacing(5)

        self.back_button = QToolButton()
        self.back_button.setText("←")
        self.back_button.setToolTip("后退")
        self.forward_button = QToolButton()
        self.forward_button.setText("→")
        self.forward_button.setToolTip("前进")
        self.reload_button = QToolButton()
        self.reload_button.setText("↻")
        self.reload_button.setToolTip("刷新")
        self.address = QLineEdit()
        self.address.setPlaceholderText("输入网址或 localhost 地址")
        self.go_button = QPushButton("打开")
        self.close_button = QToolButton()
        self.close_button.setText("×")
        self.close_button.setToolTip("关闭浏览器")
        for widget in (
            self.back_button,
            self.forward_button,
            self.reload_button,
            self.address,
            self.go_button,
            self.close_button,
        ):
            bar.addWidget(widget, 1 if widget is self.address else 0)
        layout.addWidget(toolbar)

        self.view = QWebEngineView()
        layout.addWidget(self.view, 1)

        self.back_button.clicked.connect(self.view.back)
        self.forward_button.clicked.connect(self.view.forward)
        self.reload_button.clicked.connect(self.view.reload)
        self.go_button.clicked.connect(self.navigate)
        self.close_button.clicked.connect(lambda: self.closeRequested.emit())
        self.address.returnPressed.connect(self.navigate)
        self.view.urlChanged.connect(self._url_changed)
        self.view.titleChanged.connect(self._title_changed)
        self.view.loadStarted.connect(lambda: self.reload_button.setText("×"))
        self.view.loadFinished.connect(lambda _ok: self.reload_button.setText("↻"))
        self.view.setUrl(QUrl.fromUserInput(url))

    def navigate(self) -> None:
        text = self.address.text().strip()
        if not text:
            return
        self.view.setUrl(QUrl.fromUserInput(text))

    def focus_address(self) -> None:
        self.address.setFocus()
        self.address.selectAll()

    def _url_changed(self, url: QUrl) -> None:
        self.address.setText(url.toString())
        self.back_button.setEnabled(self.view.history().canGoBack())
        self.forward_button.setEnabled(self.view.history().canGoForward())

    def _title_changed(self, title: str) -> None:
        self.titleChanged.emit(title or "新标签页")


class BrowserWorkspace(QFrame):
    closeRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        layout.addWidget(self.tabs, 1)
        self.new_tab()

    @property
    def current_browser(self) -> BrowserTab | None:
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, BrowserTab) else None

    def new_tab(self, url: str = DEFAULT_BROWSER_URL) -> BrowserTab:
        browser = BrowserTab(url)
        index = self.tabs.addTab(browser, "新标签页")
        self.tabs.setCurrentIndex(index)
        browser.titleChanged.connect(
            lambda title, b=browser: self._update_title(b, title)
        )
        browser.closeRequested.connect(lambda: self.closeRequested.emit())
        return browser

    def open_url(self, url: str = DEFAULT_BROWSER_URL) -> None:
        browser = self.current_browser
        if browser is None:
            browser = self.new_tab(url)
        else:
            browser.view.setUrl(QUrl.fromUserInput(url))
        self.tabs.setCurrentWidget(browser)

    def focus_address(self) -> None:
        browser = self.current_browser
        if browser is None:
            browser = self.new_tab()
        browser.focus_address()

    def close_tab(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if widget is None:
            return
        self.tabs.removeTab(index)
        widget.deleteLater()
        if self.tabs.count() == 0:
            self.new_tab()

    def _update_title(self, browser: BrowserTab, title: str) -> None:
        index = self.tabs.indexOf(browser)
        if index >= 0:
            self.tabs.setTabText(index, title[:28])
            self.tabs.setTabToolTip(index, title)
