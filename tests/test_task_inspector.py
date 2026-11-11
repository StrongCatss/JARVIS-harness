from __future__ import annotations

from PySide6.QtTest import QSignalSpy

from codex_cil.models import SessionKind
from codex_cil.ui.inspector_panel import InspectorPanel


def test_task_inspector_renders_plan_and_emits_controls(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = InspectorPanel()
    qtbot.addWidget(panel)
    panel.set_session("任务", SessionKind.PROJECT, "C:/")
    panel.set_plan(
        "实现功能",
        [
            {"step": "分析", "status": "completed"},
            {"step": "编码", "status": "inProgress"},
        ],
    )
    panel.set_current_activity("命令：pytest")
    panel.set_modified_count(3)

    text = panel.task.toPlainText()
    assert "实现功能" in text
    assert "[完成] 分析" in text
    assert "[进行中] 编码" in text
    assert "pytest" in text
    assert "已修改文件：3" in text

    cancel_spy = QSignalSpy(panel.cancelTurnRequested)
    steer_spy = QSignalSpy(panel.steerRequested)
    panel.cancel_button.click()
    panel.steer_input.setText("继续修复")
    panel.steer_button.click()
    assert cancel_spy.count() == 1
    assert steer_spy.count() == 1
