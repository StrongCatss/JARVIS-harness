from __future__ import annotations

from pathlib import Path

from codex_cil.paths import tools_dir


DEFAULT_OFFLINE_POLICY = """贾维斯离线资料与国内来源策略

1. 优先使用 Tools/ai-docs/openai-docs/docs 下的离线资料。
2. 外部检索只使用国内可访问来源。
3. 只允许通过 Tools/mcp/china-sources 的白名单访问国内网站。
4. 禁止国际互联网搜索、国际下载站和未经允许的远程 MCP。
5. 国内资料也不足时，明确说明“未能离线核实”，不得猜测版本、参数或 API。
6. 模型 API 本身不受网页检索限制，但不得借模型调用下载外部资料。
"""


def offline_policy_text(path: str | Path | None = None) -> str:
    policy_path = (
        Path(path).expanduser().resolve()
        if path
        else tools_dir() / "policies" / "offline-mode-policy.txt"
    )
    try:
        text = policy_path.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_OFFLINE_POLICY.strip()
    return text or DEFAULT_OFFLINE_POLICY.strip()
