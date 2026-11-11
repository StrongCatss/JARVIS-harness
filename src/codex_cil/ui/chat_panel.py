from __future__ import annotations

import html
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from codex_cil import DISPLAY_NAME
from codex_cil.models import SessionKind, UsageStats
from codex_cil.ui.composer import ComposerWidget
from codex_cil.ui.markdown_renderer import render_markdown
from codex_cil.ui.theme_system import ThemeSpec, current_theme, theme_manager
from codex_cil.ui.usage_card import UsageCard

_ASSISTANT_ACCENTS = {
    "hacker": "#73b7ff",
    "gundam": "#b77900",
    "ironman": "#ffc247",
    "scifi": "#ff6ad5",
    "black_empire": "#ffc247",
    "cyberpunk2077": "#00f0ff",
    "pacific_rim": "#54f5e8",
    "starcraft": "#56d8ff",
    "retro_crt": "#9cff7a",
}
_RENDER_INTERVAL_MS = 80
_REASONING_RAW_LIMIT = 50_000


@dataclass(slots=True)
class ChatEntry:
    role: str
    content: str
    uid: str = field(default_factory=lambda: uuid.uuid4().hex)
    reasoning_summary: str = ""
    reasoning_raw: str = ""
    reasoning_streaming: bool = False
    reasoning_expanded: bool = False
    reasoning_summary_index: int = 0
    reasoning_content_index: int = 0
    reasoning_summary_items: dict[str, str] = field(default_factory=dict)
    reasoning_raw_items: dict[str, str] = field(default_factory=dict)
    reasoning_summary_indices: dict[str, int] = field(default_factory=dict)
    reasoning_content_indices: dict[str, int] = field(default_factory=dict)
    reasoning_item_order: list[str] = field(default_factory=list)
    reasoning_dirty: bool = False
    streaming: bool = False
    cached_body_key: tuple[str, str, bool] = ("", "", False)
    cached_body_html: str = ""
    cached_reasoning_key: tuple[object, ...] = ()
    cached_reasoning_html: str = ""

    @classmethod
    def from_stored(
        cls,
        role: str,
        content: str,
        metadata_json: str = "{}",
    ) -> ChatEntry:
        try:
            metadata = json.loads(metadata_json or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        return cls(
            role=role,
            content=content,
            reasoning_summary=str(metadata.get("reasoning_summary") or ""),
            reasoning_raw=str(metadata.get("reasoning_raw") or ""),
        )

    @property
    def has_reasoning(self) -> bool:
        return bool(self.reasoning_summary.strip() or self.reasoning_raw.strip())

    def metadata(self) -> dict[str, str]:
        metadata: dict[str, str] = {}
        if self.reasoning_summary.strip():
            metadata["reasoning_summary"] = self.reasoning_summary
        if self.reasoning_raw.strip():
            metadata["reasoning_raw"] = self.reasoning_raw
        return metadata

    def ensure_reasoning_item(self, item_id: str) -> str:
        normalized = item_id or "reasoning"
        if not self.reasoning_item_order:
            if self.reasoning_summary:
                self.reasoning_item_order.append("stored")
                self.reasoning_summary_items["stored"] = self.reasoning_summary
            if self.reasoning_raw:
                if "stored" not in self.reasoning_item_order:
                    self.reasoning_item_order.append("stored")
                self.reasoning_raw_items["stored"] = self.reasoning_raw
        if normalized not in self.reasoning_item_order:
            self.reasoning_item_order.append(normalized)
        self.reasoning_summary_items.setdefault(normalized, "")
        self.reasoning_raw_items.setdefault(normalized, "")
        self.reasoning_summary_indices.setdefault(normalized, 0)
        self.reasoning_content_indices.setdefault(normalized, 0)
        return normalized

    def rebuild_reasoning_aggregate(self) -> None:
        self.reasoning_summary = "\n\n".join(
            self.reasoning_summary_items[item_id]
            for item_id in self.reasoning_item_order
            if self.reasoning_summary_items.get(item_id, "").strip()
        )
        self.reasoning_raw = "\n\n".join(
            self.reasoning_raw_items[item_id]
            for item_id in self.reasoning_item_order
            if self.reasoning_raw_items.get(item_id, "").strip()
        )


class ChatPanel(QFrame):
    messageSubmitted = Signal(str, list)
    commandRequested = Signal(str)
    stopRequested = Signal()
    detailsRequested = Signal()
    earlierRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self._messages: list[ChatEntry] = []
        self._mode = SessionKind.PURE_CHAT
        self._title = "新对话"
        self._render_force_scroll = False
        self._has_earlier = False
        self._agent_state = "idle"
        self._agent_text = "待机"
        self._thinking_phase = 0
        self._thinking_timer = QTimer(self)
        self._thinking_timer.setInterval(500)
        self._thinking_timer.timeout.connect(self._tick_thinking)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(_RENDER_INTERVAL_MS)
        self._render_timer.timeout.connect(self._flush_scheduled_render)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 12)
        root.setSpacing(9)

        header = QHBoxLayout()
        self.brand_label = QLabel(DISPLAY_NAME)
        self.brand_label.setObjectName("ChatBrand")
        self.title = QLabel("新对话")
        self.title.setObjectName("Muted")
        self.mode = QLabel("纯对话")
        self.mode.setObjectName("Pill")
        self.status_light = QLabel()
        self.status_light.setObjectName("AgentStatus")
        self.status_light.setMinimumWidth(92)
        self.status_light.setMinimumHeight(24)
        self.status_light.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.load_earlier_button = QPushButton("加载更早消息")
        self.load_earlier_button.setVisible(False)
        self.load_earlier_button.clicked.connect(self.earlierRequested)
        header.addWidget(self.brand_label)
        header.addWidget(self.mode)
        header.addWidget(self.status_light)
        header.addWidget(self.title)
        header.addWidget(self.load_earlier_button)
        header.addStretch(1)
        root.addLayout(header)

        self.transcript = QTextBrowser()
        self.transcript.setObjectName("Transcript")
        self.transcript.setOpenLinks(False)
        self.transcript.setOpenExternalLinks(False)
        self.transcript.anchorClicked.connect(self._link_activated)
        self.transcript.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self.transcript, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(9)
        self.usage_card = UsageCard()
        self.composer = ComposerWidget()
        bottom.addWidget(self.usage_card, 0)
        bottom.addWidget(self.composer, 1)
        root.addLayout(bottom)

        self.composer.messageSubmitted.connect(self.messageSubmitted)
        self.composer.commandRequested.connect(self.commandRequested)
        self.composer.stopRequested.connect(self.stopRequested)
        self.usage_card.detailsRequested.connect(self.detailsRequested)
        theme_manager.changed.connect(self._theme_changed)
        self.set_agent_status("idle")
        self._render()

    def set_session(
        self,
        title: str,
        mode: SessionKind,
        project_root: str | Path | None = None,
    ) -> None:
        self._title = title
        self._mode = mode
        is_default = _is_default_session_title(title)
        self.title.setText("" if is_default else title)
        self.title.setVisible(not is_default)
        self.mode.setText("项目 Agent" if mode == SessionKind.PROJECT else "纯对话")
        self.mode.setObjectName(
            "Pill" if mode == SessionKind.PROJECT else "PillWarning"
        )
        self.mode.style().unpolish(self.mode)
        self.mode.style().polish(self.mode)
        self.composer.set_mode(mode == SessionKind.PROJECT, project_root)

    def set_usage(self, usage: UsageStats) -> None:
        self.usage_card.set_usage(usage)

    def set_busy(self, busy: bool) -> None:
        self.composer.set_busy(busy)

    def set_agent_status(self, state: str, text: str | None = None) -> None:
        labels = {
            "idle": "待机",
            "responding": "正在输出",
            "done": "回答完毕",
            "cancelled": "已停止",
            "error": "错误",
        }
        self._agent_state = state
        self._agent_text = text or labels.get(state, state)
        colors = {
            "idle": current_theme().muted,
            "responding": current_theme().warning,
            "done": current_theme().accent,
            "cancelled": current_theme().warning,
            "error": current_theme().danger,
        }
        theme = current_theme()
        color = colors.get(state, theme.muted)
        background = _mix_hex(theme.card, color, 0.18)
        self.status_light.setText(f"● {self._agent_text}")
        self.status_light.setStyleSheet(
            f"color:{color};background:{background};"
            f"border:1px solid {color};border-radius:9px;"
            f"font-weight:700;padding:2px 8px;"
        )

    def set_messages(
        self,
        messages: list[ChatEntry] | list[tuple[str, str]],
    ) -> None:
        self._messages = [
            item
            if isinstance(item, ChatEntry)
            else ChatEntry(role=item[0], content=item[1])
            for item in messages
        ]
        self._render(force_scroll=True)

    def prepend_messages(
        self,
        messages: list[ChatEntry] | list[tuple[str, str]],
    ) -> None:
        entries = [
            item
            if isinstance(item, ChatEntry)
            else ChatEntry(role=item[0], content=item[1])
            for item in messages
        ]
        self._messages[0:0] = entries
        self._render()

    def set_has_earlier(self, has_earlier: bool) -> None:
        self._has_earlier = has_earlier
        self.load_earlier_button.setVisible(has_earlier)

    def add_message(self, role: str, content: str) -> None:
        self._messages.append(ChatEntry(role=role, content=content))
        self._render(force_scroll=True)

    def add_entry(self, entry: ChatEntry) -> None:
        self._messages.append(entry)
        self._render(force_scroll=True)

    def begin_assistant_stream(self) -> None:
        self._messages.append(
            ChatEntry(role="assistant", content="", streaming=True)
        )
        self._schedule_render(force_scroll=True)

    def append_assistant_stream(self, delta: str) -> None:
        if not self._messages or self._messages[-1].role != "assistant":
            self.begin_assistant_stream()
        self._messages[-1].content += delta
        self._schedule_render()

    def begin_reasoning_stream(self, item_id: str = "reasoning") -> None:
        if not self._messages or self._messages[-1].role != "assistant":
            self.begin_assistant_stream()
        entry = self._messages[-1]
        entry.ensure_reasoning_item(item_id)
        entry.reasoning_streaming = True
        entry.reasoning_expanded = True
        self._thinking_phase = 0
        self._thinking_timer.start()
        self._schedule_render()

    def append_reasoning_delta(
        self,
        delta: str,
        *,
        raw: bool = False,
        index: int = 0,
        item_id: str = "reasoning",
    ) -> None:
        if not delta:
            return
        if not self._messages or self._messages[-1].role != "assistant":
            self.begin_assistant_stream()
        entry = self._messages[-1]
        item_id = entry.ensure_reasoning_item(item_id)
        entry.reasoning_streaming = True
        entry.reasoning_expanded = True
        self._thinking_timer.start()
        if raw:
            current = entry.reasoning_raw_items[item_id]
            if current and index > entry.reasoning_content_indices[item_id]:
                current += "\n\n"
            updated = current + delta
            if len(updated) > _REASONING_RAW_LIMIT:
                updated = (
                    updated[-_REASONING_RAW_LIMIT:]
                    + "\n\n[原始推理超出显示上限，已截断]"
                )
            entry.reasoning_raw_items[item_id] = updated
            entry.reasoning_content_indices[item_id] = index
        else:
            current = entry.reasoning_summary_items[item_id]
            if current and index > entry.reasoning_summary_indices[item_id]:
                current += "\n\n"
            entry.reasoning_summary_items[item_id] = current + delta
            entry.reasoning_summary_indices[item_id] = index
        entry.reasoning_dirty = True
        self._schedule_render()

    def complete_reasoning(
        self,
        summary: list[str] | None = None,
        content: list[str] | None = None,
        item_id: str = "reasoning",
    ) -> None:
        if not self._messages or self._messages[-1].role != "assistant":
            return
        entry = self._messages[-1]
        item_id = entry.ensure_reasoning_item(item_id)
        final_summary = "\n\n".join(
            text.strip() for text in (summary or []) if text.strip()
        )
        final_content = "\n\n".join(
            text.strip() for text in (content or []) if text.strip()
        )
        if len(final_content) > _REASONING_RAW_LIMIT:
            final_content = (
                final_content[-_REASONING_RAW_LIMIT:]
                + "\n\n[原始推理超出保存上限，已截断]"
            )
        if final_summary:
            entry.reasoning_summary_items[item_id] = final_summary
        if final_content:
            entry.reasoning_raw_items[item_id] = final_content
        entry.reasoning_dirty = True
        entry.reasoning_streaming = False
        entry.reasoning_expanded = False
        self._stop_thinking_timer_if_idle()
        self._render(force_scroll=True)

    def finish_assistant_stream(self, final_content: str | None = None) -> str:
        if not self._messages:
            return ""
        entry = self._messages[-1]
        if final_content is not None:
            entry.content = final_content
        entry.streaming = False
        if entry.reasoning_dirty:
            entry.rebuild_reasoning_aggregate()
            entry.reasoning_dirty = False
        if entry.role == "assistant" and entry.has_reasoning:
            entry.reasoning_streaming = False
            entry.reasoning_expanded = False
        self._stop_thinking_timer_if_idle()
        self._render(force_scroll=True)
        return entry.content

    def last_assistant_metadata(self) -> dict[str, str]:
        if not self._messages or self._messages[-1].role != "assistant":
            return {}
        if self._messages[-1].reasoning_dirty:
            self._messages[-1].rebuild_reasoning_aggregate()
            self._messages[-1].reasoning_dirty = False
        return self._messages[-1].metadata()

    def last_assistant_snapshot(self) -> dict[str, str]:
        if not self._messages or self._messages[-1].role != "assistant":
            return {}
        entry = self._messages[-1]
        snapshot = {"content": entry.content}
        snapshot.update(entry.metadata())
        return snapshot

    def discard_empty_assistant(self) -> None:
        if (
            self._messages
            and self._messages[-1].role == "assistant"
            and not self._messages[-1].content.strip()
            and not self._messages[-1].has_reasoning
        ):
            self._messages.pop()
            self._render()

    def append_attachment(self, path: str) -> bool:
        added = self.composer.add_attachment(path)
        if not added:
            return False
        self.composer.focus_editor()
        return True

    def focus_composer(self) -> None:
        self.composer.focus_editor()

    def _schedule_render(self, *, force_scroll: bool = False) -> None:
        self._render_force_scroll = self._render_force_scroll or force_scroll
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _tick_thinking(self) -> None:
        if not any(entry.reasoning_streaming for entry in self._messages):
            self._thinking_timer.stop()
            return
        self._thinking_phase += 1
        if self._agent_state == "responding":
            self.set_agent_status(
                "responding",
                f"正在输出 · {self._thinking_phase // 2}s",
            )
        self._schedule_render()

    def _stop_thinking_timer_if_idle(self) -> None:
        if not any(entry.reasoning_streaming for entry in self._messages):
            self._thinking_timer.stop()

    def _flush_scheduled_render(self) -> None:
        force_scroll = self._render_force_scroll
        self._render_force_scroll = False
        self._render(force_scroll=force_scroll)

    def _render(self, *, force_scroll: bool = False) -> None:
        self._render_timer.stop()
        self._render_force_scroll = False
        theme = current_theme()
        if not self._messages:
            mode_note = (
                "项目协作模块已就绪，可调用文件、MCP、Skill、终端与智能 Agent。"
                if self._mode == SessionKind.PROJECT
                else "此会话不加载项目上下文，也不会调用任何 Agent 工具。"
            )
            shortcut_note = (
                "输入 @ 添加文件，输入 / 打开命令与工具"
                if self._mode == SessionKind.PROJECT
                else "直接输入消息即可，此模式不会读取文件或调用工具"
            )
            self.transcript.setHtml(
                f"""
                <div style="color:{theme.muted};text-align:center;margin-top:80px;">
                  <div style="font-family:'Microsoft YaHei UI','Segoe UI';font-size:34px;
                              color:{theme.accent};letter-spacing:3px;margin-bottom:11px;">
                    J.A.R.V.I.S.
                  </div>
                  <div>{mode_note}</div>
                  <div style="margin-top:16px;color:{theme.faint};">
                    {shortcut_note}
                  </div>
                </div>
                """
            )
            return
        scroll_bar = self.transcript.verticalScrollBar()
        previous_scroll = scroll_bar.value()
        was_near_bottom = (
            scroll_bar.maximum() - previous_scroll <= 24
        )
        blocks: list[str] = []
        for message_index, entry in enumerate(self._messages):
            role = entry.role
            content = entry.content
            if role == "user":
                label = "你"
                label_background = theme.accent
                label_color = theme.accent_text
                body_background = theme.accent_soft
                body_color = theme.accent
                border_color = theme.accent_hover
                bubble_margin = "8px 0 14px 58px"
            elif role == "assistant":
                assistant_accent = _assistant_accent(theme)
                label = DISPLAY_NAME
                label_background = _mix_hex(
                    theme.input_bg,
                    assistant_accent,
                    0.18 if not theme.is_light else 0.10,
                )
                label_color = assistant_accent
                body_background = _mix_hex(
                    theme.card,
                    assistant_accent,
                    0.06 if not theme.is_light else 0.025,
                )
                body_color = theme.text
                border_color = _mix_hex(
                    theme.border,
                    assistant_accent,
                    0.55,
                )
                bubble_margin = "8px 58px 14px 0"
            elif role == "error":
                label = "错误"
                label_background = theme.card
                label_color = theme.danger
                body_background = theme.input_bg
                body_color = theme.danger
                border_color = theme.danger
                bubble_margin = "7px 28px 12px 28px"
            else:
                label = "系统"
                label_background = theme.card
                label_color = theme.muted
                body_background = theme.input_bg
                body_color = theme.muted
                border_color = theme.border
                bubble_margin = "7px 28px 12px 28px"
            if role == "assistant":
                body = _render_assistant_body(entry, theme)
                reasoning = _render_reasoning(
                    entry,
                    theme,
                    self._thinking_phase,
                )
            else:
                body = (
                    f'<div style="white-space:pre-wrap;">'
                    f'{html.escape(content).replace(chr(10), "<br>")}</div>'
                )
                reasoning = ""
            blocks.append(
                f"""
                <div style="margin:{bubble_margin};border:1px solid {border_color};
                            border-left:4px solid {label_color};border-radius:9px;">
                  <div style="background:{label_background};color:{label_color};
                              font-size:11px;font-weight:700;letter-spacing:1px;
                              padding:6px 10px;border-bottom:1px solid {border_color};">
                    {label}
                  </div>
                  <div style="background:{body_background};color:{body_color};
                              line-height:150%;padding:10px 12px;
                              border-radius:0 0 9px 9px;">{reasoning}{body}</div>
                </div>
                """
            )
        self.transcript.document().setDefaultStyleSheet(
            _transcript_stylesheet(theme)
        )
        self.transcript.setHtml("".join(blocks))
        bar = self.transcript.verticalScrollBar()
        if force_scroll or was_near_bottom:
            bar.setValue(bar.maximum())
        else:
            bar.setValue(min(previous_scroll, bar.maximum()))

    def _theme_changed(self, _theme) -> None:  # type: ignore[no-untyped-def]
        self.set_agent_status(self._agent_state, self._agent_text)
        self._render(force_scroll=True)

    def _link_activated(self, url: QUrl) -> None:
        if url.scheme() == "jarvis-code-copy":
            payload = url.toString().split(":", 1)[1]
            uid, _, index_text = payload.partition(":")
            entry = self._entry_by_uid(uid)
            if entry is not None:
                from codex_cil.ui.markdown_renderer import extract_code_blocks

                blocks = extract_code_blocks(entry.content)
                try:
                    code = blocks[int(index_text)]
                except (ValueError, IndexError):
                    return
                QGuiApplication.clipboard().setText(code)
            return
        if url.scheme() == "jarvis-reasoning":
            entry = self._entry_by_uid(url.toString().split(":", 1)[1])
            if entry is not None and entry.has_reasoning:
                entry.reasoning_expanded = not entry.reasoning_expanded
                self._render()
            return
        if url.scheme().lower() in {"http", "https", "mailto"}:
            QDesktopServices.openUrl(url)

    def _entry_by_uid(self, uid: str) -> ChatEntry | None:
        return next(
            (entry for entry in self._messages if entry.uid == uid),
            None,
        )


