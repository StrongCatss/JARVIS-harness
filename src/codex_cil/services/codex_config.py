from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import tomlkit

from codex_cil import DISPLAY_NAME
from codex_cil.paths import codex_config_path, codex_home_dir


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    identifier: str
    name: str
    base_url: str
    wire_api: str
    env_key: str | None = None
    bearer_token: str | None = field(default=None, repr=False)
    requires_openai_auth: bool = False
    http_headers: dict[str, str] = field(default_factory=dict)
    env_http_headers: dict[str, str] = field(default_factory=dict)
    query_params: dict[str, str] = field(default_factory=dict)

    @property
    def has_authentication(self) -> bool:
        return bool(self.bearer_token or self.env_key or self.requires_openai_auth)

    def resolved_bearer_token(self) -> str | None:
        if self.bearer_token:
            return self.bearer_token
        if self.env_key:
            value = os.getenv(self.env_key)
            if value:
                return value
        if self.requires_openai_auth:
            return _read_openai_api_key()
        return None

    def request_url(self) -> str:
        base = self.base_url.rstrip("/")
        if self.wire_api == "chat":
            path = "/chat/completions"
        else:
            path = "/responses"
        if not base.endswith(path):
            base += path
        return base

    def request_headers(self) -> dict[str, str]:
        headers = dict(self.http_headers)
        for header, env_name in self.env_http_headers.items():
            value = os.getenv(env_name)
            if value:
                headers[header] = value
        token = self.resolved_bearer_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers


@dataclass(frozen=True, slots=True)
class CodexConfig:
    codex_home: Path
    config_path: Path
    model: str
    provider_id: str
    provider: ProviderConfig
    reasoning_effort: str | None = None
    verbosity: str | None = None
    mcp_server_ids: tuple[str, ...] = ()
    load_error: str | None = None

    @property
    def is_configured(self) -> bool:
        if not self.model or not self.provider.base_url or self.load_error:
            return False
        return (
            self.provider.resolved_bearer_token() is not None
            or not self.provider.has_authentication
        )

    @property
    def auth_description(self) -> str:
        if self.provider.bearer_token:
            return "config.toml Provider Token"
        if self.provider.env_key:
            return f"环境变量 {self.provider.env_key}"
        if self.provider.requires_openai_auth:
            return f"{DISPLAY_NAME} 安全凭证中的 API Key"
        return "无需认证"

    @classmethod
    def load(cls, path: str | Path | None = None) -> CodexConfig:
        config_path = Path(path).expanduser().resolve() if path else codex_config_path()
        codex_home = config_path.parent
        try:
            document = (
                tomlkit.parse(config_path.read_text(encoding="utf-8"))
                if config_path.exists()
                else tomlkit.document()
            )
        except Exception as exc:
            return cls(
                codex_home=codex_home,
                config_path=config_path,
                model="",
                provider_id="openai",
                provider=ProviderConfig(
                    identifier="openai",
                    name="OpenAI",
                    base_url="https://api.openai.com/v1",
                    wire_api="responses",
                    requires_openai_auth=True,
                ),
                load_error=f"无法读取 {config_path}: {exc}",
            )

        model = str(document.get("model") or "").strip()
        provider_id = str(document.get("model_provider") or "openai").strip()
        providers = document.get("model_providers") or {}
        mcp_servers = document.get("mcp_servers") or {}
        raw_provider = providers.get(provider_id) if provider_id in providers else None
        raw_provider = dict(raw_provider or {})

        if provider_id == "openai":
            base_url = str(
                document.get("openai_base_url") or "https://api.openai.com/v1"
            ).strip()
            requires_openai_auth = True
        else:
            base_url = str(raw_provider.get("base_url") or "").strip()
            requires_openai_auth = bool(
                raw_provider.get("requires_openai_auth") or False
            )

        provider = ProviderConfig(
            identifier=provider_id,
            name=str(raw_provider.get("name") or provider_id),
            base_url=base_url,
            wire_api=str(raw_provider.get("wire_api") or "responses"),
            env_key=(
                str(raw_provider["env_key"]).strip()
                if raw_provider.get("env_key")
                else None
            ),
            bearer_token=(
                str(
                    raw_provider.get("experimental_bearer_token")
                    or raw_provider.get("api_key")
                    or raw_provider.get("bearer_token")
                    or ""
                )
                or None
            ),
            requires_openai_auth=requires_openai_auth,
            http_headers=_string_map(raw_provider.get("http_headers")),
            env_http_headers=_string_map(raw_provider.get("env_http_headers")),
            query_params=_string_map(raw_provider.get("query_params")),
        )
        return cls(
            codex_home=codex_home,
            config_path=config_path,
            model=model,
            provider_id=provider_id,
            provider=provider,
            reasoning_effort=(
                str(document["model_reasoning_effort"])
                if document.get("model_reasoning_effort")
                else None
            ),
            verbosity=(
                str(document["model_verbosity"])
                if document.get("model_verbosity")
                else None
            ),
            mcp_server_ids=tuple(str(name) for name in mcp_servers),
        )


def _string_map(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def _read_openai_api_key() -> str | None:
    auth_path = codex_home_dir() / "auth.json"
    if not auth_path.exists():
        return None
    try:
        payload = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = payload.get("OPENAI_API_KEY")
    return str(value) if value else None
