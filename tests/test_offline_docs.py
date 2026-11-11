from __future__ import annotations

from pathlib import Path

from codex_cil.services.offline_docs import OfflineDocsIndex


def test_offline_doc_search_and_prompt_context(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "subagents.md").write_text(
        "# Subagents\n\nCodex subagents can run parallel tasks and return summaries.",
        encoding="utf-8",
    )
    (docs / "tokens.md").write_text(
        "# Token usage\n\nCached input tokens and output tokens are reported separately.",
        encoding="utf-8",
    )
    index = OfflineDocsIndex(docs, tmp_path / "index.sqlite")

    hits = index.search("Codex subagents parallel", limit=2)
    context = index.build_prompt_context("Codex subagents parallel")

    assert hits
    assert hits[0].path == "subagents.md"
    assert "Tools/ai-docs/openai-docs" in context
    assert "subagents.md" in context
