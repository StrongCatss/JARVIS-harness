from __future__ import annotations

from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from codex_cil.models import SessionKind, UsageStats
from codex_cil.ui.chat_panel import ChatPanel


def test_chat_panel_modes(qtbot) -> None:  # type: ignore[no-untyped-def]
    assert QApplication.instance() is not None
    panel = ChatPanel()
    qtbot.addWidget(panel)
    assert panel.brand_label.text() == "贾维斯"
    assert "J.A.R.V.I.S." in panel.transcript.toPlainText()
    panel.set_session("Project", SessionKind.PROJECT, "C:/")
    assert panel.composer.attach_button.isVisible() is False
    panel.show()
    qtbot.waitExposed(panel)
    assert panel.composer.attach_button.isVisible() is True
    assert panel.composer.command_button.isVisible() is True
    assert panel.composer.permission_combo.isVisible() is True
    assert panel.composer.context_combo.isVisible() is True
    assert panel.composer.context_combo.count() == 6
    panel.composer.permission_combo.setCurrentIndex(
        panel.composer.permission_combo.findData("plan")
    )
    assert panel.composer.permission_mode == "plan"
    stop_spy = QSignalSpy(panel.stopRequested)
    panel.set_busy(True)
    assert panel.composer.stop_button.isVisible()
    assert not panel.composer.send_button.isVisible()
    panel.composer.stop_button.click()
    assert stop_spy.count() == 1
    panel.set_busy(False)
    panel.set_usage(UsageStats(context_tokens=50, context_window=100))
    panel.add_message("user", "hello")
    assert "hello" in panel.transcript.toPlainText()


def test_usage_card_and_composer_share_top_edge(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = ChatPanel()
    qtbot.addWidget(panel)
    panel.resize(1000, 720)
    panel.show()
    qtbot.waitExposed(panel)
    qtbot.wait(50)

    assert abs(panel.usage_card.geometry().top() - panel.composer.geometry().top()) <= 1


def test_reasoning_and_speed_controls_follow_model_catalog(
    qtbot,  # type: ignore[no-untyped-def]
) -> None:
    panel = ChatPanel()
    qtbot.addWidget(panel)
    panel.show()
    qtbot.waitExposed(panel)
    composer = panel.composer
    composer.set_model_capabilities(
        efforts=[
            {"reasoningEffort": "low", "description": "Fast"},
            {"reasoningEffort": "high", "description": "Deep"},
        ],
        default_effort="high",
        service_tiers=[],
        preferred_effort="high",
    )
    assert composer.reasoning_combo.isVisible()
    assert composer.reasoning_combo.currentData() == "high"
    assert not composer.speed_combo.isVisible()

    composer.set_model_capabilities(
        efforts=[{"reasoningEffort": "medium", "description": "Balanced"}],
        default_effort="medium",
        service_tiers=[{"id": "fast", "name": "Fast", "description": "Faster"}],
    )
    assert composer.speed_combo.isVisible()
    assert composer.speed_combo.count() == 2


def test_fallback_reasoning_control_stays_visible_for_custom_models(
    qtbot,  # type: ignore[no-untyped-def]
) -> None:
    panel = ChatPanel()
    qtbot.addWidget(panel)
    panel.show()
    qtbot.waitExposed(panel)

    composer = panel.composer
    composer.set_fallback_reasoning_efforts("low")

    assert composer.reasoning_combo.isVisible()
    assert composer.reasoning_combo.currentData() == "low"
    assert composer.reasoning_combo.count() == 5


def test_agent_status_light_states(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = ChatPanel()
    qtbot.addWidget(panel)

    panel.set_agent_status("responding")
    assert "正在输出" in panel.status_light.text()
    panel.set_agent_status("error")
    assert "错误" in panel.status_light.text()
    panel.set_agent_status("done")
    assert "回答完毕" in panel.status_light.text()


def test_context_mode_values(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = ChatPanel()
    qtbot.addWidget(panel)

    panel.composer.set_context_mode("10")
    assert panel.composer.context_message_limit == 10
    panel.composer.set_context_mode("none")
    assert panel.composer.context_message_limit == 0
    panel.composer.set_context_mode("all")
    assert panel.composer.context_message_limit is None
