from __future__ import annotations

from PySide6.QtWidgets import QApplication

from codex_cil.ui.theme_system import THEMES, apply_theme, build_stylesheet


def test_all_themes_have_complete_stylesheets() -> None:
    assert set(THEMES) == {
        "hacker",
        "gundam",
        "ironman",
        "scifi",
        "black_empire",
        "cyberpunk2077",
        "pacific_rim",
        "starcraft",
        "retro_crt",
    }
    for theme in THEMES.values():
        stylesheet = build_stylesheet(theme)
        assert "@" not in stylesheet
        assert theme.accent in stylesheet
        assert theme.text in stylesheet


def test_theme_switch_updates_application_palette(qtbot) -> None:  # type: ignore[no-untyped-def]
    app = QApplication.instance()
    assert app is not None
    selected = apply_theme(app, "black_empire")
    qtbot.wait(10)
    assert selected.identifier == "black_empire"
    assert app.palette().window().color().name() == selected.bg
    assert app.palette().windowText().color().name() == selected.text
    cyberpunk = apply_theme(app, "cyberpunk2077")
    qtbot.wait(10)
    assert cyberpunk.name == "赛博朋克2077"
    assert cyberpunk.accent == "#fcee0a"
    assert cyberpunk.assistant_accent == "#00f0ff"
    pacific = apply_theme(app, "pacific_rim")
    assert pacific.accent == "#00c8d7"
    assert pacific.danger == "#ff4655"
    starcraft = apply_theme(app, "starcraft")
    assert starcraft.assistant_accent == "#56d8ff"
    retro = apply_theme(app, "retro_crt")
    assert retro.accent == "#ffb000"
    assert retro.assistant_accent == "#9cff7a"
    apply_theme(app, "hacker")
