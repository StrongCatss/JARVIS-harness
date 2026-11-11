from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from codex_cil import DISPLAY_NAME
from codex_cil.models import SessionKind, UsageStats
from codex_cil.ui.usage_card import compact_number

_DEEPSEEK_V41_FLASH_CACHE_MISS_PRICE = 2.0
_DEEPSEEK_V41_FLASH_CACHE_HIT_PRICE = 0.04
_DEEPSEEK_V41_FLASH_OUTPUT_PRICE = 8.0


class InspectorPanel(QFrame):
    cancelTurnRequested = Signal()
    steerRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.setMinimumWidth(230)
        self.setMaximumWidth(350)
        self._usage = UsageStats()
        self._plan_explanation = ""
        self._plan: list[dict] = []
        self._current_activity = ""
        self._modified_count = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        header = QLabel("检查器")
        header.setObjectName("SectionTitle")
        layout.addWidget(header)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self.overview = QPlainTextEdit()
        self.overview.setReadOnly(True)
        self.overview.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.usage = QPlainTextEdit()
        self.usage.setReadOnly(True)
        self.activity = QPlainTextEdit()
        self.activity.setReadOnly(True)
        self.task = QPlainTextEdit()
        self.task.setReadOnly(True)
        self.steer_input = QLineEdit()
        self.steer_input.setPlaceholderText("补充要求并发送给当前回合")
        self.steer_button = QPushButton("补充")
        self.cancel_button = QPushButton("停止回合")
        self.cancel_button.setObjectName("Danger")
        self.task_page = QWidget()
        task_layout = QVBoxLayout(self.task_page)
        task_layout.setContentsMargins(0, 0, 0, 0)
        task_layout.addWidget(self.task, 1)
        steer_row = QHBoxLayout()
        steer_row.addWidget(self.steer_input, 1)
        steer_row.addWidget(self.steer_button)
        steer_row.addWidget(self.cancel_button)
        task_layout.addLayout(steer_row)
        self.tabs.addTab(self.overview, "概览")
        self.tabs.addTab(self.task_page, "任务")
        self.tabs.addTab(self.usage, "Token")
        self.tabs.addTab(self.activity, "活动")
        self.steer_button.clicked.connect(self._emit_steer)
        self.steer_input.returnPressed.connect(self._emit_steer)
        self.cancel_button.clicked.connect(self.cancelTurnRequested)
        self.set_session("未选择会话", SessionKind.PURE_CHAT, None)
        self.set_usage(UsageStats())
        self.reset_turn()

    def set_session(
        self,
        title: str,
        kind: SessionKind,
        project_path: str | None,
    ) -> None:
        mode = "项目 Agent" if kind == SessionKind.PROJECT else "纯对话"
        lines = [
            f"会话：{title}",
            f"模式：{mode}",
            f"项目：{project_path or '无'}",
            "",
            f"项目模式已接入 {DISPLAY_NAME} 智能核心。",
            "纯对话模式不会加载工具、文件或项目指令。",
            "",
            "指令来源：",
        ]
        global_agents = Path.home() / ".codex" / "AGENTS.md"
        lines.append(_instruction_source("全局", global_agents))
        if kind == SessionKind.PROJECT and project_path:
            project_agents = Path(project_path) / "AGENTS.md"
            lines.append(_instruction_source("项目", project_agents))
            lines.append("优先级：项目指令覆盖冲突的全局指令。")
        self.overview.setPlainText("\n".join(lines))

    def set_usage(self, usage: UsageStats) -> None:
        self._usage = usage
        percent = (
            f"{usage.context_ratio * 100:.1f}%"
            if usage.context_ratio is not None
            else "未知"
        )
        cache = (
            f"{usage.cache_hit_ratio * 100:.1f}%"
            if usage.cache_hit_ratio is not None
            else "—"
        )
        estimated = usage.estimated_cost(
            _DEEPSEEK_V41_FLASH_CACHE_MISS_PRICE,
            _DEEPSEEK_V41_FLASH_OUTPUT_PRICE,
            _DEEPSEEK_V41_FLASH_CACHE_HIT_PRICE,
        )
        self.usage.setPlainText(
            "\n".join(
                [
                    "上下文",
                    f"  已用       {compact_number(usage.context_tokens)}",
                    f"  总量       {compact_number(usage.context_window)}",
                    f"  占比       {percent}",
                    "",
                    "会话 Token",
                    f"  总计       {compact_number(usage.total_tokens)}",
                    f"  输入       {compact_number(usage.input_tokens)}",
                    f"  未缓存     {compact_number(usage.uncached_input_tokens)}",
                    f"  缓存读取   {compact_number(usage.cached_input_tokens)}",
                    f"  输出       {compact_number(usage.output_tokens)}",
                    f"  推理输出   {compact_number(usage.reasoning_output_tokens)}",
                    f"  缓存命中   {cache}",
                    "  计费口径   DeepSeek V4.1 Flash 高峰价",
                    f"  估算费用   ¥{estimated:.4f}",
                ]
            )
        )

    def add_activity(self, message: str) -> None:
        current = self.activity.toPlainText().splitlines()
        current.append(_redact_sensitive(message))
        self.activity.setPlainText("\n".join(current[-200:]))
        bar = self.activity.verticalScrollBar()
        bar.setValue(bar.maximum())

    def set_plan(
        self,
        explanation: str | None,
        plan: list[dict],
    ) -> None:
        self._plan_explanation = explanation or ""
        self._plan = list(plan)
        self._refresh_task()

    def set_current_activity(self, activity: str) -> None:
        self._current_activity = activity
        self._refresh_task()

    def set_backend_state(self, state: str) -> None:
        lines = [
            line
            for line in self.overview.toPlainText().splitlines()
            if not line.startswith("Codex 会话：")
        ]
        lines.append(f"Codex 会话：{state}")
        self.overview.setPlainText("\n".join(lines))

    def set_protocol_info(self, info: dict) -> None:
        lines = [
            line
            for line in self.overview.toPlainText().splitlines()
            if not line.startswith("Codex 协议：")
        ]
        summary = ", ".join(
            f"{key}={value}"
            for key, value in info.items()
            if key in {
                "userAgent",
                "codexHome",
                "platformFamily",
                "platformOs",
            }
        )
        lines.append(f"Codex 协议：{summary or '已初始化'}")
        self.overview.setPlainText("\n".join(lines))

    def set_modified_count(self, count: int) -> None:
        self._modified_count = count
        self._refresh_task()

    def reset_turn(self) -> None:
        self._plan_explanation = ""
        self._plan = []
        self._current_activity = ""
        self._modified_count = 0
        self._refresh_task()

    def _refresh_task(self) -> None:
        lines = [
            f"当前目标：{self._plan_explanation or '等待智能体建立计划'}",
            "",
        ]
        if self._plan:
            for item in self._plan:
                status = str(item.get("status") or "pending")
                marker = {
                    "completed": "[完成]",
                    "inProgress": "[进行中]",
                    "pending": "[待办]",
                }.get(status, f"[{status}]")
                lines.append(f"{marker} {item.get('step') or ''}")
        else:
            lines.append("暂无计划步骤")
        lines.extend(
            [
                "",
                f"当前工具/命令：{self._current_activity or '无'}",
                f"已修改文件：{self._modified_count} 个",
            ]
        )
        self.task.setPlainText("\n".join(lines))

    def _emit_steer(self) -> None:
        text = self.steer_input.text().strip()
        if not text:
            return
        self.steer_input.clear()
        self.steerRequested.emit(text)

    def set_active_tab(self, index: int) -> None:
        self.tabs.setCurrentIndex(index)


def _redact_sensitive(value: str) -> str:
    value = re.sub(
        r"(?i)(api[_-]?key|token|authorization|bearer)\s*[:=]\s*\S+",
        r"\1=[已脱敏]",
        value,
    )
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[已脱敏密钥]", value)
    return value


def _instruction_source(label: str, path: Path) -> str:
    if not path.exists():
        return f"  {label}：未创建 ({path})"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"  {label}：读取失败 ({exc})"
    duplicates = len(text) - len(text.rstrip())
    return (
        f"  {label}：{path}\n"
        f"    大小 {len(text)} 字符，尾部空白 {duplicates}"
    )
