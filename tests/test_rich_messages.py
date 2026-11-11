from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtTest import QSignalSpy

from codex_cil.ui.browser_panel import BrowserWorkspace, DEFAULT_BROWSER_URL
from codex_cil.ui.chat_panel import ChatEntry, ChatPanel
from codex_cil.ui.markdown_renderer import extract_code_blocks, render_markdown
from codex_cil.ui.theme_system import THEMES


def test_markdown_renders_rich_content_and_code_colors() -> None:
    content = """# 标题

**加粗** 和 `inline`

```python
def greet(name):
    return f"Hello {name}"
```

| 名称 | 状态 |
|---|---|
| 贾维斯 | 在线 |
"""
    rendered = render_markdown(content, THEMES["hacker"])

    assert "<h1>标题</h1>" in rendered
    assert "<strong>加粗</strong>" in rendered
    assert "<table>" in rendered
    assert "background: #0b1510" in rendered
    assert "Cascadia Code" in rendered
    assert 'style="color:' in rendered


def test_markdown_sanitizes_active_content() -> None:
    rendered = render_markdown(
        '<script>alert(1)</script>'
        '[危险链接](javascript:alert(1))'
        '<img src="https://example.com/a.png">',
        THEMES["hacker"],
    )

    assert "<script" not in rendered
    assert "<img" not in rendered
    assert "javascript:" not in rendered
    assert "alert(1)" in rendered


def test_markdown_code_blocks_expose_copy_link_and_text() -> None:
    content = "```python\nprint('hello')\n```"
    rendered = render_markdown(
        content,
        THEMES["hacker"],
        copy_prefix="jarvis-code-copy:test:",
    )

    assert extract_code_blocks(content) == ["print('hello')"]
    assert "jarvis-code-copy:test:0" in rendered
    assert "复制代码" in rendered


def test_chat_roles_use_distinct_surfaces(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = ChatPanel()
    qtbot.addWidget(panel)
    panel.add_message("user", "用户内容")
    panel.add_message(
        "assistant",
        "## 贾维斯回答\n\n```powershell\nGet-ChildItem\n```",
    )

    rendered = panel.transcript.toHtml()
    assert "你" in rendered
    assert "贾维斯" in rendered
    assert "用户内容" in rendered
    assert "Get-ChildItem" in rendered
    assert "#31e982" in rendered.lower()
    assert "#153425" in rendered.lower()
    assert "#73b7ff" in rendered.lower()
    assert "border-left" in rendered.lower()
    assert "58px" in rendered
    assert "重新生成" not in rendered
    assert ">复制<" not in rendered


def test_browser_defaults_to_baidu_and_can_request_close(qtbot) -> None:  # type: ignore[no-untyped-def]
    workspace = BrowserWorkspace()
    qtbot.addWidget(workspace)
    browser = workspace.current_browser
    assert browser is not None
    assert browser.view.url().toString() == DEFAULT_BROWSER_URL

    close_spy = QSignalSpy(workspace.closeRequested)
    browser.close_button.click()
    assert close_spy.count() == 1


def test_reasoning_streams_then_merges_into_collapsible_answer(
    qtbot,  # type: ignore[no-untyped-def]
) -> None:
    panel = ChatPanel()
    qtbot.addWidget(panel)
    panel.begin_assistant_stream()
    panel.begin_reasoning_stream()
    panel.append_reasoning_delta("先分析需求", index=0)
    panel.append_reasoning_delta("再检查文件", index=1)
    panel.append_assistant_stream("最终答案")
    qtbot.wait(120)

    streaming_text = panel.transcript.toPlainText()
    assert "思考过程 · 思考中" in streaming_text
    assert "先分析需求" in streaming_text

    panel.complete_reasoning(
        summary=["先分析需求\n\n再检查文件"],
        content=["检查上下文后给出答案"],
    )
    qtbot.wait(20)
    completed_text = panel.transcript.toPlainText()
    assert "思考过程 · 思考完成" in completed_text
    assert "检查上下文后给出答案" not in completed_text
    assert panel.last_assistant_metadata() == {
        "reasoning_summary": "先分析需求\n\n再检查文件",
        "reasoning_raw": "检查上下文后给出答案",
    }

    panel._link_activated(
        QUrl(f"jarvis-reasoning:{panel._messages[-1].uid}")
    )
    expanded_text = panel.transcript.toPlainText()
    assert "推理摘要" in expanded_text
    assert "模型推理内容" in expanded_text
    assert "检查上下文后给出答案" in expanded_text


def test_reasoning_start_shows_live_placeholder_without_deltas(
    qtbot,  # type: ignore[no-untyped-def]
) -> None:
    panel = ChatPanel()
    qtbot.addWidget(panel)
    panel.show()
    qtbot.waitExposed(panel)
    panel.begin_assistant_stream()
    panel.begin_reasoning_stream("waiting")
    qtbot.wait(150)

    assert "正在分析上下文" in panel.transcript.toPlainText()
    assert panel.status_light.isVisible()


def test_reasoning_metadata_survives_message_reload() -> None:
    entry = ChatEntry.from_stored(
        "assistant",
        "回答",
        '{"reasoning_summary": "思考", "reasoning_raw": "原始推理"}',
    )

    assert entry.has_reasoning
    assert entry.reasoning_summary == "思考"
    assert entry.reasoning_raw == "原始推理"


def test_streaming_renders_are_coalesced(
    qtbot,  # type: ignore[no-untyped-def]
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    panel = ChatPanel()
    qtbot.addWidget(panel)
    calls: list[bool] = []

    def fake_render(*, force_scroll: bool = False) -> None:
        calls.append(force_scroll)

    monkeypatch.setattr(panel, "_render", fake_render)
    panel.begin_assistant_stream()
    panel.begin_reasoning_stream("batch")
    for index in range(200):
        panel.append_reasoning_delta("片段", index=0, item_id="batch")

    assert calls == []
    qtbot.wait(130)
    assert len(calls) == 1


def test_stream_finish_uses_authoritative_text_and_full_markdown_rendering(
    qtbot,  # type: ignore[no-untyped-def]
) -> None:
    panel = ChatPanel()
    qtbot.addWidget(panel)
    panel.begin_assistant_stream()
    panel.append_assistant_stream("临时")

    panel.finish_assistant_stream("## 最终回答\n\n```python\nprint('ok')\n```")
    qtbot.wait(20)

    entry = panel._messages[-1]
    assert entry.content.startswith("## 最终回答")
    assert not entry.streaming
    assert "复制代码" in panel.transcript.toHtml()
