from __future__ import annotations

import re
import shutil
from pathlib import Path

from PySide6.QtCore import QEvent, QProcess, QProcessEnvironment, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
READY_MARKER = "__CODEX_CIL_READY__"


class TerminalPanel(QFrame):
    """PowerShell 命令控制台。完整 ConPTY TUI 将在后续版本接入。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._read_output)
        self._process.finished.connect(self._process_finished)
        self._cwd = Path.cwd()
        self._history: list[str] = []
        self._history_index = 0
        self._output_buffer = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)

        header = QHBoxLayout()
        self.title = QLabel("PowerShell ·")
        self.title.setObjectName("SectionTitle")
        header.addWidget(self.title)
        header.addStretch(1)
        clear_button = QPushButton("清屏")
        clear_button.clicked.connect(self.clear)
        restart_button = QPushButton("重启")
        restart_button.clicked.connect(self.restart)
        header.addWidget(clear_button)
        header.addWidget(restart_button)
        layout.addLayout(header)

        self.output = QPlainTextEdit()
        self.output.setObjectName("TerminalOutput")
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.output, 1)

        self.input = QLineEdit()
        self.input.setPlaceholderText("输入 PowerShell 命令，Enter 执行")
        self.input.returnPressed.connect(self._execute)
        self.input.installEventFilter(self)
        layout.addWidget(self.input)

    def set_root(self, root: str | Path) -> None:
        next_root = Path(root).expanduser().resolve()
        if next_root != self._cwd:
            self._cwd = next_root
            self.restart()

    def start(self) -> None:
        if self._process.state() != QProcess.ProcessState.NotRunning:
            return
        shell = shutil.which("pwsh.exe") or shutil.which("powershell.exe") or "powershell.exe"
        env = QProcessEnvironment.systemEnvironment()
        self._process.setProcessEnvironment(env)
        self._process.setWorkingDirectory(str(self._cwd))
        self._process.start(shell, ["-NoLogo", "-NoProfile", "-NoExit", "-Command", "-"])
        self._append(f"PowerShell · {self._cwd}\n")

    def restart(self) -> None:
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()
            self._process.waitForFinished(1000)
        self.output.clear()
        self.start()

    def shutdown(self) -> None:
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()
            self._process.waitForFinished(1000)

    def clear(self) -> None:
        self.output.clear()
        self._prompt()

    def _execute(self) -> None:
        command = self.input.text().strip()
        if not command:
            return
        self.input.clear()
        self._history.append(command)
        self._history_index = len(self._history)
        if command.lower() in {"cls", "clear"}:
            self.clear()
            return
        self._append(f"\nPS {self._cwd}> {command}\n")
        if self._process.state() == QProcess.ProcessState.NotRunning:
            self.start()
        wrapped = (
            command
            + "\n"
            + f'Write-Output "{READY_MARKER}$((Get-Location).Path)"'
            + "\n"
        )
        self._process.write(wrapped.encode("utf-8"))

    def _read_output(self) -> None:
        raw = bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
        text = ANSI_RE.sub("", raw).replace("\r\n", "\n").replace("\r", "\n")
        self._output_buffer += text
        while READY_MARKER in self._output_buffer:
            before, rest = self._output_buffer.split(READY_MARKER, 1)
            if before:
                self._append(before)
            if "\n" not in rest:
                self._output_buffer = READY_MARKER + rest
                return
            path_line, self._output_buffer = rest.split("\n", 1)
            new_path = Path(path_line.strip())
            if new_path.exists():
                self._cwd = new_path
            self._prompt()
        if self._output_buffer and len(self._output_buffer) > 0:
            self._append(self._output_buffer)
            self._output_buffer = ""

    def _append(self, text: str) -> None:
        if not text:
            return
        cursor = self.output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()

    def _prompt(self) -> None:
        self.title.setText(f"PowerShell · {self._cwd}")
        self.input.setPlaceholderText(f"PS {self._cwd}>")

    def _process_finished(self, _exit_code: int, _status: QProcess.ExitStatus) -> None:
        self._append("\n[PowerShell 已退出]\n")

    def eventFilter(self, watched, event) -> bool:  # type: ignore[no-untyped-def]
        if watched is self.input and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Up and self._history:
                self._history_index = max(self._history_index - 1, 0)
                self.input.setText(self._history[self._history_index])
                return True
            if event.key() == Qt.Key.Key_Down and self._history:
                self._history_index = min(self._history_index + 1, len(self._history))
                text = (
                    ""
                    if self._history_index >= len(self._history)
                    else self._history[self._history_index]
                )
                self.input.setText(text)
                return True
        return super().eventFilter(watched, event)
