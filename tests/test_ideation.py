"""创新点生成测试（离线）。"""

from __future__ import annotations

from pathlib import Path

from sciforge.config import get_settings
from sciforge.ideation import IdeaCard, build_proposal, format_ideas, generate_ideas


def test_offline_ideas(sciforge_home: Path) -> None:
    settings = get_settings()
    cards = generate_ideas(settings, "视频生成", mock=True)
    assert len(cards) == 3
    assert all(isinstance(card, IdeaCard) for card in cards)
    assert all(card.title for card in cards)
    assert all(1 <= card.novelty <= 5 for card in cards)


def test_format_and_proposal() -> None:
    card = IdeaCard(title="测试想法", gap="空白", insight="洞察", approach=["a", "b"], mvp="mvp")
    text = format_ideas("主题", [card])
    assert "测试想法" in text and "推荐主攻方向" in text
    proposal = build_proposal("主题", card)
    assert "研究 Proposal" in proposal
    assert "最小可行实验" in proposal
    assert "里程碑" in proposal
