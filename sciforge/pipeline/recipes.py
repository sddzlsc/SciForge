"""内置科研 Recipe：把 Stage 排列组合成不同工作流。

新增 recipe 只需在这里加一个 ``Recipe``；每个 Stage 的 prompt 对应
``sciforge/prompts/stages/<prompt>_zh.md``。
"""

from __future__ import annotations

from typing import Any

from sciforge.pipeline.types import Recipe, Stage

# 常用工具集（工具不存在时 AgentLoop 会自动忽略）
LIT_TOOLS = ["lit_search", "lit_daily", "paper_cite", "paper_fetch", "read_file", "write_file"]
READ_TOOLS = [
    "paper_read",
    "paper_note",
    "paper_compare",
    "paper_ask",
    "read_file",
    "write_file",
    "list_dir",
]
IDEA_TOOLS = ["lit_search", "paper_cite", "read_file", "write_file", "skill_load"]
DESIGN_TOOLS = ["read_file", "write_file", "paper_cite", "skill_load"]
CODE_TOOLS = [
    "exp_scaffold",
    "write_file",
    "edit_file",
    "read_file",
    "list_dir",
    "shell",
    "skill_load",
]
RUN_TOOLS = [
    "gpu_status",
    "remote_run",
    "remote_sync",
    "exp_launch",
    "exp_status",
    "exp_logs",
    "read_file",
    "write_file",
]
ANALYZE_TOOLS = [
    "exp_results",
    "read_file",
    "write_file",
    "shell",
    "figure_plot",
    "figure_table",
    "skill_load",
]
WRITE_TOOLS = [
    "paper_init",
    "paper_section",
    "paper_cite",
    "write_file",
    "read_file",
    "skill_load",
]
FIGURE_TOOLS = ["figure_architecture", "figure_plot", "figure_table", "write_file", "read_file"]
REVIEW_TOOLS = ["paper_review", "read_file", "write_file", "skill_load"]
SCOPE_TOOLS = ["read_file", "write_file", "project_tree", "skill_load"]
GPU_TOOLS = ["gpu_status", "remote_run", "read_file", "write_file"]
DATA_TOOLS = ["remote_sync", "remote_run", "read_file", "write_file", "list_dir", "shell"]


def _stage(
    name: str,
    title: str,
    prompt: str,
    tools: list[str],
    *,
    role: str = "executor",
    max_steps: int = 12,
    artifacts: list[str] | None = None,
) -> Stage:
    return Stage(
        name=name,
        title=title,
        prompt=prompt,
        tools=tools,
        role=role,
        max_steps=max_steps,
        artifacts=artifacts or [],
    )


FULL_RECIPE = Recipe(
    name="full",
    title="全流程科研流水线",
    description="课题定义 → 文献调研 → 精读 → 创新点 → GPU 连接 → 数据集上传 → 实验设计/代码/运行/分析 → 配图 → 论文 → 自检",
    tags=("research", "end-to-end"),
    stages=[
        _stage(
            "scope",
            "① 课题定义与边界",
            "scope",
            SCOPE_TOOLS,
            role="planner",
            max_steps=8,
            artifacts=["research-brief.md"],
        ),
        _stage(
            "survey",
            "② 自动文献调研",
            "survey",
            LIT_TOOLS,
            role="planner",
            max_steps=18,
            artifacts=["survey.md"],
        ),
        _stage(
            "read",
            "③ 代表论文精读",
            "read",
            READ_TOOLS,
            role="planner",
            max_steps=16,
            artifacts=["reading-notes.md"],
        ),
        _stage(
            "ideate",
            "④ 创新点挖掘",
            "ideate",
            IDEA_TOOLS,
            role="planner",
            max_steps=14,
            artifacts=["ideas.md"],
        ),
        _stage("gpu", "⑤ GPU 连接与资源确认", "gpu", GPU_TOOLS, max_steps=8, artifacts=["gpu-check.md"]),
        _stage(
            "data",
            "⑥ 数据集准备与上传",
            "data",
            DATA_TOOLS,
            max_steps=10,
            artifacts=["data-manifest.md"],
        ),
        _stage("design", "⑦ 实验设计", "design", DESIGN_TOOLS, max_steps=12, artifacts=["experiment-plan.md"]),
        _stage("code", "⑧ 代码实现", "code", CODE_TOOLS, max_steps=18, artifacts=["code-plan.md"]),
        _stage("run", "⑨ 远程训练/实验", "run", RUN_TOOLS, max_steps=14, artifacts=["run-log.md"]),
        _stage("analyze", "⑩ 结果分析", "analyze", ANALYZE_TOOLS, max_steps=14, artifacts=["analysis.md"]),
        _stage("figures", "⑪ 顶会级配图", "figures", FIGURE_TOOLS, max_steps=14, artifacts=["figures.md"]),
        _stage(
            "write",
            "⑫ 论文写作",
            "write",
            WRITE_TOOLS,
            role="writer",
            max_steps=18,
            artifacts=["paper-draft.md"],
        ),
        _stage(
            "review",
            "⑬ 审稿式自检",
            "review",
            REVIEW_TOOLS,
            role="reviewer",
            max_steps=10,
            artifacts=["review.md"],
        ),
    ],
)

