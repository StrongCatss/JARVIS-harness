from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomlkit


class AgentCapabilitiesError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class McpServerSetting:
    name: str
    enabled: bool
    transport: str
    target: str


@dataclass(frozen=True, slots=True)
class SkillSetting:
    name: str
    description: str
    path: Path
    enabled: bool
    scope: str


def load_mcp_servers(config_path: str | Path) -> list[McpServerSetting]:
    document = _load_document(config_path)
    servers = document.get("mcp_servers") or {}
    results: list[McpServerSetting] = []
    for name, raw in servers.items():
        if not isinstance(raw, dict):
            continue
        url = str(raw.get("url") or "").strip()
        command = str(raw.get("command") or "").strip()
        transport = str(
            raw.get("transport")
            or ("HTTP" if url else "stdio")
        ).strip()
        results.append(
            McpServerSetting(
                name=str(name),
                enabled=bool(raw.get("enabled", True)),
                transport=transport,
                target=url or command or "未提供启动命令或 URL",
            )
        )
    return sorted(results, key=lambda item: item.name.lower())


def save_mcp_server_states(
    config_path: str | Path,
    states: dict[str, bool],
) -> Path:
    path = Path(config_path).expanduser().resolve()
    document = _load_document(path)
    if "mcp_servers" not in document:
        return _write_document(path, document)
    servers = document["mcp_servers"]
    for name, enabled in states.items():
        if name not in servers or not isinstance(servers[name], dict):
            continue
        servers[name]["enabled"] = bool(enabled)
    return _write_document(path, document)


def discover_skills(
    roots: list[str | Path],
    config_path: str | Path,
) -> list[SkillSetting]:
    document = _load_document(config_path)
    overrides = _skill_overrides(document)
    discovered: dict[str, SkillSetting] = {}
    for raw_root in roots:
        root = Path(raw_root).expanduser().resolve()
        if not root.is_dir():
            continue
        for skill_file in root.rglob("SKILL.md"):
            try:
                resolved = skill_file.resolve()
            except OSError:
                continue
            key = str(resolved).casefold()
            if key in discovered:
                continue
            name, description = _read_skill_metadata(resolved)
            discovered[key] = SkillSetting(
                name=name,
                description=description,
                path=resolved,
                enabled=overrides.get(str(resolved).casefold(), True),
                scope=_skill_scope(resolved, root),
            )
    return sorted(
        discovered.values(),
        key=lambda item: (item.scope, item.name.casefold(), str(item.path)),
    )


def save_skill_states(
    config_path: str | Path,
    states: dict[Path, bool],
) -> Path:
    path = Path(config_path).expanduser().resolve()
    document = _load_document(path)
    if "skills" not in document:
        document["skills"] = tomlkit.table()
    skills = document["skills"]
    if "config" not in skills:
        skills["config"] = tomlkit.aot()
    entries = skills["config"]
    for skill_path, enabled in states.items():
        normalized = str(skill_path.expanduser().resolve()).casefold()
        matching: Any | None = None
        for entry in entries:
            raw_path = entry.get("path") if isinstance(entry, dict) else None
            if raw_path and str(
                Path(str(raw_path)).expanduser().resolve()
            ).casefold() == normalized:
                matching = entry
                break
        if matching is None:
            matching = tomlkit.table()
            matching["path"] = str(skill_path.expanduser().resolve())
            entries.append(matching)
        matching["enabled"] = bool(enabled)
    return _write_document(path, document)


def _load_document(path: str | Path):  # type: ignore[no-untyped-def]
    config_path = Path(path).expanduser().resolve()
    try:
        return (
            tomlkit.parse(config_path.read_text(encoding="utf-8"))
            if config_path.exists()
            else tomlkit.document()
        )
    except Exception as exc:
        raise AgentCapabilitiesError(
            f"无法解析 Codex 配置，未写入任何修改：{config_path}\n{exc}"
        ) from exc


def _write_document(path: Path, document) -> Path:  # type: ignore[no-untyped-def]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".codex-cil.tmp")
    try:
        temporary.write_text(tomlkit.dumps(document), encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        raise AgentCapabilitiesError(
            f"无法写入 Codex 配置：{path}\n{exc}"
        ) from exc
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)
    return path


def _skill_overrides(document) -> dict[str, bool]:  # type: ignore[no-untyped-def]
    skills = document.get("skills") or {}
    entries = skills.get("config") if isinstance(skills, dict) else None
    result: dict[str, bool] = {}
    for entry in entries or []:
        if not isinstance(entry, dict) or not entry.get("path"):
            continue
        raw_path = str(entry["path"]).strip()
        if not raw_path:
            continue
        path = Path(raw_path).expanduser()
        if path.name.casefold() != "skill.md":
            path = path / "SKILL.md"
        normalized = str(path.resolve()).casefold()
        result[normalized] = bool(entry.get("enabled", True))
    return result


def _read_skill_metadata(path: Path) -> tuple[str, str]:
    name = path.parent.name
    description = ""
    try:
        text = path.read_text(encoding="utf-8")[:16_384]
    except OSError:
        return name, description
    name_match = re.search(r"(?m)^name:\s*[\"']?([^\"'\n]+)", text)
    description_match = re.search(
        r"(?m)^description:\s*[\"']?(.+?)[\"']?\s*$",
        text,
    )
    if name_match:
        name = name_match.group(1).strip()
    if description_match:
        description = description_match.group(1).strip()
    return name, description


def _skill_scope(path: Path, root: Path) -> str:
    normalized = str(path).replace("\\", "/").lower()
    if "/.system/" in normalized:
        return "系统"
    if "/plugins/cache/" in normalized:
        return "插件"
    if root.parent.name.casefold() == "tools":
        return "离线工具"
    return "用户"
