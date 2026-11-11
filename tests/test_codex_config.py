from __future__ import annotations

from pathlib import Path

from codex_cil.services.codex_config import CodexConfig


def test_custom_responses_provider_from_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
model = "configured-model"
model_provider = "custom"
model_reasoning_effort = "high"
model_verbosity = "medium"

[mcp_servers.china-sources]
command = "python"
args = ["server.py"]

[model_providers.custom]
name = "Configured Provider"
base_url = "https://example.test/api"
wire_api = "responses"
requires_openai_auth = false
experimental_bearer_token = "test-secret"
http_headers = { "X-Tenant" = "demo" }
query_params = { "api-version" = "2026-01-01" }
""",
        encoding="utf-8",
    )
    config = CodexConfig.load(config_path)

    assert config.model == "configured-model"
    assert config.provider_id == "custom"
    assert config.provider.request_url() == "https://example.test/api/responses"
    assert config.provider.request_headers()["Authorization"] == "Bearer test-secret"
    assert config.provider.request_headers()["X-Tenant"] == "demo"
    assert config.reasoning_effort == "high"
    assert config.verbosity == "medium"
    assert config.mcp_server_ids == ("china-sources",)
    assert config.is_configured
    assert "test-secret" not in repr(config)


def test_provider_env_key_is_resolved(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
model = "env-model"
model_provider = "proxy"

[model_providers.proxy]
base_url = "https://proxy.example.test/v1"
env_key = "CODEX_CIL_TEST_KEY"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_CIL_TEST_KEY", "env-secret")
    config = CodexConfig.load(config_path)

    assert config.provider.wire_api == "responses"
    assert config.provider.resolved_bearer_token() == "env-secret"
    assert config.auth_description == "环境变量 CODEX_CIL_TEST_KEY"
