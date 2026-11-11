from __future__ import annotations

from codex_cil.ui.server_request_dialog import build_approval_result


def test_modern_and_legacy_approval_results() -> None:
    assert build_approval_result(
        "item/commandExecution/requestApproval",
        "once",
    ) == {"decision": "accept"}
    assert build_approval_result(
        "item/fileChange/requestApproval",
        "session",
    ) == {"decision": "acceptForSession"}
    assert build_approval_result(
        "execCommandApproval",
        "decline",
    ) == {"decision": "denied"}
    assert build_approval_result(
        "applyPatchApproval",
        "cancel",
    ) == {"decision": "abort"}
