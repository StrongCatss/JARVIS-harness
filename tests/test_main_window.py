from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QSignalSpy, QTest

from codex_cil.database import Database
from codex_cil.models import SessionKind
from codex_cil.services.codex_gateway import CodexGateway
from codex_cil.services.theme_store import CustomThemeStore
from codex_cil.ui.main_window import (
    MainWindow,
    _approval_policy_for_mode,
    _build_pure_chat_payload,
)
from codex_cil.ui.theme_system import THEMES


def test_main_window_exposes_tools_configuration(
    qtbot,  # type: ignore[no-untyped-def]
    monkeypatch,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(CodexGateway, "start", lambda self: None)
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / ".JARVIS"))
    database = Database(tmp_path / "app.db")
    window = MainWindow(database)
    qtbot.addWidget(window)

    assert window.tools_config_button.text() == "数据配置"
    assert window.screenshot_button.text() == "截图"
    assert (
        window.menuBar().cornerWidget(Qt.Corner.TopRightCorner)
        is window.screenshot_button
    )
    assert (
        window.commands.actions["screenshot.region"].shortcut().toString()
        == "Ctrl+Shift+Z"
    )
    assert (
        window.commands.actions["screenshot.screen1"].shortcut().toString()
        == "Ctrl+Shift+1"
    )
    assert (
        window.commands.actions["screenshot.screen2"].shortcut().toString()
        == "Ctrl+Shift+2"
    )
    assert "tools.configure" in window.commands.actions
    assert window.tools_layout is not None
    assert window.tools_layout.root.name == "Tools"
    assert any(
        action.text() == "配色设置…"
        for action in window.theme_menu.actions()
    )

    window.close()


def test_browser_tab_context_menu_closes_browser(
    qtbot,  # type: ignore[no-untyped-def]
    monkeypatch,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(CodexGateway, "start", lambda self: None)
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / ".JARVIS"))
    window = MainWindow(Database(tmp_path / "app.db"))
    qtbot.addWidget(window)
    window._open_browser()

    browser = window._browser_workspace
    assert browser is not None
    tab_bar = window.work_tabs.tabBar()
    browser_index = window.work_tabs.indexOf(browser)
    assert window._is_browser_tab_position(
        tab_bar.tabRect(browser_index).center()
    )
    window._close_browser()

    assert window._browser_workspace is None
    assert window.work_tabs.currentWidget() is window.chat
    window.close()


