from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from codex_cil.paths import custom_themes_path
from codex_cil.ui.theme_system import THEMES, ThemeSpec


THEME_COLOR_FIELDS: tuple[tuple[str, str], ...] = (
    ("bg", "主窗口背景"),
    ("panel", "面板背景"),
    ("topbar", "顶栏背景"),
    ("card", "卡片背景"),
    ("input_bg", "输入框背景"),
    ("border", "普通边框"),
    ("border_strong", "强调边框"),
    ("text", "主要文字"),
    ("muted", "次要文字"),
    ("faint", "弱化文字"),
    ("accent", "用户主色"),
    ("accent_hover", "用户主色悬停"),
    ("accent_text", "用户主色文字"),
    ("accent_soft", "用户柔和背景"),
    ("selection", "文本选中背景"),
    ("warning", "警告色"),
    ("danger", "错误色"),
    ("assistant_accent", "AI 智能体强调色"),
)

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_FORMAT_NAME = "jarvis-themes"
_FORMAT_VERSION = 1


class ThemeStoreError(ValueError):
    pass


class CustomThemeStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = (
            Path(path).expanduser().resolve()
            if path is not None
            else custom_themes_path()
        )

    def load(self) -> list[ThemeSpec]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ThemeStoreError(f"无法读取主题配置: {exc}") from exc
        if not isinstance(payload, dict):
            raise ThemeStoreError("主题配置文件格式无效。")
        themes = payload.get("themes", [])
        if not isinstance(themes, list):
            raise ThemeStoreError("主题列表格式无效。")
        return [theme_from_payload(item) for item in themes]

    def save(self, themes: list[ThemeSpec]) -> None:
        identifiers: set[str] = set()
        for theme in themes:
            validate_theme(theme)
            if theme.identifier in identifiers:
                raise ThemeStoreError(
                    f"自定义主题标识重复: {theme.identifier}"
                )
            identifiers.add(theme.identifier)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format": _FORMAT_NAME,
            "version": _FORMAT_VERSION,
            "themes": [theme_to_payload(theme) for theme in themes],
        }
        temp_path = self.path.with_suffix(".tmp")
        try:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(self.path)
        except OSError as exc:
            raise ThemeStoreError(f"无法保存主题配置: {exc}") from exc

    def export_theme(self, theme: ThemeSpec, path: str | Path) -> None:
        validate_theme(theme)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format": _FORMAT_NAME,
            "version": _FORMAT_VERSION,
            "theme": theme_to_payload(theme),
        }
        try:
            destination.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            raise ThemeStoreError(f"无法导出主题: {exc}") from exc

    def import_themes(self, path: str | Path) -> list[ThemeSpec]:
        source = Path(path)
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ThemeStoreError(f"无法读取导入文件: {exc}") from exc
        if not isinstance(payload, dict):
            raise ThemeStoreError("导入文件格式无效。")
        if "theme" in payload:
            return [theme_from_payload(payload["theme"])]
        themes = payload.get("themes")
        if not isinstance(themes, list):
            raise ThemeStoreError("导入文件中没有主题。")
        imported = [theme_from_payload(item) for item in themes]
        if not imported:
            raise ThemeStoreError("导入文件中没有主题。")
        return imported


def theme_to_payload(theme: ThemeSpec) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "identifier": theme.identifier,
        "name": theme.name,
        "description": theme.description,
        "is_light": theme.is_light,
    }
    for field_name, _label in THEME_COLOR_FIELDS:
        value = getattr(theme, field_name)
        if field_name == "assistant_accent" and not value:
            value = theme.warning
        payload[field_name] = value
    return payload


def theme_from_payload(payload: Any) -> ThemeSpec:
    if not isinstance(payload, dict):
        raise ThemeStoreError("主题条目格式无效。")
    identifier = str(payload.get("identifier") or "").strip()
    if not identifier:
        raise ThemeStoreError("主题缺少 identifier。")
    if identifier in THEMES:
        raise ThemeStoreError(f"自定义主题不能使用内置标识: {identifier}")

    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 40:
        raise ThemeStoreError("主题名称必须为 1 到 40 个字符。")
    colors: dict[str, str] = {}
    for field_name, label in THEME_COLOR_FIELDS:
        value = payload.get(field_name)
        if field_name == "assistant_accent" and value in {None, ""}:
            value = payload.get("warning")
        color = str(value or "").strip()
        if not _HEX_COLOR.fullmatch(color):
            raise ThemeStoreError(f"{label} 不是有效的 #RRGGBB 颜色。")
        colors[field_name] = color.upper()

    return ThemeSpec(
        identifier=identifier,
        name=name,
        description=str(payload.get("description") or "用户自定义配色"),
        is_light=bool(payload.get("is_light", False)),
        **colors,
    )


def validate_theme(theme: ThemeSpec) -> None:
    if not theme.identifier.strip():
        raise ThemeStoreError("主题缺少 identifier。")
    if theme.identifier in THEMES:
        raise ThemeStoreError(f"自定义主题不能使用内置标识: {theme.identifier}")
    if not theme.name.strip() or len(theme.name.strip()) > 40:
        raise ThemeStoreError("主题名称必须为 1 到 40 个字符。")
    for field_name, label in THEME_COLOR_FIELDS:
        value = getattr(theme, field_name)
        if not isinstance(value, str) or not _HEX_COLOR.fullmatch(value):
            raise ThemeStoreError(f"{label} 不是有效的 #RRGGBB 颜色。")
