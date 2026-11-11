from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import tomlkit


class ToolsConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ToolsLayout:
    root: Path
    docs_root: Path
    skills_root: Path
    openai_skill: Path
    china_skill: Path
    catalog: Path
    mcp_executable: Path


def inspect_tools(root: str | Path) -> ToolsLayout:
    candidate = Path(root).expanduser().resolve()
    if not candidate.is_dir():
        raise ToolsConfigurationError(f"Tools 目录不存在: {candidate}")

    nested = candidate / "Tools"
    if not (candidate / "ai-docs").is_dir() and (nested / "ai-docs").is_dir():
        candidate = nested.resolve()

    layout = ToolsLayout(
        root=candidate,
        docs_root=candidate / "ai-docs" / "openai-docs" / "docs" / "markdown",
        skills_root=candidate / "skills",
        openai_skill=candidate / "skills" / "openai-docs" / "SKILL.md",
        china_skill=candidate
        / "skills"
        / "china-first-research"
        / "SKILL.md",
        catalog=candidate / "tools-catalog.json",
        mcp_executable=candidate
        / "mcp"
        / "china-sources"
        / "bin"
        / "china-sources.exe",
    )
    missing = [
        str(path)
        for path in (
            layout.catalog,
            layout.openai_skill,
            layout.china_skill,
            layout.mcp_executable,
        )
        if not path.is_file()
    ]
    if not layout.docs_root.is_dir() or not any(layout.docs_root.rglob("*.md")):
        missing.append(str(layout.docs_root))
    if missing:
        raise ToolsConfigurationError(
            "Tools 离线资料不完整，缺少：\n" + "\n".join(missing)
        )
    return layout


def configure_codex_mcp(
    layout: ToolsLayout,
    config_path: str | Path,
    *,
    enabled: bool = True,
) -> Path:
    path = Path(config_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        document = (
            tomlkit.parse(path.read_text(encoding="utf-8"))
            if path.exists()
            else tomlkit.document()
        )
    except Exception as exc:
        raise ToolsConfigurationError(
            f"无法解析 Codex 配置，未写入任何修改：{path}\n{exc}"
        ) from exc

    if "mcp_servers" not in document:
        document["mcp_servers"] = tomlkit.table()
    servers = document["mcp_servers"]
    if "china-sources" not in servers:
        servers["china-sources"] = tomlkit.table()
    server = servers["china-sources"]
    server["command"] = str(layout.mcp_executable)
    server["enabled"] = enabled
    server["startup_timeout_sec"] = 30
    server["tool_timeout_sec"] = 90
    server["default_tools_approval_mode"] = "approve"
    for legacy_key in ("args", "url"):
        if legacy_key in server:
            del server[legacy_key]
    if "env" not in server:
        server["env"] = tomlkit.inline_table()
    server["env"]["CHINA_SOURCE_MAX_BYTES"] = "5000000"
    if "PYTHONUTF8" in server["env"]:
        del server["env"]["PYTHONUTF8"]

    temporary = path.with_name(path.name + ".codex-cil.tmp")
    try:
        temporary.write_text(tomlkit.dumps(document), encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        raise ToolsConfigurationError(f"无法写入 Codex 配置：{path}\n{exc}") from exc
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)
    return path


def activate_tools_environment(layout: ToolsLayout) -> None:
    os.environ["CODEX_CIL_TOOLS_DIR"] = str(layout.root)
