from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from codex_cil.paths import app_data_dir, tools_dir


TOKEN_PATTERN = re.compile(
    r"[A-Za-z][A-Za-z0-9_.:/-]{2,}|[\u4e00-\u9fff]{3,}"
)


@dataclass(frozen=True, slots=True)
class OfflineDocHit:
    path: str
    title: str
    excerpt: str


class OfflineDocsIndex:
    def __init__(
        self,
        docs_root: str | Path | None = None,
        index_path: str | Path | None = None,
    ) -> None:
        self.docs_root = (
            Path(docs_root).expanduser().resolve()
            if docs_root
            else tools_dir() / "ai-docs" / "openai-docs" / "docs" / "markdown"
        )
        self.index_path = (
            Path(index_path).expanduser().resolve()
            if index_path
            else app_data_dir() / "offline-docs-index.sqlite"
        )
        self._lock = threading.RLock()
        self._ready = False

    @property
    def available(self) -> bool:
        return self.docs_root.exists() and any(self.docs_root.rglob("*.md"))

    def ensure_index(self, force: bool = False) -> None:
        with self._lock:
            if self._ready and not force:
                return
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.index_path)
            try:
                fingerprint = self._fingerprint()
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT)"
                )
                row = connection.execute(
                    "SELECT value FROM metadata WHERE key='fingerprint'"
                ).fetchone()
                current = row[0] if row else None
                has_table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='docs_fts'"
                ).fetchone()
                if force or current != fingerprint or not has_table:
                    self._rebuild(connection, fingerprint)
                self._ready = True
            finally:
                connection.close()

    def search(self, query: str, limit: int = 4) -> list[OfflineDocHit]:
        if not self.available:
            return []
        self.ensure_index()
        tokens = _query_tokens(query)
        if not tokens:
            return []
        with self._lock:
            connection = sqlite3.connect(self.index_path)
            connection.row_factory = sqlite3.Row
            try:
                match_query = " OR ".join(
                    f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens[:6]
                )
                try:
                    rows = connection.execute(
                        """
                        SELECT path, title, snippet(docs_fts, 2, '', '', ' … ', 30) AS excerpt,
                               bm25(docs_fts, 0.0, 6.0, 1.0) AS score
                        FROM docs_fts
                        WHERE docs_fts MATCH ?
                        ORDER BY score
                        LIMIT ?
                        """,
                        (match_query, limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = self._fallback_search(connection, tokens, limit)
                return [
                    OfflineDocHit(
                        path=row["path"],
                        title=row["title"] or Path(row["path"]).stem,
                        excerpt=" ".join(str(row["excerpt"] or "").split()),
                    )
                    for row in rows
                ]
            finally:
                connection.close()

    def build_prompt_context(self, query: str, limit: int = 4) -> str:
        hits = self.search(query, limit)
        if not hits:
            return (
                "【离线资料检索】\n"
                "未在 Tools/ai-docs/openai-docs 中找到直接相关摘录。"
                "如果国内来源也不足，请明确说明无法离线核实，不得猜测。\n"
            )
        sections = [
            "【离线资料检索】",
            "以下内容来自 Tools/ai-docs/openai-docs，回答时优先引用：",
        ]
        for index, hit in enumerate(hits, start=1):
            sections.append(
                f"[{index}] {hit.title}\n路径：{hit.path}\n摘录：{hit.excerpt}"
            )
        return "\n\n".join(sections)

    def _rebuild(self, connection: sqlite3.Connection, fingerprint: str) -> None:
        connection.execute("DROP TABLE IF EXISTS docs_fts")
        try:
            connection.execute(
                """
                CREATE VIRTUAL TABLE docs_fts USING fts5(
                    path UNINDEXED,
                    title,
                    body,
                    tokenize='trigram'
                )
                """
            )
        except sqlite3.OperationalError:
            connection.execute(
                """
                CREATE VIRTUAL TABLE docs_fts USING fts5(
                    path UNINDEXED,
                    title,
                    body,
                    tokenize='unicode61'
                )
                """
            )
        files = sorted(self.docs_root.rglob("*.md"))
        with connection:
            for path in files:
                try:
                    body = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                title = _document_title(body, path.stem)
                relative = path.relative_to(self.docs_root).as_posix()
                connection.execute(
                    "INSERT INTO docs_fts(path, title, body) VALUES (?, ?, ?)",
                    (relative, title, body),
                )
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES('fingerprint', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (fingerprint,),
            )

    def _fallback_search(
        self,
        connection: sqlite3.Connection,
        tokens: list[str],
        limit: int,
    ) -> list[sqlite3.Row]:
        token = tokens[0]
        pattern = f"%{token}%"
        return connection.execute(
            """
            SELECT path, title, substr(body, 1, 1200) AS excerpt
            FROM docs_fts
            WHERE body LIKE ? OR title LIKE ?
            LIMIT ?
            """,
            (pattern, pattern, limit),
        ).fetchall()

    def _fingerprint(self) -> str:
        catalog = self.docs_root.parent.parent / "tools-catalog.json"
        if catalog.exists():
            try:
                payload = json.loads(catalog.read_text(encoding="utf-8"))
                return str(payload.get("generatedAt") or catalog.stat().st_mtime_ns)
            except (OSError, json.JSONDecodeError):
                return str(catalog.stat().st_mtime_ns)
        return str(self.docs_root)


def _query_tokens(query: str) -> list[str]:
    values = [match.group(0).strip(".,;:()[]{}") for match in TOKEN_PATTERN.finditer(query)]
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if len(value) < 3 or value.lower() in seen:
            continue
        seen.add(value.lower())
        result.append(value[:80])
    return result


def _document_title(body: str, fallback: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title
        if stripped:
            return stripped[:120]
    return fallback
