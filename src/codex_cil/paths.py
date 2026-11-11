from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "CodexCIL"


def app_data_dir() -> Path:
    override = os.getenv("CODEX_CIL_DATA_DIR")
    if override:
        path = Path(override).expanduser().resolve()
    else:
        base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        path = Path(base or Path.home()).resolve() / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_path() -> Path:
    return app_data_dir() / "codex-cil.db"


def jarvis_home_dir() -> Path:
    override = os.getenv("JARVIS_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".JARVIS").resolve()


def custom_themes_path() -> Path:
    return jarvis_home_dir() / "themes.json"


def update_history_path() -> Path:
    override = os.getenv("CODEX_CIL_UPDATE_HISTORY")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "UPDATE_HISTORY.md"
    return Path(__file__).resolve().parents[2] / "UPDATE_HISTORY.md"


def codex_home_dir() -> Path:
    override = os.getenv("CODEX_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".codex").resolve()


def codex_config_path() -> Path:
    return codex_home_dir() / "config.toml"


def tools_dir() -> Path:
    override = os.getenv("CODEX_CIL_TOOLS_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "Tools"
    return Path(__file__).resolve().parents[3] / "Tools"


def project_instructions_path(project_root: str | Path) -> Path:
    return Path(project_root).expanduser().resolve() / "AGENTS.md"


def normalize_workspace(path: str | Path) -> Path:
    candidate = Path(path).expanduser().resolve()
    if not candidate.exists():
        raise FileNotFoundError(f"工作目录不存在: {candidate}")
    if not candidate.is_dir():
        raise NotADirectoryError(f"路径不是目录: {candidate}")
    return candidate
