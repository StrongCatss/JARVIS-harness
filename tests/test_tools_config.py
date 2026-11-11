from __future__ import annotations

from pathlib import Path

import pytest
import tomlkit

from codex_cil.services.tools_config import (
    ToolsConfigurationError,
    configure_codex_mcp,
    inspect_tools,
)


def _make_tools(root: Path) -> Path:
    docs = root / "ai-docs" / "openai-docs" / "docs" / "markdown"
    docs.mkdir(parents=True)
    (docs / "index.md").write_text("# OpenAI\n\nOffline docs.", encoding="utf-8")
    for skill_name in ("openai-docs", "china-first-research"):
        skill = root / "skills" / skill_name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"# {skill_name}", encoding="utf-8")
    mcp = root / "mcp" / "china-sources" / "bin"
    mcp.mkdir(parents=True)
    (mcp / "china-sources.exe").write_bytes(b"stub")
    (root / "tools-catalog.json").write_text("{}", encoding="utf-8")
    return root


def test_inspect_tools_accepts_complete_offline_layout(tmp_path: Path) -> None:
    root = _make_tools(tmp_path / "Tools")

    layout = inspect_tools(root)

    assert layout.root == root.resolve()
    assert layout.docs_root.name == "markdown"
    assert layout.skills_root.name == "skills"
    assert layout.mcp_executable.name == "china-sources.exe"


def test_inspect_tools_rejects_incomplete_layout(tmp_path: Path) -> None:
    root = tmp_path / "Tools"
    root.mkdir()

    with pytest.raises(ToolsConfigurationError, match="资料不完整"):
        inspect_tools(root)


def test_configure_codex_mcp_preserves_existing_config(tmp_path: Path) -> None:
    tools = inspect_tools(_make_tools(tmp_path / "Tools"))
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
model = "configured-model"
model_provider = "custom"

[mcp_servers.other]
command = "keep-me"

[mcp_servers.china-sources]
command = "python"
args = ["old-server.py"]

[mcp_servers.china-sources.env]
PYTHONUTF8 = "1"

[model_providers.custom]
base_url = "https://example.test/v1"
wire_api = "responses"
experimental_bearer_token = "do-not-touch"
""",
        encoding="utf-8",
    )

    configure_codex_mcp(tools, config_path)
    document = tomlkit.parse(config_path.read_text(encoding="utf-8"))

    assert document["model"] == "configured-model"
    assert document["model_providers"]["custom"]["experimental_bearer_token"] == (
        "do-not-touch"
    )
    assert document["mcp_servers"]["other"]["command"] == "keep-me"
    server = document["mcp_servers"]["china-sources"]
    assert server["command"] == str(tools.mcp_executable)
    assert server["enabled"] is True
    assert "args" not in server
    assert "PYTHONUTF8" not in server["env"]
