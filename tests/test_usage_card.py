from __future__ import annotations

from PySide6.QtWidgets import QApplication

from codex_cil.models import UsageStats
from codex_cil.ui.usage_card import UsageCard, compact_number


def test_compact_number() -> None:
    assert compact_number(999) == "999"
    assert compact_number(1_500) == "1.5K"
    assert compact_number(54_229_180) == "54.23M"


def test_usage_card_rendering(qtbot) -> None:  # type: ignore[no-untyped-def]
    assert QApplication.instance() is not None
    card = UsageCard()
    qtbot.addWidget(card)
    card.set_usage(
        UsageStats(
            input_tokens=54_037_340,
            cached_input_tokens=53_790_592,
            output_tokens=191_840,
            total_tokens=54_229_180,
            context_tokens=78_420,
            context_window=200_000,
        )
    )
    assert "54.23M" in card.total_label.text()
    assert "99.5%" in card.cache_label.text()
    assert round(card.ring._ratio * 100, 1) == 39.2


def test_usage_cost_estimate() -> None:
    usage = UsageStats(input_tokens=1_000_000, output_tokens=500_000)
    assert usage.estimated_cost(2.0, 4.0) == 4.0
