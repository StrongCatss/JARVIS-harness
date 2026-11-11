from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from codex_cil import DISPLAY_NAME, __version__


class CodexGateway(QObject):
    """Codex app-server 的 JSONL/JSON-RPC 客户端。"""

    statusChanged = Signal(str)
    initialized = Signal()
    notification = Signal(dict)
    serverRequest = Signal(int, str, dict)
    protocolInfo = Signal(dict)
    requestFailed = Signal(str)
    diagnostic = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self._process.readyReadStandardOutput.connect(self._read_stdout)
        self._process.readyReadStandardError.connect(self._read_stderr)
        self._process.errorOccurred.connect(self._on_process_error)
        self._process.finished.connect(self._on_process_finished)
        self._buffer = b""
        self._next_id = 1
        self._callbacks: dict[int, Callable[[dict[str, Any]], None]] = {}
        self._ready = False
        self._offline_mode = False
        self._disabled_mcp_servers: tuple[str, ...] = ()
        self._skill_extra_roots: tuple[str, ...] = ()
        self._last_cwd: str | None = None
        self._stopping = False
        self._restart_attempts = 0
        self._initialize_result: dict[str, Any] = {}
        self._pending_notifications: list[dict[str, Any]] = []
        self._notification_timer = QTimer(self)
        self._notification_timer.setSingleShot(True)
        self._notification_timer.setInterval(40)
        self._notification_timer.timeout.connect(self._flush_notifications)

    @property
    def is_ready(self) -> bool:
        return self._ready

    def codex_binary(self) -> str:
        override = os.getenv("CODEX_CIL_CODEX_BIN")
        if override:
            return override
        bundled = Path(sys.executable).resolve().parent / "runtime" / "codex" / "codex.exe"
        if bundled.exists():
            return str(bundled)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundled_internal = (
                Path(meipass) / "runtime" / "codex" / "codex.exe"
            )
            if bundled_internal.exists():
                return str(bundled_internal)
        return shutil.which("codex") or "codex"

    def configure_offline_mode(
        self,
        enabled: bool,
        disabled_mcp_servers: tuple[str, ...] = (),
    ) -> None:
        self._offline_mode = enabled
        self._disabled_mcp_servers = disabled_mcp_servers if enabled else ()

    def configure_skill_roots(self, roots: tuple[str | Path, ...]) -> None:
        self._skill_extra_roots = tuple(
            str(Path(root).expanduser().resolve())
            for root in roots
            if Path(root).expanduser().is_dir()
        )

    @property
    def skill_extra_roots(self) -> tuple[str, ...]:
        return self._skill_extra_roots

    def start(self, cwd: str | Path | None = None) -> None:
        if self._process.state() != QProcess.ProcessState.NotRunning:
            return
        self._buffer = b""
        self._callbacks.clear()
        self._next_id = 1
        self._ready = False
        self._stopping = False
        self._pending_notifications.clear()
        self._notification_timer.stop()
        if cwd:
            self._last_cwd = str(cwd)
            self._process.setWorkingDirectory(self._last_cwd)
        env = QProcessEnvironment.systemEnvironment()
        self._process.setProcessEnvironment(env)
        self.statusChanged.emit(f"{DISPLAY_NAME} 正在上线…")
        arguments = self.launch_arguments()
        self._process.start(self.codex_binary(), arguments)

    def launch_arguments(self) -> list[str]:
        arguments = ["app-server", "--listen", "stdio://"]
        if not self._offline_mode:
            return arguments
        arguments.extend(
            [
                "-c",
                'web_search="disabled"',
                "-c",
                "features.web_search=false",
                "-c",
                "features.apps=false",
                "-c",
                "features.plugins=false",
                "-c",
                "features.remote_plugin=false",
                "-c",
                "features.enable_mcp_apps=false",
                "-c",
                "features.hooks=false",
                "-c",
                "features.in_app_browser=false",
                "-c",
                "features.browser_use=false",
                "-c",
                "features.browser_use_external=false",
                "-c",
                "features.browser_use_full_cdp_access=false",
                "-c",
                "features.computer_use=false",
                "-c",
                "sandbox_workspace_write.network_access=false",
                "-c",
                "mcp_servers.china-sources.enabled=true",
            ]
        )
        for server_name in self._disabled_mcp_servers:
            arguments.extend(
                ["-c", f"mcp_servers.{server_name}.enabled=false"]
            )
        return arguments

    def restart(self) -> None:
        self.stop()
        self.start()

    def stop(self) -> None:
        self._stopping = True
        self._notification_timer.stop()
        self._pending_notifications.clear()
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.terminate()
            if not self._process.waitForFinished(1500):
                self._process.kill()

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> int:
        request_id = self._next_id
        self._next_id += 1
        if callback is not None:
            self._callbacks[request_id] = callback
        self._send(
            {
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
        return request_id

    def start_thread(
        self,
        cwd: str,
        callback: Callable[[dict[str, Any]], None],
        model: str | None = None,
    ) -> None:
        params: dict[str, Any] = {"cwd": cwd}
        if model:
            params["model"] = model
        self.request("thread/start", params, callback)

    def start_turn(
        self,
        thread_id: str,
        text: str,
        cwd: str,
        callback: Callable[[dict[str, Any]], None] | None = None,
        model: str | None = None,
        effort: str | None = None,
        service_tier: str | None = None,
        sandbox_policy: dict[str, Any] | None = None,
        approval_policy: str | dict[str, Any] | None = None,
        collaboration_mode: dict[str, Any] | None = None,
        image_paths: list[str] | None = None,
    ) -> None:
        input_items: list[dict[str, Any]] = [{"type": "text", "text": text}]
        input_items.extend(
            {"type": "localImage", "path": str(path)}
            for path in image_paths or []
            if path
        )
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": input_items,
            "cwd": cwd,
        }
        if model:
            params["model"] = model
        if effort:
            params["effort"] = effort
        if service_tier:
            params["serviceTier"] = service_tier
        if sandbox_policy:
            params["sandboxPolicy"] = sandbox_policy
        if approval_policy:
            params["approvalPolicy"] = approval_policy
        if collaboration_mode:
            params["collaborationMode"] = collaboration_mode
        self.request("turn/start", params, callback)

    def steer_turn(
        self,
        thread_id: str,
        text: str,
        expected_turn_id: str | None = None,
    ) -> None:
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": text}],
        }
        if expected_turn_id:
            params["expectedTurnId"] = expected_turn_id
        self.request("turn/steer", params)

    def interrupt_turn(self, thread_id: str, turn_id: str) -> None:
        self.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})

    def _send(self, payload: dict[str, Any]) -> None:
        if self._process.state() == QProcess.ProcessState.NotRunning:
            self.requestFailed.emit(f"{DISPLAY_NAME} 智能核心未运行。")
            return
        wire = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._process.write(wire + b"\n")

    def respond(
        self,
        request_id: int,
        result: dict[str, Any] | None = None,
        *,
        error: str | None = None,
    ) -> None:
        if error is not None:
            self._send(
                {
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": error,
                    },
                }
            )
            return
        self._send({"id": request_id, "result": result or {}})

    def _read_stdout(self) -> None:
        self._buffer += bytes(self._process.readAllStandardOutput())
        while b"\n" in self._buffer:
            raw, self._buffer = self._buffer.split(b"\n", 1)
            raw = raw.strip()
            if not raw:
                continue
            try:
                message = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            self._dispatch(message)

    def _read_stderr(self) -> None:
        text = bytes(self._process.readAllStandardError()).decode("utf-8", errors="replace")
        for line in text.splitlines():
            if line.strip():
                self.diagnostic.emit(line.strip())

    def _dispatch(self, message: dict[str, Any]) -> None:
        if "id" in message:
            request_id = int(message["id"])
            method = message.get("method")
            if method:
                self.serverRequest.emit(
                    request_id,
                    str(method),
                    message.get("params") or {},
                )
                return
            callback = self._callbacks.pop(request_id, None)
            if "error" in message:
                error = message["error"]
                self.requestFailed.emit(str(error.get("message") or error))
                return
            result = message.get("result") or {}
            if callback is not None:
                callback(result)
            return
        method = message.get("method")
        if method == "initialized":
            return
        params = message.get("params") or {}
        self._pending_notifications.append(
            {"method": method, "params": params}
        )
        if not self._notification_timer.isActive():
            self._notification_timer.start()

    def _flush_notifications(self) -> None:
        pending = self._pending_notifications
        self._pending_notifications = []
        if not pending:
            return

        aggregate: dict[str, Any] | None = None
        aggregate_key: tuple[Any, ...] | None = None

        def flush_aggregate() -> None:
            nonlocal aggregate, aggregate_key
            if aggregate is not None:
                self.notification.emit(aggregate)
            aggregate = None
            aggregate_key = None

        for event in pending:
            method = str(event.get("method") or "")
            params = event.get("params") or {}
            key = self._notification_delta_key(method, params)
            if key is None:
                flush_aggregate()
                self.notification.emit(event)
                continue
            if aggregate is None or key != aggregate_key:
                flush_aggregate()
                aggregate = {
                    "method": method,
                    "params": dict(params),
                }
                aggregate_key = key
                continue
            current = str(aggregate["params"].get("delta") or "")
            aggregate["params"]["delta"] = current + str(
                params.get("delta") or ""
            )
        flush_aggregate()

    @staticmethod
    def _notification_delta_key(
        method: str,
        params: dict[str, Any],
    ) -> tuple[Any, ...] | None:
        if method == "item/agentMessage/delta":
            return (
                method,
                params.get("threadId"),
                params.get("itemId"),
            )
        if method == "item/reasoning/summaryTextDelta":
            return (
                method,
                params.get("threadId"),
                params.get("itemId"),
                params.get("summaryIndex"),
            )
        if method == "item/reasoning/textDelta":
            return (
                method,
                params.get("threadId"),
                params.get("itemId"),
                params.get("contentIndex"),
            )
        if method == "item/commandExecution/outputDelta":
            return (
                method,
                params.get("threadId"),
                params.get("itemId"),
            )
        return None

    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        self._ready = False
        self.statusChanged.emit(f"{DISPLAY_NAME} 核心连接失败")
        self.requestFailed.emit(f"无法启动 {DISPLAY_NAME} 智能核心: {error.name}")

    def _on_process_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        self._ready = False
        self.statusChanged.emit(f"{DISPLAY_NAME} 核心已断开 ({exit_code})")
        if self._stopping:
            return
        if self._restart_attempts < 3:
            self._restart_attempts += 1
            delay = self._restart_attempts * 1500
            self.statusChanged.emit(
                f"{DISPLAY_NAME} 将在 {delay // 1000} 秒后尝试重连 "
                f"({self._restart_attempts}/3)"
            )
            QTimer.singleShot(delay, lambda: self.start(self._last_cwd))
        else:
            self.requestFailed.emit(
                f"{DISPLAY_NAME} 核心连续异常退出，已停止自动重连。"
            )

    def _on_started(self) -> None:
        if not self._process.isOpen():
            return
        self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "codex_cil_desktop",
                    "title": DISPLAY_NAME,
                    "version": __version__,
                },
                "capabilities": {"experimentalApi": True},
            },
            self._on_initialized_response,
        )

    def _on_initialized_response(self, _result: dict[str, Any]) -> None:
        self._initialize_result = dict(_result)
        self._send({"method": "initialized", "params": {}})
        if self._skill_extra_roots:
            self.request(
                "skills/extraRoots/set",
                {"extraRoots": list(self._skill_extra_roots)},
            )
        self._ready = True
        self._restart_attempts = 0
        self.statusChanged.emit(f"{DISPLAY_NAME} 已就绪")
        self.initialized.emit()
        self.protocolInfo.emit(self._initialize_result)

    @property
    def initialize_result(self) -> dict[str, Any]:
        return dict(self._initialize_result)

    def connect_started_hook(self) -> None:
        self._process.started.connect(self._on_started)
