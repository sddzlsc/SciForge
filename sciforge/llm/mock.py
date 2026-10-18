"""离线 Mock LLM：没有 API key 也能跑通 pipeline / demo / 测试。

两种模式：
1. ``scripts=[...]``：按顺序返回脚本化响应（字符串或 LLMResponse），用于精确测试；
2. 默认启发式：根据 prompt 关键词生成结构化的“像样”科研文本，使
   ``sciforge pipeline run --recipe full --mock`` 能产出可读的完整工件。
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable

from sciforge.core.types import ChatMessage, LLMResponse, Usage
from sciforge.llm.base import LLMProvider

Responder = Callable[[list[ChatMessage], dict], LLMResponse]

_TOPIC_PATTERNS = [
    re.compile(r"研究主题[：:]\s*(.+)"),
    re.compile(r"主题[：:]\s*(.+)"),
    re.compile(r"[Tt]opic[：:]\s*(.+)"),
    re.compile(r"课题[：:]\s*(.+)"),
]


def extract_topic(messages: list[ChatMessage]) -> str:
    text = "\n".join(m.content for m in messages if m.role == "user")
    for pattern in _TOPIC_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1).strip().splitlines()[0][:120]
    # 退回到首句
    first = text.strip().splitlines()[0] if text.strip() else "深度学习科研主题"
    return first[:120]


def _seed_of(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)


class MockProvider(LLMProvider):
    name = "mock"

    def __init__(
        self,
        *,
        name: str = "mock",
        model: str = "mock-researcher",
        scripts: list[LLMResponse | str] | None = None,
        responder: Responder | None = None,
        latency: float = 0.0,
    ) -> None:
        self.name = name
        self.model = model
        self._scripts = list(scripts or [])
        self._responder = responder
        self.latency = latency
        self.calls = 0

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        self.calls += 1
        if self.latency:
            time.sleep(self.latency)

        if self._scripts:
            item = self._scripts.pop(0)
            if isinstance(item, str):
                item = LLMResponse(content=item)
            if stream and on_delta and item.content:
                for chunk in _chunks(item.content):
                    on_delta(chunk)
            return item

        if self._responder is not None:
            response = self._responder(messages, {"tools": tools, "model": model})
        else:
            response = self._heuristic(messages, tools)

        if stream and on_delta and response.content:
            for chunk in _chunks(response.content):
                on_delta(chunk)
        return response

    # ------------------------------------------------------------------
    def _heuristic(self, messages: list[ChatMessage], tools: list[dict] | None) -> LLMResponse:
        prompt = "\n".join(m.content for m in messages if m.role in ("user", "system"))
        topic = extract_topic(messages)
        seed = _seed_of(prompt + topic)
        body = _stage_template(prompt, topic, seed)
        usage = Usage(
            prompt_tokens=max(1, len(prompt) // 4),
            completion_tokens=max(1, len(body) // 4),
            total_tokens=max(2, (len(prompt) + len(body)) // 4),
            calls=1,
        )
        return LLMResponse(
            content=body,
            tool_calls=[],
            usage=usage,
            finish_reason="stop",
            model=self.model,
        )


def _chunks(text: str, size: int = 24) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


# ---------------------------------------------------------------------------
# 启发式模板：让离线 pipeline 输出看起来像真实科研中间产物
# ---------------------------------------------------------------------------


def _stage_template(prompt: str, topic: str, seed: int) -> str:
    low = prompt.lower()
    if "文献调研" in prompt or "survey" in low or "literature review" in low:
        return _survey(topic)
    if "精读" in prompt or "deep read" in low or "阅读笔记" in prompt:
        return _reading(topic)
    if "创新点" in prompt or "idea" in low or "研究空白" in prompt:
        return _ideas(topic)
    if "实验设计" in prompt or "experiment design" in low or "实验方案" in prompt:
        return _experiment_design(topic)
    if "代码" in prompt and ("实现" in prompt or "scaffold" in low):
        return _code_plan(topic)
    if "结果分析" in prompt or "analysis" in low or "实验结果" in prompt:
        return _analysis(topic)
    if "论文" in prompt and ("写作" in prompt or "draft" in low):
        return _writing(topic)
    if "图" in prompt or "figure" in low or "diagram" in low:
        return _figure(topic)
    if "审稿" in prompt or "review" in low:
        return _review(topic)
    return (
        f"## 关于「{topic}」的离线草稿\n\n"
        "这是 Mock LLM 生成的占位内容，用于在没有 API key 时验证 SciForge 的流程编排。\n\n"
        "- 配置 DEEPSEEK_API_KEY 后可切换到真实 DeepSeek 推理；\n"
        "- 或在 ~/.sciforge/config.toml 中新增任意 OpenAI 兼容 provider。\n"
    )


def _survey(topic: str) -> str:
    return f"""## 文献调研：{topic}

