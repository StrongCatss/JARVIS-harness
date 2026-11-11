from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


_COMMAND_APPROVAL_METHODS = {
    "item/commandExecution/requestApproval",
    "execCommandApproval",
}
_FILE_APPROVAL_METHODS = {
    "item/fileChange/requestApproval",
    "applyPatchApproval",
}


class ServerRequestDialog(QDialog):
    def __init__(
        self,
        method: str,
        params: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.method = method
        self.params = params
        self.result_payload: dict[str, Any] | None = None
        self.approval_choice = ""
        self.rejection_reason = ""
        self.inputs: dict[str, QWidget] = {}
        self.resize(720, 520)

        layout = QVBoxLayout(self)
        if method in _COMMAND_APPROVAL_METHODS:
            self.setWindowTitle("命令执行审批")
            self._build_approval(
                layout,
                "智能体请求执行命令",
                self._command_details(),
            )
        elif method in _FILE_APPROVAL_METHODS:
            self.setWindowTitle("文件修改审批")
            self._build_approval(
                layout,
                "智能体请求修改文件",
                self._file_details(),
            )
        elif method == "item/permissions/requestApproval":
            self.setWindowTitle("权限申请")
            self._build_permissions(layout)
        elif method == "item/tool/requestUserInput":
            self.setWindowTitle("智能体需要补充信息")
            self._build_user_input(layout)
        elif method == "mcpServer/elicitation/request":
            self.setWindowTitle("MCP 工具请求确认")
            self._build_elicitation(layout)
        else:
            self.setWindowTitle("Codex 请求")
            label = QLabel(f"暂未支持的服务端请求：{method}")
            label.setWordWrap(True)
            layout.addWidget(label)
            details = QPlainTextEdit()
            details.setReadOnly(True)
            details.setPlainText(
                json.dumps(params, ensure_ascii=False, indent=2)
            )
            layout.addWidget(details, 1)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

    def _command_details(self) -> str:
        lines = [
            f"命令：{self.params.get('command') or '未提供命令预览'}",
            f"目录：{self.params.get('cwd') or '未提供'}",
        ]
        if self.params.get("reason"):
            lines.append(f"原因：{self.params['reason']}")
        network = self.params.get("networkApprovalContext") or {}
        if network:
            lines.append(
                "网络访问："
                f"{network.get('protocol') or '未知协议'} "
                f"{network.get('host') or '未知主机'}"
            )
        amendment = self.params.get("proposedExecpolicyAmendment")
        if amendment:
            lines.append(
                "建议规则：" + json.dumps(amendment, ensure_ascii=False)
            )
        permissions = self.params.get("additionalPermissions")
        if permissions:
            lines.append(
                "附加权限："
                + json.dumps(permissions, ensure_ascii=False, indent=2)
            )
        return "\n".join(lines)

    def _file_details(self) -> str:
        lines = ["智能体请求将补丁或文件变更应用到项目。"]
        if self.params.get("reason"):
            lines.append(f"原因：{self.params['reason']}")
        if self.params.get("grantRoot"):
            lines.append(f"请求目录：{self.params['grantRoot']}")
        return "\n".join(lines)

    def _build_approval(
        self,
        layout: QVBoxLayout,
        title: str,
        details_text: str,
    ) -> None:
        title_label = QLabel(title)
        title_label.setObjectName("SectionTitle")
        layout.addWidget(title_label)
        details = QPlainTextEdit()
        details.setReadOnly(True)
        details.setPlainText(details_text)
        layout.addWidget(details, 1)
        self.rejection_reason_input = QLineEdit()
        self.rejection_reason_input.setPlaceholderText("拒绝原因（可选）")
        layout.addWidget(self.rejection_reason_input)

        actions = QDialogButtonBox()
        once = actions.addButton(
            "仅允许一次",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        session = actions.addButton(
            "本会话允许",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        project = actions.addButton(
            "本项目允许",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        decline = actions.addButton(
            "拒绝",
            QDialogButtonBox.ButtonRole.DestructiveRole,
        )
        cancel = actions.addButton(
            "取消当前回合",
            QDialogButtonBox.ButtonRole.RejectRole,
        )
        once.clicked.connect(lambda: self._approval_choice("once"))
        session.clicked.connect(lambda: self._approval_choice("session"))
        project.clicked.connect(lambda: self._approval_choice("project"))
        decline.clicked.connect(lambda: self._approval_choice("decline"))
        cancel.clicked.connect(lambda: self._approval_choice("cancel"))
        layout.addWidget(actions)

    def _approval_choice(self, choice: str) -> None:
        normalized = "once" if choice == "project" else choice
        self.rejection_reason = self.rejection_reason_input.text().strip()
        self.result_payload = build_approval_result(
            self.method,
            normalized,
            rejection_reason=self.rejection_reason,
        )
        self.approval_choice = choice
        self.accept()

    def _build_permissions(self, layout: QVBoxLayout) -> None:
        hint = QLabel("智能体请求以下额外权限：")
        hint.setObjectName("SectionTitle")
        layout.addWidget(hint)
        details = QPlainTextEdit()
        details.setReadOnly(True)
        details.setPlainText(
            json.dumps(self.params.get("permissions") or {}, ensure_ascii=False, indent=2)
        )
        layout.addWidget(details, 1)
        actions = QDialogButtonBox()
        once = actions.addButton("本轮允许", QDialogButtonBox.ButtonRole.AcceptRole)
        session = actions.addButton(
            "本会话允许",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        project = actions.addButton(
            "本项目允许",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        decline = actions.addButton("拒绝", QDialogButtonBox.ButtonRole.DestructiveRole)
        once.clicked.connect(lambda: self._permission_choice("turn"))
        session.clicked.connect(lambda: self._permission_choice("session"))
        project.clicked.connect(lambda: self._permission_choice("project"))
        decline.clicked.connect(lambda: self._permission_choice("decline"))
        layout.addWidget(actions)

    def _permission_choice(self, choice: str) -> None:
        self.approval_choice = choice
        if choice == "decline":
            self.result_payload = {"permissions": {}, "scope": "turn"}
        else:
            scope = "session" if choice in {"session", "project"} else "turn"
            self.result_payload = {
                "permissions": self.params.get("permissions") or {},
                "scope": scope,
            }
        self.accept()

    def _build_user_input(self, layout: QVBoxLayout) -> None:
        form = QFormLayout()
        for question in self.params.get("questions") or []:
            question_id = str(question.get("id") or "")
            label = str(question.get("question") or question.get("header") or "")
            options = question.get("options") or []
            if options:
                field = QComboBox()
                field.setEditable(bool(question.get("isOther")))
                for option in options:
                    field.addItem(
                        str(option.get("label") or option.get("description") or ""),
                        str(option.get("label") or ""),
                    )
                if question.get("isOther"):
                    field.setEditText("")
            else:
                field = QLineEdit()
                if question.get("isSecret"):
                    field.setEchoMode(QLineEdit.EchoMode.Password)
            self.inputs[question_id] = field
            form.addRow(label, field)
        layout.addLayout(form)
        layout.addStretch(1)
        actions = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        actions.accepted.connect(self._submit_user_input)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)

    def _submit_user_input(self) -> None:
        answers: dict[str, dict[str, list[str]]] = {}
        for question_id, field in self.inputs.items():
            if isinstance(field, QComboBox):
                value = field.currentText().strip()
            elif isinstance(field, QLineEdit):
                value = field.text().strip()
            else:
                value = ""
            answers[question_id] = {"answers": [value]}
        self.result_payload = {"answers": answers}
        self.accept()

    def _build_elicitation(self, layout: QVBoxLayout) -> None:
        message = QLabel(str(self.params.get("message") or "MCP 工具请求确认"))
        message.setWordWrap(True)
        layout.addWidget(message)
        mode = str(self.params.get("mode") or "")
        if mode == "url":
            url = QLabel(str(self.params.get("url") or ""))
            url.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            url.setWordWrap(True)
            layout.addWidget(url)
        else:
            schema = self.params.get("requestedSchema") or {}
            properties = schema.get("properties") or {}
            form = QFormLayout()
            for name, spec in properties.items():
                field = _field_for_schema(spec)
                self.inputs[name] = field
                form.addRow(
                    str(spec.get("title") or spec.get("description") or name),
                    field,
                )
            layout.addLayout(form)
        layout.addStretch(1)
        actions = QDialogButtonBox()
        accept = actions.addButton("接受", QDialogButtonBox.ButtonRole.AcceptRole)
        decline = actions.addButton("拒绝", QDialogButtonBox.ButtonRole.DestructiveRole)
        cancel = actions.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        accept.clicked.connect(self._accept_elicitation)
        decline.clicked.connect(lambda: self._finish_elicitation("decline"))
        cancel.clicked.connect(lambda: self._finish_elicitation("cancel"))
        layout.addWidget(actions)

    def _accept_elicitation(self) -> None:
        content: dict[str, Any] = {}
        for name, field in self.inputs.items():
            if isinstance(field, QCheckBox):
                content[name] = field.isChecked()
            elif isinstance(field, QComboBox):
                content[name] = field.currentData()
            elif isinstance(field, QLineEdit):
                content[name] = field.text()
        self.result_payload = {"action": "accept", "content": content or None}
        self.accept()

    def _finish_elicitation(self, action: str) -> None:
        self.result_payload = {"action": action, "content": None}
        self.accept()


def _field_for_schema(spec: dict[str, Any]) -> QWidget:
    field_type = str(spec.get("type") or "string")
    enum = spec.get("enum")
    if enum:
        field = QComboBox()
        names = spec.get("enumNames") or []
        for index, value in enumerate(enum):
            label = names[index] if index < len(names) else str(value)
            field.addItem(str(label), value)
        return field
    if field_type == "boolean":
        field = QCheckBox()
        field.setChecked(bool(spec.get("default")))
        return field
    field = QLineEdit()
    if spec.get("default") is not None:
        field.setText(str(spec["default"]))
    return field


def build_approval_result(
    method: str,
    choice: str,
    *,
    rejection_reason: str = "",
) -> dict[str, Any]:
    if method in {"execCommandApproval", "applyPatchApproval"}:
        if choice == "decline" and rejection_reason:
            return {
                "decision": {
                    "denied": {"rejection": rejection_reason}
                }
            }
        legacy = {
            "once": "approved",
            "session": "approved_for_session",
            "decline": "denied",
            "cancel": "abort",
        }
        return {"decision": legacy[choice]}
    modern = {
        "once": "accept",
        "session": "acceptForSession",
        "decline": "decline",
        "cancel": "cancel",
    }
    return {"decision": modern[choice]}
