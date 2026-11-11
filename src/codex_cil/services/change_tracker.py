from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass(slots=True)
class FileSnapshot:
    path: str
    existed: bool
    checkpoint: str
    diff: str = ""
    kind: str = "update"


class TurnChangeTracker:
    def __init__(
        self,
        project_root: str | Path,
        checkpoint_root: str | Path,
        session_id: int,
        turn_id: str,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_turn = "".join(
            character if character.isalnum() or character in "-_" else "_"
            for character in turn_id
        )[:48]
        self.root = (
            Path(checkpoint_root).expanduser().resolve()
            / f"session-{session_id}"
            / f"{stamp}-{safe_turn}"
        )
        self.root.mkdir(parents=True, exist_ok=True)
        self._files: dict[str, FileSnapshot] = {}
        self.aggregate_diff = ""

    @property
    def files(self) -> list[FileSnapshot]:
        return [self._files[path] for path in sorted(self._files)]

    @property
    def modified_count(self) -> int:
        return len(self._files)

    def track_changes(self, changes: list[dict]) -> None:
        for change in changes:
            if not isinstance(change, dict):
                continue
            relative = str(change.get("path") or "").strip()
            if not relative or relative in self._files:
                if relative in self._files and change.get("diff"):
                    self._files[relative].diff = str(change["diff"])
                continue
            target = self._safe_target(relative)
            if target is None:
                continue
            existed = target.exists()
            checkpoint = ""
            if existed and target.is_file():
                snapshot = self.root / "files" / relative
                snapshot.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, snapshot)
                checkpoint = str(snapshot.relative_to(self.root))
            self._files[relative] = FileSnapshot(
                path=relative.replace("\\", "/"),
                existed=existed,
                checkpoint=checkpoint,
                diff=str(change.get("diff") or ""),
                kind=str(change.get("kind") or "update"),
            )
        self._write_manifest()

    def set_aggregate_diff(self, diff: str) -> None:
        self.aggregate_diff = diff
        self._write_manifest()

    def restore(self, paths: list[str] | None = None) -> list[str]:
        selected = paths or list(self._files)
        restored: list[str] = []
        for relative in selected:
            snapshot = self._files.get(relative)
            if snapshot is None:
                continue
            target = self._safe_target(relative)
            if target is None:
                continue
            if snapshot.existed:
                source = self.root / snapshot.checkpoint
                if not source.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            elif target.exists() and target.is_file():
                target.unlink()
            restored.append(snapshot.path)
        return restored

    def _safe_target(self, relative: str) -> Path | None:
        candidate = (self.project_root / relative).resolve()
        if candidate == self.project_root:
            return None
        try:
            candidate.relative_to(self.project_root)
        except ValueError:
            return None
        return candidate

    def _write_manifest(self) -> None:
        payload = {
            "projectRoot": str(self.project_root),
            "aggregateDiff": self.aggregate_diff,
            "files": [asdict(item) for item in self.files],
        }
        (self.root / "manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