SURVEY_RECIPE = Recipe(
    name="survey",
    title="文献调研与创新点",
    description="快速调研一个方向：检索 → 精读 2-3 篇 → 输出研究空白与候选创新点",
    tags=("literature", "quick"),
    stages=[
        _stage("survey", "① 文献调研", "survey", LIT_TOOLS, role="planner", max_steps=18, artifacts=["survey.md"]),
        _stage("read", "② 代表论文精读", "read", READ_TOOLS, role="planner", max_steps=16, artifacts=["reading-notes.md"]),
        _stage("ideate", "③ 创新点挖掘", "ideate", IDEA_TOOLS, role="planner", max_steps=14, artifacts=["ideas.md"]),
    ],
)

EXPERIMENT_RECIPE = Recipe(
    name="experiment",
    title="从想法到实验",
    description="实验设计 → 代码骨架 → GPU 运行 → 结果分析",
    tags=("experiment",),
    stages=[
        _stage("design", "① 实验设计", "design", DESIGN_TOOLS, max_steps=12, artifacts=["experiment-plan.md"]),
        _stage("code", "② 代码实现", "code", CODE_TOOLS, max_steps=18, artifacts=["code-plan.md"]),
        _stage("run", "③ 远程训练/实验", "run", RUN_TOOLS, max_steps=14, artifacts=["run-log.md"]),
        _stage("analyze", "④ 结果分析", "analyze", ANALYZE_TOOLS, max_steps=14, artifacts=["analysis.md"]),
    ],
)

PAPER_RECIPE = Recipe(
    name="paper",
    title="从结果到论文",
    description="结果分析 → 论文草稿 → 顶会级配图 → 审稿式自检",
    tags=("writing",),
    stages=[
        _stage("analyze", "① 结果分析", "analyze", ANALYZE_TOOLS, max_steps=14, artifacts=["analysis.md"]),
        _stage(
            "write",
            "② 论文写作",
            "write",
            WRITE_TOOLS,
            role="writer",
            max_steps=18,
            artifacts=["paper-draft.md"],
        ),
        _stage("figures", "③ 顶会级配图", "figures", FIGURE_TOOLS, max_steps=14, artifacts=["figures.md"]),
        _stage("review", "④ 审稿式自检", "review", REVIEW_TOOLS, role="reviewer", max_steps=10, artifacts=["review.md"]),
    ],
)

DAILY_RECIPE = Recipe(
    name="daily",
    title="每日 arXiv 速览",
    description="抓取 daily-arXiv-ai-enhanced 当日 AI 增强摘要，按感兴趣方向筛选并给出精读建议",
    tags=("literature", "daily"),
    stages=[
        _stage("daily", "每日 arXiv 速览", "daily", LIT_TOOLS + ["skill_load"], max_steps=12, artifacts=["daily.md"]),
    ],
)

FIGURE_RECIPE = Recipe(
    name="figure",
    title="论文配图专项",
    description="根据方法与结果生成模型架构图、实验曲线与三线表",
    tags=("figure",),
    stages=[
        _stage("figures", "顶会级配图", "figures", FIGURE_TOOLS, max_steps=14, artifacts=["figures.md"]),
    ],
)

LITREVIEW_RECIPE = Recipe(
    name="litreview",
    title="多论文精读对比",
    description="检索并精读多篇论文，输出对比矩阵与研究空白",
    tags=("literature", "reading"),
    stages=[
        _stage("survey", "① 文献调研", "survey", LIT_TOOLS, role="planner", max_steps=18, artifacts=["survey.md"]),
        _stage("read", "② 多论文精读对比", "read", READ_TOOLS, role="planner", max_steps=16, artifacts=["reading-notes.md"]),
    ],
)

RECIPES: dict[str, Recipe] = {
    recipe.name: recipe
    for recipe in (
        FULL_RECIPE,
        SURVEY_RECIPE,
        EXPERIMENT_RECIPE,
        PAPER_RECIPE,
        DAILY_RECIPE,
        FIGURE_RECIPE,
        LITREVIEW_RECIPE,
    )
}


def get_recipe(name: str) -> Recipe | None:
    return RECIPES.get(name)


def stage_recipe(stage_name: str) -> Recipe | None:
    """把 full 流程中的单个阶段包成 recipe，用于对话顶部按 pipeline 顺序单步运行。"""
    for stage in FULL_RECIPE.stages:
        if stage.name == stage_name:
            return Recipe(
                name=f"stage-{stage.name}",
                title=f"单阶段 · {stage.title}",
                description=f"只运行全流程中的「{stage.title}」阶段",
                stages=[stage],
                tags=("stage", stage.name),
            )
    return None


def list_recipes() -> list[dict[str, Any]]:
    return [
        {
            "name": recipe.name,
            "title": recipe.title,
            "description": recipe.description,
            "stages": ", ".join(stage.name for stage in recipe.stages),
            "stage_details": [{"name": stage.name, "title": stage.title} for stage in recipe.stages],
            "tags": ", ".join(recipe.tags),
        }
        for recipe in RECIPES.values()
    ]


__all__ = [
    "RECIPES",
    "get_recipe",
    "stage_recipe",
    "list_recipes",
    "FULL_RECIPE",
    "SURVEY_RECIPE",
    "EXPERIMENT_RECIPE",
    "PAPER_RECIPE",
    "DAILY_RECIPE",
    "FIGURE_RECIPE",
    "LITREVIEW_RECIPE",
]
