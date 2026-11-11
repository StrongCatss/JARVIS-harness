from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
import tomlkit

from codex_cil.services.agent_capabilities import (
    discover_skills,
    load_mcp_servers,
    save_mcp_server_states,
    save_skill_states,
)
from codex_cil.services.codex_config import CodexConfig
from codex_cil.ui.dialogs import SettingsDialog


def _write_config(path: Path, skill_path: Path) -> None:
    path.write_text(
        """
model = "test-model"
model_provider = "test"

[model_providers.test]
base_url = "http://localhost"
wire_api = "responses"

[mcp_servers.alpha]
command = "alpha.exe"
enabled = true

[mcp_servers.beta]
url = "https://example.invalid/mcp"
enabled = false

[[skills.config]]
path = "%s"
enabled = false
"""
        % str(skill_path).replace("\\", "\\\\"),
        encoding="utf-8",
    )


def test_mcp_and_skill_states_are_loaded_and_saved(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    alpha_skill = skills_root / "alpha-skill"
    alpha_skill.mkdir(parents=True)
    skill_file = alpha_skill / "SKILL.md"
    skill_file.write_text(
        "---\nname: alpha-skill\ndescription: Alpha skill\n---\n",
        encoding="utf-8",
    )
    beta_skill = skills_root / "beta-skill"
    beta_skill.mkdir()
    (beta_skill / "SKILL.md").write_text(
        "---\nname: beta-skill\ndescription: Beta skill\n---\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.toml"
    _write_config(config_path, skill_file)

    servers = load_mcp_servers(config_path)
    skills = discover_skills([skills_root], config_path)

    assert [server.name for server in servers] == ["alpha", "beta"]
    assert servers[0].enabled
    assert not servers[1].enabled
    assert {skill.name for skill in skills} == {"alpha-skill", "beta-skill"}
    assert next(skill for skill in skills if skill.name == "alpha-skill").enabled is False

    save_mcp_server_states(config_path, {"alpha": False, "beta": True})
    save_skill_states(
        config_path,
        {
            alpha_skill / "SKILL.md": True,
            beta_skill / "SKILL.md": False,
        },
    )
    document = tomlkit.parse(config_path.read_text(encoding="utf-8"))

    assert document["mcp_servers"]["alpha"]["enabled"] is False
    assert document["mcp_servers"]["beta"]["enabled"] is True
    entries = document["skills"]["config"]
    states = {
        Path(str(entry["path"])).parent.name: bool(entry["enabled"])
        for entry in entries
    }
    assert states == {"alpha-skill": True, "beta-skill": False}


def test_settings_dialog_exposes_mcp_and_skill_tabs(
    qtbot,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "demo"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\nname: demo\ndescription: Demo skill\n---\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.toml"
    _write_config(config_path, skill_file)
    config = CodexConfig.load(config_path)

    dialog = SettingsDialog(
        config,
        "codex.exe",
        32000,
        skill_roots=[skills_root],
    )
    qtbot.addWidget(dialog)

    assert dialog.tabs.count() == 4
    assert dialog.mcp_list.count() == 2
    assert dialog.skill_list.count() >= 1
    dialog.mcp_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    dialog.mcp_list.item(1).setCheckState(Qt.CheckState.Checked)
    dialog.skill_list.item(0).setCheckState(Qt.CheckState.Checked)
    dialog.accept()

    updated = tomlkit.parse(config_path.read_text(encoding="utf-8"))
    assert updated["mcp_servers"]["alpha"]["enabled"] is False
    assert updated["mcp_servers"]["beta"]["enabled"] is True
    assert updated["skills"]["config"][0]["enabled"] is True
    assert dialog.capabilities_changed
