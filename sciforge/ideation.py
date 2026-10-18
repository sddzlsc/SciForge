"""创新点挖掘：从文献空白到 Idea Card / 研究 Proposal。

既可作为 Pipeline 的 ``ideate`` 阶段底层实现，也可被 Tool / CLI 直接调用。
没有 API key 时自动降级为离线启发式，保证流程可跑通。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from sciforge.config import Settings
from sciforge.core.types import ChatMessage
from sciforge.llm.base import LLMError
from sciforge.llm.router import LLMRouter

_IDEA_SYSTEM = """你是一名顶会（NeurIPS/CVPR/ICLR/ACL）资深研究者，擅长从文献中找研究空白并设计有洞察力的方法。
只输出 JSON，不要输出多余文字。每个想法必须回答「为什么之前没人做/做不好」，拒绝无洞察的模块堆叠。"""

_IDEA_PROMPT = """研究主题：{topic}

已知文献与结论：
{context}

请产出 {n} 个候选创新点，输出 JSON 数组，每个元素字段：
{{
  "title": "一句话论文标题",
  "gap": "对应哪条具体研究空白（含证据）",
  "insight": "核心洞察：为什么这样做会有效",
  "approach": ["步骤1", "步骤2", "步骤3"],
  "mvp": "2天内可完成的最小验证实验（数据集/baseline/指标）",
  "risks": ["最可能失败的方式", "如何提前发现"],
  "novelty": 4,
  "feasibility": 4,
  "impact": 3,
  "related": ["可能最接近的已有工作，标注差异化"]
}}