def test_main_window_loads_saved_custom_themes(
    qtbot,  # type: ignore[no-untyped-def]
    monkeypatch,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(CodexGateway, "start", lambda self: None)
    jarvis_home = tmp_path / ".JARVIS"
    monkeypatch.setenv("JARVIS_HOME", str(jarvis_home))
    custom = replace(
        THEMES["hacker"],
        identifier="custom_restart",
        name="重启后主题",
        assistant_accent="#73B7FF",
    )
    CustomThemeStore(jarvis_home / "themes.json").save([custom])

    window = MainWindow(Database(tmp_path / "app.db"))
    qtbot.addWidget(window)

    assert "custom_restart" in window.theme_actions
    assert window.theme_actions["custom_restart"].text() == "重启后主题"
    window.close()


def test_reasoning_notifications_are_saved_with_answer(
    qtbot,  # type: ignore[no-untyped-def]
    monkeypatch,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(CodexGateway, "start", lambda self: None)
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / ".JARVIS"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = Database(tmp_path / "app.db")
    project = database.add_project("Demo", str(workspace))
    session = database.create_session(
        SessionKind.PROJECT,
        "项目会话",
        project_id=project.id,
    )
    window = MainWindow(database)
    qtbot.addWidget(window)
    window.chat.begin_assistant_stream()

    window._codex_notification(
        {
            "method": "item/reasoning/summaryTextDelta",
            "params": {
                "delta": "分析问题",
                "summaryIndex": 0,
                "itemId": "reason-1",
                "turnId": "turn-1",
            },
        }
    )
    window._codex_notification(
        {
            "method": "item/reasoning/textDelta",
            "params": {
                "delta": "检查上下文",
                "contentIndex": 0,
                "itemId": "reason-1",
                "turnId": "turn-1",
            },
        }
    )
    window._codex_notification(
        {
            "method": "item/completed",
            "params": {
                "item": {
                    "id": "reason-1",
                    "type": "reasoning",
                    "summary": ["分析问题"],
                    "content": ["检查上下文"],
                }
            },
        }
    )
    window._codex_notification(
        {
            "method": "item/reasoning/summaryTextDelta",
            "params": {
                "delta": "核对保存方式",
                "summaryIndex": 0,
                "itemId": "reason-2",
                "turnId": "turn-1",
            },
        }
    )
    window._codex_notification(
        {
            "method": "item/completed",
            "params": {
                "item": {
                    "id": "reason-2",
                    "type": "reasoning",
                    "summary": ["核对保存方式"],
                    "content": [],
                }
            },
        }
    )
    window.chat.append_assistant_stream("最终回答")
    window._finish_project_turn()

    messages = database.list_messages(SessionKind.PROJECT, session.id)
    assistant = next(message for message in messages if message.role == "assistant")
    assert assistant.content == "最终回答"
    assert "分析问题" in assistant.metadata_json
    assert "检查上下文" in assistant.metadata_json
    assert "核对保存方式" in assistant.metadata_json
    window.close()


def test_pure_chat_payload_uses_recent_context_budget() -> None:
    messages = [
        SimpleNamespace(
            role="user" if index % 2 == 0 else "assistant",
            content=f"message-{index}-" + ("内容" * 400),
        )
        for index in range(50)
    ]

    payload, trimmed = _build_pure_chat_payload(
        messages,
        context_window=4_000,
    )

    assert trimmed > 0
    assert payload
    assert payload[-1]["content"].startswith("message-49-")
    assert len(payload) < len(messages)


def test_auto_permission_mode_does_not_prompt_for_each_command() -> None:
    assert _approval_policy_for_mode("auto") == "never"
    assert _approval_policy_for_mode("full_access") == "never"
    assert _approval_policy_for_mode("read_only") == "on-request"
    assert _approval_policy_for_mode("plan") == "on-request"


def test_region_screenshot_shortcut_works_from_composer(
    qtbot,  # type: ignore[no-untyped-def]
    monkeypatch,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(CodexGateway, "start", lambda self: None)
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / ".JARVIS"))
    screenshot = QPixmap(40, 30)
    screenshot.fill(QColor("#123524"))
    monkeypatch.setattr(
        "codex_cil.ui.main_window.capture_virtual_desktop",
        lambda: screenshot,
    )
    monkeypatch.setattr(
        "codex_cil.ui.main_window.virtual_screen_geometry",
        lambda: QRect(0, 0, 40, 30),
    )
    window = MainWindow(Database(tmp_path / "app.db"))
    qtbot.addWidget(window)
    spy = QSignalSpy(window.commands.actions["screenshot.region"].triggered)
    window.chat.focus_composer()

    QTest.keyClick(
        window.chat.composer.editor,
        Qt.Key.Key_Z,
        Qt.KeyboardModifier.ControlModifier
        | Qt.KeyboardModifier.ShiftModifier,
    )
    qtbot.wait(30)

    assert spy.count() == 1
    if window._screenshot_overlay is not None:
        window._screenshot_overlay.close()
    window.close()


def test_captured_image_is_saved_and_attached_to_current_chat(
    qtbot,  # type: ignore[no-untyped-def]
    monkeypatch,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(CodexGateway, "start", lambda self: None)
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / ".JARVIS"))
    monkeypatch.setenv("CODEX_CIL_DATA_DIR", str(tmp_path / "data"))
    window = MainWindow(Database(tmp_path / "app.db"))
    qtbot.addWidget(window)
    screenshot = QPixmap(20, 16)
    screenshot.fill(QColor("#123524"))

    window._screenshot_captured(screenshot)

    attachments = window.chat.composer.attachment_paths
    assert len(attachments) == 1
    assert attachments[0].endswith(".png")
    assert Path(attachments[0]).exists()
    window.close()


def test_project_context_prompt_contains_only_selected_history() -> None:
    history = [
        SimpleNamespace(role="user", content="old-user"),
        SimpleNamespace(role="assistant", content="old-assistant"),
    ]

    prompt = MainWindow._project_prompt_with_history(
        "current-task",
        history,
        2,
    )

    assert "最近 2 条历史消息" in prompt
    assert "old-user" in prompt
    assert "old-assistant" in prompt
    assert "【当前用户任务】\ncurrent-task" in prompt


def test_main_window_loads_history_pages(
    qtbot,  # type: ignore[no-untyped-def]
    monkeypatch,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(CodexGateway, "start", lambda self: None)
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / ".JARVIS"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = Database(tmp_path / "app.db")
    project = database.add_project("Demo", str(workspace))
    session = database.create_session(
        SessionKind.PROJECT,
        "长会话",
        project_id=project.id,
    )
    for index in range(250):
        database.add_message(
            SessionKind.PROJECT,
            session.id,
            "user" if index % 2 == 0 else "assistant",
            f"message-{index}",
        )

    window = MainWindow(database)
    qtbot.addWidget(window)

    assert len(window.chat._messages) == 200
    assert window.chat.load_earlier_button.isVisible() is False
    assert window.chat.load_earlier_button.isHidden() is False
    window._load_earlier_messages()
    assert len(window.chat._messages) == 250
    assert window.chat.load_earlier_button.isHidden() is True
    window.close()
