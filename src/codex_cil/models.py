from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SessionKind(StrEnum):
    PROJECT = "project"
    PURE_CHAT = "chat"


@dataclass(slots=True)
class Project:
    id: int
    name: str
    path: str
    created_at: str
    updated_at: str

    @property
    def instructions_path(self) -> str:
        from codex_cil.paths import project_instructions_path

        return str(project_instructions_path(self.path))


@dataclass(slots=True)
class Session:
    id: int
    kind: SessionKind
    project_id: int | None
    title: str
    backend_id: str | None
    model: str | None
    created_at: str
    updated_at: str
    pinned: int = 0
    archived: int = 0


@dataclass(slots=True)
class Message:
    id: int
    kind: SessionKind
    session_id: int
    role: str
    content: str
    metadata_json: str
    created_at: str


@dataclass(slots=True)
class UsageStats:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0
    context_tokens: int = 0
    context_window: int | None = None

    @property
    def uncached_input_tokens(self) -> int:
        return max(self.input_tokens - self.cached_input_tokens, 0)

    @property
    def cache_hit_ratio(self) -> float | None:
        if self.input_tokens <= 0:
            return None
        return self.cached_input_tokens / self.input_tokens

    @property
    def context_ratio(self) -> float | None:
        if not self.context_window or self.context_window <= 0:
            return None
        return min(max(self.context_tokens / self.context_window, 0.0), 1.0)

    def estimated_cost(
        self,
        input_price_per_million: float,
        output_price_per_million: float,
        cached_input_price_per_million: float = 0.0,
    ) -> float:
        return (
            self.uncached_input_tokens * input_price_per_million
            + self.cached_input_tokens * cached_input_price_per_million
            + self.output_tokens * output_price_per_million
        ) / 1_000_000