### 1. 检索策略
- 数据源：arXiv (cs.CV / cs.CL / cs.LG) + Semantic Scholar + daily-arXiv-ai-enhanced 每日增强摘要
- 检索式：`("{topic}" OR 相关同义词) AND (method OR benchmark OR survey)`
- 时间窗：近 24 个月优先，经典基线不限

### 2. 研究脉络（时间线）
| 阶段 | 代表工作 | 核心思想 | 局限 |
|---|---|---|---|
| 奠基 | 经典 baseline | 建立评测协议与基本范式 | 规模小、泛化弱 |
| 发展 | 中期代表工作 | 引入更强的归纳偏置/架构 | 计算成本高 |
| 当前 | 最新 SOTA | 缩放律 + 数据工程 + 多任务统一 | 可解释性、数据效率仍是空白 |

### 3. 方法谱系
1. **架构路线**：模块替换、注意力/卷积混合、层级化设计。
2. **训练路线**：预训练目标、课程学习、RL 对齐、蒸馏压缩。
3. **数据路线**：合成数据、数据筛选与去重、多模态对齐。

### 4. 评测与基准
- 主流 benchmark：需要补充具体数据集与指标（Accuracy / F1 / mAP / perplexity / win-rate）。
- 常见问题：数据泄漏、评测协议不一致、超参未对齐。

### 5. 初步空白（供下一步精读确认）
- [ ] 在低资源/长尾场景下的鲁棒性证据不足；
- [ ] 方法组件的收益归因（ablation）不充分；
- [ ] 训练与推理成本未被系统报告。

> 说明：以上为离线 Mock 生成的结构化模板；接入真实 LLM 后会由 DeepSeek 基于实际检索结果填充。
"""


def _reading(topic: str) -> str:
    return f"""## 精读笔记：{topic} 代表论文

### 元信息
- 题目 / 作者 / venue：待真实检索填充
- 代码 / 数据：待核实链接

### 一句话总结
论文针对「{topic}」中的关键瓶颈，提出了一个可插拔的模块化方法，并在多个基准上取得一致提升。

### 问题设定
- 输入 / 输出：
- 关键假设：
- 与已有工作的差异：

### 方法拆解
1. **整体框架**：输入编码 → 核心模块 → 解码/预测 → 辅助损失。
2. **关键模块**：给出公式直觉（不抄公式，写清“为什么这样设计”）。
3. **训练目标**：主损失 + 正则/对比/蒸馏项的作用。

### 实验证据
- 主实验：相对最强 baseline 的提升幅度与显著性。
- 消融：每个组件的边际贡献。
- 泛化：跨数据集 / 跨规模表现。

### 批判性评估
- 优点：
- 弱点 / 可疑点：
- 可复现风险：

### 可迁移点
- 可直接借用的设计：
- 可改进的方向：
"""


def _ideas(topic: str) -> str:
    return f"""## 创新点挖掘：{topic}

### 候选方向（按 新颖性 × 可行性 排序）

#### Idea A：面向长尾场景的自适应模块路由
- **空白**：现有方法在分布偏移下性能骤降，缺少输入自适应的计算分配。
- **做法**：轻量路由器根据输入不确定性选择模块组合；用一致性正则约束路由稳定性。
- **验证**：在 2 个分布偏移 benchmark 上与 baseline 对比，附路由可视化。
- **风险**：路由坍塌（需负载均衡损失）。
- **新颖性/可行性/影响力**：4/5 · 4/5 · 4/5

#### Idea B：训练-推理一致的稀疏化
- **空白**：稀疏训练与部署稀疏模式不一致，导致实际加速有限。
- **做法**：在训练中显式建模硬件友好的结构化稀疏约束，并用蒸馏恢复精度。
- **验证**：报告 FLOPs、显存、吞吐与精度四象限。
- **风险**：精度回退，需要蒸馏与 warmup 调参。

#### Idea C：小样本评测协议修复
- **空白**：现有 few-shot 评测存在数据泄漏与提示敏感性问题。
- **做法**：构造去泄漏 split + 多提示鲁棒性报告，提出标准化评测卡。
- **验证**：复现 3 个代表方法，报告方差与置信区间。

### 推荐下一步
1. 对 Idea A 做 1 页 proposal；
2. 用 2 天做最小可行实验（MVP）验证路由假设；
3. 若成立，扩展为完整论文故事线（问题→洞察→方法→证据）。
"""


def _experiment_design(topic: str) -> str:
    return f"""## 实验设计：{topic}

### 假设
H1：所提模块在分布偏移 benchmark 上带来 ≥2% 的稳定提升。
H2：收益来自自适应路由而非参数量的增加（需匹配参数量对照）。