def _render_assistant_body(entry: ChatEntry, theme: ThemeSpec) -> str:
    key = (theme.identifier, entry.content, entry.streaming)
    if entry.cached_body_key == key:
        return entry.cached_body_html
    entry.cached_body_key = key
    if entry.streaming:
        entry.cached_body_html = _render_streaming_body(
            entry.content,
            theme,
        )
        return entry.cached_body_html
    entry.cached_body_html = render_markdown(
        entry.content,
        theme,
        copy_prefix=f"jarvis-code-copy:{entry.uid}:",
    )
    return entry.cached_body_html


def _render_streaming_body(content: str, theme: ThemeSpec) -> str:
    escaped = html.escape(content).replace("\n", "<br>")
    return (
        f'<div style="white-space:pre-wrap;color:{theme.text};'
        f'font-family:\'Microsoft YaHei UI\',\'Segoe UI\';line-height:150%;">'
        f"{escaped}"
        f'<span style="color:{_assistant_accent(theme)};">▌</span>'
        "</div>"
    )


def _render_reasoning(
    entry: ChatEntry,
    theme: ThemeSpec,
    thinking_phase: int,
) -> str:
    if not entry.has_reasoning:
        if entry.reasoning_dirty:
            entry.rebuild_reasoning_aggregate()
            entry.reasoning_dirty = False
        if not entry.has_reasoning and not entry.reasoning_streaming:
            return ""
    if entry.reasoning_dirty:
        entry.rebuild_reasoning_aggregate()
        entry.reasoning_dirty = False
    if not entry.has_reasoning and not entry.reasoning_streaming:
        return ""
    cache_key = (
        theme.identifier,
        entry.reasoning_summary,
        entry.reasoning_raw,
        entry.reasoning_streaming,
        entry.reasoning_expanded,
        thinking_phase,
        _assistant_accent(theme),
        theme.border,
        theme.muted,
    )
    if entry.cached_reasoning_key == cache_key:
        return entry.cached_reasoning_html
    accent = _assistant_accent(theme)
    status = "思考中" if entry.reasoning_streaming else "思考完成"
    marker = "▾" if entry.reasoning_expanded else "▸"
    border = _mix_hex(theme.border, accent, 0.35)
    details: list[str] = []
    if entry.reasoning_expanded:
        if entry.reasoning_summary.strip():
            details.append(
                '<div style="color:%s;font-weight:700;margin:7px 0 3px 0;">'
                "推理摘要</div>%s"
                % (
                    accent,
                    _render_reasoning_text(
                        entry.reasoning_summary,
                        entry.reasoning_streaming,
                        theme,
                    ),
                )
            )
        raw = entry.reasoning_raw.strip()
        summary = entry.reasoning_summary.strip()
        if raw and raw != summary:
            details.append(
                '<div style="color:%s;font-weight:700;margin:7px 0 3px 0;">'
                "模型推理内容</div>%s"
                % (
                    theme.muted,
                    _render_reasoning_text(
                        raw,
                        entry.reasoning_streaming,
                        theme,
                    ),
                )
            )
        if not entry.has_reasoning and entry.reasoning_streaming:
            dots = "." * (thinking_phase % 4)
            details.append(
                f'<div style="color:{theme.muted};margin-top:6px;">'
                f"正在分析上下文并准备回答{dots}</div>"
            )
    entry.cached_reasoning_key = cache_key
    entry.cached_reasoning_html = (
        f'<table width="100%" cellspacing="0" cellpadding="0" '
        f'style="margin:0 0 9px 0;background:transparent;'
        f'border:1px solid {border};border-left:3px solid {accent};">'
        f'<tr><td style="padding:7px 9px;background:transparent;">'
        f'<a href="jarvis-reasoning:{entry.uid}" '
        f'style="color:{accent};text-decoration:none;font-weight:700;">'
        f"{marker} 思考过程 · {status}</a>"
        f'{"".join(details)}</td></tr></table>'
    )
    return entry.cached_reasoning_html


