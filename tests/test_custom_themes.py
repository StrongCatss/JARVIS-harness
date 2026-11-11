from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from codex_cil.services.theme_store import (
    CustomThemeStore,
    THEME_COLOR_FIELDS,
    ThemeStoreError,
)
from codex_cil.ui.dialogs import ThemeEditorDialog
from codex_cil.ui.theme_system import THEMES


def _custom_theme():  # type: ignore[no-untyped-def]
    return replace(
        THEMES["hacker"],
        identifier="custom_test",
        name="测试主题",
        description="自定义测试",
        assistant_accent="#73B7FF",
    )


def test_custom_theme_store_roundtrip_and_export(tmp_path: Path) -> None:
    store = CustomThemeStore(tmp_path / ".JARVIS" / "themes.json")
    theme = _custom_theme()

    store.save([theme])
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].identifier == theme.identifier
    assert loaded[0].name == theme.name
    assert loaded[0].accent.lower() == theme.accent.lower()
    assert store.path.parent.name == ".JARVIS"

    exported = tmp_path / "测试主题.jarvis-theme.json"
    store.export_theme(theme, exported)
    imported = store.import_themes(exported)
    assert imported[0].identifier == theme.identifier
    assert imported[0].assistant_accent == theme.assistant_accent


def test_custom_theme_store_rejects_invalid_color(tmp_path: Path) -> None:
    store = CustomThemeStore(tmp_path / "themes.json")
    invalid = replace(_custom_theme(), accent="not-a-color")

    with pytest.raises(ThemeStoreError):
        store.save([invalid])


def test_theme_editor_contains_every_theme_element(
    qtbot,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    dialog = ThemeEditorDialog(
        [],
        THEMES["hacker"],
        CustomThemeStore(tmp_path / ".JARVIS" / "themes.json"),
    )
    qtbot.addWidget(dialog)

    assert set(dialog.color_inputs) == {
        field_name for field_name, _label in THEME_COLOR_FIELDS
    }
    assert len(dialog.color_inputs) == 18
    assert dialog.name.text()
    assert dialog.import_button.text() == "导入…"
    assert dialog.export_button.text() == "导出…"
    assert dialog.save_button.text() == "保存并应用"
    assert dialog.theme_combo.count() == 1

    draft = dialog._draft_theme()
    assert draft.identifier.startswith("custom_")
    assert draft.assistant_accent

    dialog.name.setText("我的主题")
    dialog._save_theme()
    assert dialog.saved_theme is not None
    assert dialog.saved_theme.name == "我的主题"
    assert dialog._store.path.exists()

