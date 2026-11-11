from __future__ import annotations

from pathlib import Path

from codex_cil.services.offline_policy import (
    DEFAULT_OFFLINE_POLICY,
    offline_policy_text,
)


def test_offline_policy_is_loaded_from_tools(tmp_path: Path) -> None:
    policy = tmp_path / "offline-mode-policy.txt"
    policy.write_text("只允许国内来源。", encoding="utf-8")

    assert offline_policy_text(policy) == "只允许国内来源。"


def test_offline_policy_has_safe_fallback(tmp_path: Path) -> None:
    missing = tmp_path / "missing.txt"

    assert offline_policy_text(missing) == DEFAULT_OFFLINE_POLICY.strip()
