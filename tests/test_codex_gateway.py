from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtTest import QSignalSpy

from codex_cil.services.codex_gateway import CodexGateway


def test_bundled_codex_runtime_is_preferred(
    qtbot,  # type: ignore[no-untyped-def]
    monkeypatch,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    executable = tmp_path / "JARVIS.exe"
    bundled = tmp_path / "runtime" / "codex" / "codex.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"stub")
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.delenv("CODEX_CIL_CODEX_BIN", raising=False)

    gateway = CodexGateway()
    assert gateway.codex_binary() == str(bundled)


def test_offline_mode_blocks_network_and_external_capabilities() -> None:
    gateway = CodexGateway()
    gateway.configure_offline_mode(True, ("remote-docs", "github"))

    arguments = gateway.launch_arguments()
    values = set(arguments)

    assert 'web_search="disabled"' in values
    assert "sandbox_workspace_write.network_access=false" in values
    assert "features.apps=false" in values
    assert "features.plugins=false" in values
    assert "features.hooks=false" in values
    assert "features.browser_use=false" in values
    assert "features.computer_use=false" in values
    assert "mcp_servers.china-sources.enabled=true" in values
    assert "mcp_servers.remote-docs.enabled=false" in values
    assert "mcp_servers.github.enabled=false" in values

    gateway.configure_offline_mode(False)
    assert gateway.launch_arguments() == [
        "app-server",
        "--listen",
        "stdio://",
    ]


def test_tool_skill_roots_are_absolute_and_valid(
    tmp_path: Path,
) -> None:
    skills = tmp_path / "skills"
    skills.mkdir()
    gateway = CodexGateway()

    gateway.configure_skill_roots((skills, tmp_path / "missing"))

    assert gateway.skill_extra_roots == (str(skills.resolve()),)


def test_server_request_is_forwarded_and_can_be_answered(
    qtbot,  # type: ignore[no-untyped-def]
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    gateway = CodexGateway()
    spy = QSignalSpy(gateway.serverRequest)
    gateway._dispatch(
        {
            "id": 77,
            "method": "item/commandExecution/requestApproval",
            "params": {"threadId": "thread-1", "command": "git status"},
        }
    )

    assert spy.count() == 1
    args = spy.at(0)
    assert args[0] == 77
    assert args[1] == "item/commandExecution/requestApproval"

    sent: list[dict] = []
    monkeypatch.setattr(gateway, "_send", sent.append)
    gateway.respond(77, {"decision": "accept"})
    assert sent == [{"id": 77, "result": {"decision": "accept"}}]


def test_streaming_deltas_are_batched_before_ui_dispatch(
    qtbot,  # type: ignore[no-untyped-def]
) -> None:
    gateway = CodexGateway()
    spy = QSignalSpy(gateway.notification)
    for text in ("A", "B", "C"):
        gateway._dispatch(
            {
                "method": "item/reasoning/summaryTextDelta",
                "params": {
                    "delta": text,
                    "threadId": "thread-1",
                    "itemId": "reason-1",
                    "summaryIndex": 0,
                },
            }
        )

    gateway._flush_notifications()
    gateway._notification_timer.stop()

    assert spy.count() == 1
    event = spy.at(0)[0]
    assert event["params"]["delta"] == "ABC"


def test_start_turn_includes_local_image_inputs(
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    gateway = CodexGateway()
    sent: list[dict] = []
    monkeypatch.setattr(gateway, "_send", sent.append)

    gateway.start_turn(
        "thread-1",
        "分析截图",
        "C:/workspace",
        image_paths=["C:/screenshots/shot.png"],
    )

    inputs = sent[0]["params"]["input"]
    assert inputs == [
        {"type": "text", "text": "分析截图"},
        {
            "type": "localImage",
            "path": "C:/screenshots/shot.png",
        },
    ]
