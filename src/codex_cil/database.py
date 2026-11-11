from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codex_cil.models import Message, Project, Session, SessionKind, UsageStats
from codex_cil.paths import database_path, normalize_workspace


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    """应用元数据存储，不复制 Codex 项目会话的完整正文。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._initialize()

    def _initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            path TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL CHECK (kind IN ('project', 'chat')),
            project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            backend_id TEXT,
            model TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            pinned INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            session_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_sessions_project
            ON sessions(project_id, kind, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_messages_session
            ON messages(kind, session_id, id);

        CREATE TABLE IF NOT EXISTS usage_snapshots (
            kind TEXT NOT NULL,
            session_id INTEGER NOT NULL,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            cached_input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_output_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            context_tokens INTEGER NOT NULL DEFAULT 0,
            context_window INTEGER,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(kind, session_id)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
        with self._lock, self._connection:
            self._connection.executescript(schema)
            columns = {
                row["name"]
                for row in self._connection.execute(
                    "PRAGMA table_info(sessions)"
                ).fetchall()
            }
            if "pinned" not in columns:
                self._connection.execute(
                    "ALTER TABLE sessions ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"
                )
            if "archived" not in columns:
                self._connection.execute(
                    "ALTER TABLE sessions ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
                )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def backup_to(self, destination: str | Path) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        if temporary.exists():
            temporary.unlink()
        with self._lock:
            backup = sqlite3.connect(temporary)
            try:
                self._connection.backup(backup)
            finally:
                backup.close()
        temporary.replace(target)
        return target

    def list_projects(self) -> list[Project]:
        rows = self._connection.execute(
            "SELECT * FROM projects ORDER BY updated_at DESC, name COLLATE NOCASE"
        ).fetchall()
        return [Project(**dict(row)) for row in rows]

    def add_project(self, name: str, path: str) -> Project:
        root = normalize_workspace(path)
        clean_name = name.strip() or root.name
        stamp = utc_now()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "INSERT INTO projects(name, path, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (clean_name, str(root), stamp, stamp),
            )
        return self.get_project(int(cursor.lastrowid))

    def get_project(self, project_id: int) -> Project:
        row = self._connection.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"项目不存在: {project_id}")
        return Project(**dict(row))

    def touch_project(self, project_id: int) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE projects SET updated_at = ? WHERE id = ?",
                (utc_now(), project_id),
            )

    def delete_project(self, project_id: int) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    def normalize_default_session_titles(self) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE sessions
                SET title = '贾维斯'
                WHERE title IN ('J.A.R.V.I.S.', '新纯对话', '新对话')
                   OR title LIKE '% 新对话'
                   OR title LIKE '% 新纯对话'
                """
            )

    def list_sessions(
        self,
        kind: SessionKind,
        project_id: int | None = None,
        *,
        include_archived: bool = False,
    ) -> list[Session]:
        archived_clause = "" if include_archived else " AND archived = 0"
        if kind == SessionKind.PROJECT:
            rows = self._connection.execute(
                f"""
                SELECT * FROM sessions
                WHERE kind = 'project' AND project_id = ?{archived_clause}
                ORDER BY pinned DESC, updated_at DESC, id DESC
                """,
                (project_id,),
            ).fetchall()
        else:
            rows = self._connection.execute(
                f"""
                SELECT * FROM sessions
                WHERE kind = 'chat'{archived_clause}
                ORDER BY pinned DESC, updated_at DESC, id DESC
                """
            ).fetchall()
        return [Session(**{**dict(row), "kind": SessionKind(row["kind"])}) for row in rows]

    def create_session(
        self,
        kind: SessionKind,
        title: str,
        project_id: int | None = None,
        model: str | None = None,
    ) -> Session:
        stamp = utc_now()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO sessions(kind, project_id, title, model, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (kind.value, project_id, title.strip() or "新对话", model, stamp, stamp),
            )
        return self.get_session(int(cursor.lastrowid), kind)

    def get_session(self, session_id: int, kind: SessionKind) -> Session:
        row = self._connection.execute(
            "SELECT * FROM sessions WHERE id = ? AND kind = ?",
            (session_id, kind.value),
        ).fetchone()
        if row is None:
            raise KeyError(f"会话不存在: {kind.value}/{session_id}")
        data = dict(row)
        data["kind"] = SessionKind(data["kind"])
        return Session(**data)

    def update_session_backend_id(self, session_id: int, backend_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE sessions SET backend_id = ?, updated_at = ? WHERE id = ?",
                (backend_id, utc_now(), session_id),
            )

    def update_session_title(self, session_id: int, title: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title, utc_now(), session_id),
            )

    def set_session_pinned(self, session_id: int, pinned: bool) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE sessions SET pinned = ?, updated_at = ? WHERE id = ?",
                (1 if pinned else 0, utc_now(), session_id),
            )

    def set_session_archived(self, session_id: int, archived: bool) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE sessions SET archived = ?, updated_at = ? WHERE id = ?",
                (1 if archived else 0, utc_now(), session_id),
            )

    def touch_session(self, session_id: int) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (utc_now(), session_id),
            )

    def delete_session(self, session_id: int, kind: SessionKind) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM messages WHERE kind = ? AND session_id = ?",
                (kind.value, session_id),
            )
            self._connection.execute(
                "DELETE FROM usage_snapshots WHERE kind = ? AND session_id = ?",
                (kind.value, session_id),
            )
            self._connection.execute(
                "DELETE FROM sessions WHERE id = ? AND kind = ?",
                (session_id, kind.value),
            )

    def add_message(
        self,
        kind: SessionKind,
        session_id: int,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO messages(kind, session_id, role, content, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    kind.value,
                    session_id,
                    role,
                    content,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    utc_now(),
                ),
            )
        self.touch_session(session_id)
        return int(cursor.lastrowid)

    def list_messages(self, kind: SessionKind, session_id: int) -> list[Message]:
        rows = self._connection.execute(
            "SELECT * FROM messages WHERE kind = ? AND session_id = ? ORDER BY id",
            (kind.value, session_id),
        ).fetchall()
        return [
            Message(**{**dict(row), "kind": SessionKind(row["kind"])}) for row in rows
        ]

    def list_messages_page(
        self,
        kind: SessionKind,
        session_id: int,
        *,
        limit: int = 200,
        before_id: int | None = None,
    ) -> tuple[list[Message], bool]:
        if limit <= 0:
            raise ValueError("limit 必须大于 0")
        params: list[object] = [kind.value, session_id]
        before_clause = ""
        if before_id is not None:
            before_clause = " AND id < ?"
            params.append(before_id)
        params.append(limit + 1)
        rows = self._connection.execute(
            f"""
            SELECT * FROM messages
            WHERE kind = ? AND session_id = ?{before_clause}
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        has_more = len(rows) > limit
        page_rows = list(reversed(rows[:limit]))
        return (
            [
                Message(
                    **{
                        **dict(row),
                        "kind": SessionKind(row["kind"]),
                    }
                )
                for row in page_rows
            ],
            has_more,
        )

    def search_messages(
        self,
        query: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        needle = query.strip()
        if not needle:
            return []
        rows = self._connection.execute(
            """
            SELECT m.id, m.kind, m.session_id, m.role, m.content,
                   m.created_at, s.title, s.project_id
            FROM messages m
            JOIN sessions s ON s.id = m.session_id AND s.kind = m.kind
            WHERE m.content LIKE ?
            ORDER BY m.id DESC
            LIMIT ?
            """,
            (f"%{needle}%", limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def save_usage(self, kind: SessionKind, session_id: int, usage: UsageStats) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO usage_snapshots(
                    kind, session_id, input_tokens, cached_input_tokens,
                    output_tokens, reasoning_output_tokens, total_tokens,
                    context_tokens, context_window, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(kind, session_id) DO UPDATE SET
                    input_tokens=excluded.input_tokens,
                    cached_input_tokens=excluded.cached_input_tokens,
                    output_tokens=excluded.output_tokens,
                    reasoning_output_tokens=excluded.reasoning_output_tokens,
                    total_tokens=excluded.total_tokens,
                    context_tokens=excluded.context_tokens,
                    context_window=excluded.context_window,
                    updated_at=excluded.updated_at
                """,
                (
                    kind.value,
                    session_id,
                    usage.input_tokens,
                    usage.cached_input_tokens,
                    usage.output_tokens,
                    usage.reasoning_output_tokens,
                    usage.total_tokens,
                    usage.context_tokens,
                    usage.context_window,
                    utc_now(),
                ),
            )

    def load_usage(self, kind: SessionKind, session_id: int) -> UsageStats:
        row = self._connection.execute(
            "SELECT * FROM usage_snapshots WHERE kind = ? AND session_id = ?",
            (kind.value, session_id),
        ).fetchone()
        if row is None:
            return UsageStats()
        return UsageStats(
            input_tokens=row["input_tokens"],
            cached_input_tokens=row["cached_input_tokens"],
            output_tokens=row["output_tokens"],
            reasoning_output_tokens=row["reasoning_output_tokens"],
            total_tokens=row["total_tokens"],
            context_tokens=row["context_tokens"],
            context_window=row["context_window"],
        )

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self._connection.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO settings(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value),
            )

    def delete_setting(self, key: str) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM settings WHERE key = ?", (key,))