要求：三个想法要有明显不同的切入角度；新颖性/可行性/影响力用 1-5 整数；中文输出。"""


@dataclass
class IdeaCard:
    title: str
    gap: str = ""
    insight: str = ""
    approach: list[str] = field(default_factory=list)
    mvp: str = ""
    risks: list[str] = field(default_factory=list)
    novelty: int = 3
    feasibility: int = 3
    impact: int = 3
    related: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        return round((self.novelty + self.feasibility + self.impact) / 3, 2)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["score"] = self.score
        return data

    def to_markdown(self) -> str:
        lines = [
            f"### {self.title}",
            "",
            f"- **研究空白**：{self.gap}",
            f"- **核心洞察**：{self.insight}",
            "- **方法草案**：",
            *[f"  {i}. {step}" for i, step in enumerate(self.approach, start=1)],
            f"- **MVP**：{self.mvp}",
            f"- **风险**：{'；'.join(self.risks) if self.risks else '待评估'}",
            f"- **评分**：新颖性 {self.novelty}/5 · 可行性 {self.feasibility}/5 · 影响力 {self.impact}/5"
            f" → 综合 {self.score}",
        ]
        if self.related:
            lines.append(f"- **最接近工作**：{'；'.join(self.related)}")
        return "\n".join(lines)


def _extract_json(text: str) -> list[dict]:
    """从 LLM 输出中稳健地提取 JSON 数组。"""
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = text.find("["), text.rfind("]")
        if start != -1 and end != -1 and end > start:
            candidate = text[start : end + 1]
    if candidate is None:
        return []
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    return [item for item in data if isinstance(item, dict)]


def _cards_from_payload(payload: list[dict]) -> list[IdeaCard]:
    cards: list[IdeaCard] = []
    for item in payload:
        cards.append(
            IdeaCard(
                title=str(item.get("title") or "未命名想法").strip(),
                gap=str(item.get("gap") or ""),
                insight=str(item.get("insight") or ""),
                approach=[str(x) for x in item.get("approach") or []],
                mvp=str(item.get("mvp") or ""),
                risks=[str(x) for x in item.get("risks") or []],
                novelty=_clamp(item.get("novelty")),
                feasibility=_clamp(item.get("feasibility")),
                impact=_clamp(item.get("impact")),
                related=[str(x) for x in item.get("related") or []],
            )
        )
    return cards


def _clamp(value) -> int:
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 3


# ---------------------------------------------------------------------------
# 离线启发式（无 API key / mock 模式）
# ---------------------------------------------------------------------------


def _offline_ideas(topic: str) -> list[IdeaCard]:
    return [
        IdeaCard(
            title=f"面向长尾与分布偏移的输入自适应计算：以「{topic}」为例",
            gap="现有方法在分布偏移与长尾类别上性能骤降，且对所有输入使用固定计算量（来自调研中的共同局限）。",
            insight="用轻量路由根据输入不确定性动态选择模块/深度，可在不增加平均计算量的前提下提升最差组性能。",
            approach=[
                "不确定性估计头：输出预测熵与置信度",
                "可微路由：在候选模块间做 soft-top-k 选择，加负载均衡损失",
                "一致性正则：扰动输入下路由决策稳定",
                "推理加速：置信度高时提前退出",
            ],
            mvp="在 2 个分布偏移 benchmark 上对比固定计算量 baseline，报告 worst-group acc 与平均 FLOPs。",
            risks=["路由坍塌（全部选同一分支）", "最差组提升但平均性能下降"],
            novelty=4,
            feasibility=4,
            impact=4,
            related=["Mixture-of-Experts 路由方法（差异化：面向最差组而非平均指标）"],
        ),
        IdeaCard(
            title="训练-部署一致的硬件友好稀疏化",
            gap="大量稀疏训练宣称加速，但部署时稀疏模式不被硬件利用，实际吞吐提升有限。",
            insight="在训练中显式优化硬件友好的结构化稀疏模式，并用教师蒸馏补偿精度损失。",
            approach=[
                "定义块稀疏约束（如 4×4/2:4）",
                "训练时直通估计 + 稀疏度课程",
                "蒸馏恢复精度",
                "报告精度-FLOPs-吞吐三角关系",
            ],
            mvp="在 ResNet/ViT 小规模设定下对比非结构化稀疏与结构化稀疏的实际吞吐。",
            risks=["精度回退超预期", "硬件测量环境不可复现"],
            novelty=3,
            feasibility=4,
            impact=3,
            related=["经典剪枝/稀疏训练工作（差异化：以真实部署吞吐为核心指标）"],
        ),
        IdeaCard(
            title="去泄漏的评测协议与鲁棒性报告卡",
            gap="现有 few-shot / OOD 评测存在数据泄漏与提示敏感性，结论不可复现。",
            insight="构造去泄漏 split 并强制多提示、多种子、置信区间报告，能暴露被高估的性能。",
            approach=[
                "泄漏检测工具：n-gram/嵌入相似度扫描 train-test",
                "多提示鲁棒性协议",
                "标准化报告卡模板",
                "复现 3 个代表方法作为证据",
            ],
            mvp="复现 3 个方法的官方结果与去泄漏结果，报告差距。",
            risks=["复现成本高", "审稿人认为贡献偏 benchmark"],
            novelty=3,
            feasibility=5,
            impact=4,
            related=["评测基准修复类工作（差异化：提供可复用检测工具与报告卡）"],
        ),
    ]


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------


def generate_ideas(
    settings: Settings,
    topic: str,
    context: str = "",
    *,
    n: int = 3,
    router: LLMRouter | None = None,
    mock: bool | None = None,
) -> list[IdeaCard]:
    """生成候选创新点；LLM 失败时优雅降级到离线启发式。"""
    use_mock = mock if mock is not None else None
    if use_mock is False and router is None:
        use_mock = None
    router = router or LLMRouter(settings, force_mock=use_mock)
    prompt = _IDEA_PROMPT.format(topic=topic, context=context or "（暂无文献上下文）", n=n)
    try:
        response = router.chat(
            "planner",
            [ChatMessage(role="system", content=_IDEA_SYSTEM), ChatMessage(role="user", content=prompt)],
        )
        cards = _cards_from_payload(_extract_json(response.content))
        if cards:
            return cards[:n]
    except LLMError:
        pass
    except Exception:  # noqa: BLE001 - 任何解析/网络问题都降级
        pass
    return _offline_ideas(topic)[:n]


def format_ideas(topic: str, cards: list[IdeaCard], *, header: bool = True) -> str:
    lines: list[str] = []
    if header:
        lines += [f"# 创新点挖掘：{topic}", ""]
    lines += [card.to_markdown() + "\n" for card in cards]
    if cards:
        best = max(cards, key=lambda c: c.score)
        lines.append(f"## 推荐主攻方向\n\n**{best.title}**（综合评分 {best.score}/5）\n")
        lines.append("下一步：用最小可行实验在 2 天内验证核心假设，再决定是否扩展为完整论文故事线。\n")
    return "\n".join(lines)


def build_proposal(topic: str, card: IdeaCard) -> str:
    return f"""# 研究 Proposal：{card.title}

## 1. 背景与问题
研究主题：{topic}

## 2. 研究空白
{card.gap}

## 3. 核心洞察
{card.insight}

## 4. 方法草案
{chr(10).join(f"{i}. {step}" for i, step in enumerate(card.approach, start=1))}

## 5. 最小可行实验（MVP）
{card.mvp}

## 6. 风险与缓解
{chr(10).join(f"- {risk}" for risk in card.risks) if card.risks else "- 待评估"}

## 7. 与已有工作的差异
{chr(10).join(f"- {rel}" for rel in card.related) if card.related else "- 需在文献调研阶段补充"}

## 8. 预期贡献
1. 方法贡献：{card.insight}
2. 实证贡献：在标准与鲁棒性设定下系统验证。
3. 可复现贡献：开源代码、配置和评测脚本。

## 9. 里程碑
| 周次 | 目标 | 交付物 |
|---|---|---|
| W1 | 完成 MVP 实验 | 初步结果表 |
| W2-W3 | 完整方法与消融 | 主实验 + 消融 |
| W4 | 论文初稿与图表 | LaTeX 草稿 |
"""


__all__ = ["IdeaCard", "generate_ideas", "format_ideas", "build_proposal"]