def _render_reasoning_text(
    content: str,
    streaming: bool,
    theme: ThemeSpec,
) -> str:
    if not streaming:
        return render_markdown(content, theme)
    return (
        '<div style="white-space:pre-wrap;">'
        f'{html.escape(content).replace(chr(10), "<br>")}</div>'
    )


def _is_default_session_title(title: str) -> bool:
    return (
        title in {DISPLAY_NAME, "J.A.R.V.I.S.", "新对话", "新纯对话"}
        or title.endswith("新对话")
        or title.endswith("新纯对话")
    )


def _assistant_accent(theme: ThemeSpec) -> str:
    return (
        theme.assistant_accent
        or _ASSISTANT_ACCENTS.get(theme.identifier, theme.warning)
    )


def _mix_hex(base: str, overlay: str, ratio: float) -> str:
    ratio = min(max(ratio, 0.0), 1.0)

    def channels(value: str) -> tuple[int, int, int]:
        normalized = value.lstrip("#")
        if len(normalized) == 3:
            normalized = "".join(character * 2 for character in normalized)
        return tuple(
            int(normalized[index : index + 2], 16)
            for index in (0, 2, 4)
        )

    base_rgb = channels(base)
    overlay_rgb = channels(overlay)
    mixed = (
        round(background + (foreground - background) * ratio)
        for background, foreground in zip(base_rgb, overlay_rgb)
    )
    return "#" + "".join(f"{channel:02x}" for channel in mixed)


