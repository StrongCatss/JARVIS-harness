from __future__ import annotations

from codex_cil.ui.server_request_dialog import ServerRequestDialog


def test_command_dialog_builds_modern_approval_result(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = ServerRequestDialog(
        "item/commandExecution/requestApproval",
        {"command": "git status", "cwd": "C:/work"},
    )
    qtbot.addWidget(dialog)

    dialog._approval_choice("session")

    assert dialog.result_payload == {"decision": "acceptForSession"}
    assert dialog.approval_choice == "session"


def test_user_input_dialog_collects_answers(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = ServerRequestDialog(
        "item/tool/requestUserInput",
        {
            "questions": [
                {
                    "id": "choice",
                    "header": "选择",
                    "question": "继续吗？",
                    "options": [{"label": "继续", "description": "继续任务"}],
                }
            ]
        },
    )
    qtbot.addWidget(dialog)

    dialog._submit_user_input()

    assert dialog.result_payload == {
        "answers": {"choice": {"answers": ["继续"]}}
    }


def test_project_approval_choice_uses_single_accept(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = ServerRequestDialog(
        "item/fileChange/requestApproval",
        {"reason": "update files"},
    )
    qtbot.addWidget(dialog)

    dialog._approval_choice("project")

    assert dialog.result_payload == {"decision": "accept"}
    assert dialog.approval_choice == "project"
