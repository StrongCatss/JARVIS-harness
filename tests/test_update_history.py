from __future__ import annotations

import tomllib
from pathlib import Path

from codex_cil import __version__
from codex_cil.paths import update_history_path
from codex_cil.ui.dialogs import UpdateHistoryDialog


def test_update_history_exists_and_matches_package_version() -> None:
    path = update_history_path()
    text = path.read_text(encoding="utf-8")
    project = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )

    assert path.exists()
    assert project["project"]["version"] == __version__
    assert f"## {__version__}" in text
    assert "赛博朋克2077" in text
    assert "MCP" in text
    assert "Skill" in text


def test_update_history_dialog_renders_markdown(
    qtbot,  # type: ignore[no-untyped-def]
) -> None:
    dialog = UpdateHistoryDialog()
    qtbot.addWidget(dialog)

    text = dialog.browser.toPlainText()
    assert "贾维斯 J.A.R.V.I.S. 更新记录" in text
    assert __version__ in text