def _transcript_stylesheet(theme: ThemeSpec) -> str:
    assistant_accent = _assistant_accent(theme)
    return f"""
      body {{ color: {theme.text}; }}
      h1, h2, h3, h4, h5, h6 {{
        color: {assistant_accent};
        margin-top: 10px;
        margin-bottom: 5px;
      }}
      p {{ margin: 3px 0 7px 0; }}
      ul, ol {{ margin: 4px 0 7px 20px; }}
      li {{ margin: 1px 0; }}
      blockquote {{
        border-left: 3px solid {assistant_accent};
        color: {theme.muted};
        margin: 8px 0;
        padding-left: 10px;
      }}
      a {{ color: {assistant_accent}; text-decoration: underline; }}
      code {{
        font-family: "Cascadia Code", "Consolas";
        color: {theme.warning};
        background-color: {theme.input_bg};
      }}
      pre {{
        font-family: "Cascadia Code", "Consolas";
        white-space: pre-wrap;
        background-color: transparent;
      }}
      table {{
        border-collapse: collapse;
        margin: 7px 0;
      }}
      th, td {{
        border: 1px solid {theme.border_strong};
        padding: 5px 8px;
      }}
      th {{
        background-color: {theme.input_bg};
        color: {assistant_accent};
      }}
    """
