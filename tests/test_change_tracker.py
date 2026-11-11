from __future__ import annotations

from pathlib import Path

from codex_cil.services.change_tracker import TurnChangeTracker


def test_turn_change_tracker_restores_files(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    changed = project / "changed.txt"
    deleted = project / "deleted.txt"
    changed.write_text("before", encoding="utf-8")
    deleted.write_text("keep", encoding="utf-8")
    tracker = TurnChangeTracker(
        project,
        tmp_path / "checkpoints",
        session_id=3,
        turn_id="turn-1",
    )

    tracker.track_changes(
        [
            {"path": "changed.txt", "kind": "update", "diff": "-before\n+after"},
            {"path": "deleted.txt", "kind": "delete", "diff": "-keep"},
            {"path": "../outside.txt", "kind": "update", "diff": ""},
        ]
    )
    changed.write_text("after", encoding="utf-8")
    deleted.unlink()

    assert tracker.modified_count == 2
    restored = tracker.restore(["changed.txt", "deleted.txt"])

    assert restored == ["changed.txt", "deleted.txt"]
    assert changed.read_text(encoding="utf-8") == "before"
    assert deleted.read_text(encoding="utf-8") == "keep"
    assert tracker.restore(["changed.txt"]) == ["changed.txt"]