### 数据集与指标
| 数据集 | 用途 | 指标 |
|---|---|---|
| 主 benchmark | 主实验 | Top-1 / F1 |
| 偏移 split | 鲁棒性 | worst-group acc |
| 小规模消融集 | 消融 | 同上 |

### 基线
- 复现的最强 baseline（固定随机种子 × 3）
- 参数量匹配对照
- 计算量匹配对照

### 消融计划
1. 去掉路由 → 固定平均；
2. 去掉一致性正则；
3. 替换路由输入特征。

### 资源预估
- 单卡 A100 80G：主实验约 12 GPU-hours；
- 消融约 8 GPU-hours；总计 < 1 天。

### 交付物
- `experiments/` 可复现配置（YAML）+ 训练脚本；
- `results/metrics.json` + 论文三线表；
- 失败分析附录。
"""


def _code_plan(topic: str) -> str:
    return f"""## 代码实现计划：{topic}

### 目录结构
```
experiments/
  configs/base.yaml
  src/data.py
  src/model.py
  src/train.py
  src/evaluate.py
  scripts/run.sh
```

### 关键实现点
1. 数据管线固定 seed 与 deterministic dataloader；
2. 模型模块化：`Backbone` + `Router` + `Head`，便于消融；
3. 训练脚本支持 `--config`、`--seed`、`--resume`；
4. 指标写入 `results/metrics.json`，同时打印三线表 latex。

### 验收
- `python -m src.train --config configs/base.yaml --smoke` 在 CPU 上 1 分钟跑通；
- GPU 上 `nohup`/`tmux` 启动，日志可 tail。
"""


def _analysis(topic: str) -> str:
    return f"""## 结果分析：{topic}

### 主结果
| 方法 | 主指标↑ | 偏移指标↑ | 参数量 | 吞吐 |
|---|---|---|---|---|
| Baseline | 基准 | 基准 | 1.0× | 1.0× |
| Ours | +2.3 | +4.1 | 1.02× | 0.97× |

### 结论
- 主假设 H1 成立（提升超出 3 个随机种子的标准差）；
- H2 部分成立：参数量匹配后仍有收益，但幅度下降约 30%。

### 失败案例分析
- 在小样本类别上收益不稳定；
- 路由在极端 OOD 输入上趋同，需要熵正则。

### 对论文叙事的影响
- 主卖点改为「输入自适应计算」而非「更强 backbone」；
- 增加参数量匹配实验到主文，避免审稿人质疑。
"""


def _writing(topic: str) -> str:
    return f"""## 论文草稿：{topic}

### Abstract（草稿）
现有方法在……场景下存在……问题。我们观察到……，据此提出……。在……上，方法相对最强
基线提升……，同时保持……。消融表明……是关键。代码与配置已开源。

### 1. Introduction
- 背景与重要性；
- 现有方法的三个不足（对应文献）；
- 我们的洞察（一句话）；
- 贡献列表（方法 / 实验 / 分析）。

### 2. Related Work
- 路线 A：……
- 路线 B：……
- 与本文差异：……

### 3. Method
- 3.1 问题定义
- 3.2 整体框架（配 Figure 1 模型图）
- 3.3 核心模块（公式 + 直觉）
- 3.4 训练目标

### 4. Experiments
- 4.1 设置
- 4.2 主结果（Table 1）
- 4.3 消融（Table 2）
- 4.4 可视化与失败分析

### 5. Conclusion & Limitations
"""


def _figure(topic: str) -> str:
    return f"""## 图表规划：{topic}

### Figure 1（模型总览图）
- 风格：CVPR/NeurIPS 单页顶部通栏，横向 3 段式；
- 元素：数据流 → 核心模块（高亮）→ 输出与损失；
- 配色：Okabe-Ito 色盲友好；箭头细、圆角框、统一 8pt 字体。

### Figure 2（实验结果曲线）
- 折线图，x=训练步数，y=主指标；3 条曲线（baseline/ours/ours w/o router）；
- 带均值±方差阴影，图例左上，无右上边框。

### Table 1
- booktabs 三线表，最佳结果加粗，第二好下划线。

> 用 `sciforge fig arch --spec figures/fig1.yaml` 生成矢量图。
"""


def _review(topic: str) -> str:
    return f"""## 审稿式自检：{topic}

### 潜在问题（按严重程度）
1. **[Major] 缺少参数量/计算量匹配对照** → 补充 matching baseline。
2. **[Major] 主结果仅报告单次运行** → 报告 3 seeds 均值±std。
3. **[Minor] 相关工作遗漏近期工作** → 补充 2 篇 2024-2025 工作并对比。
4. **[Minor] 图 1 文字过小** → 统一到 8pt，检查灰度可读性。

### 修复优先级
先补实验（1-2 天），再改写作（半天），最后统一图表风格。
"""


__all__ = ["MockProvider", "extract_topic"]
