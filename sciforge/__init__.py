"""SciForge — 面向深度学习科研的本地智能体 Harness。

DeepSeek 驱动 · 可 SSH 调度 GPU · 文献/读论文/找创新点/实验/写作/顶会配图，
所有能力以 Skill + Tool + Pipeline 模块组合。

用法::

    import sciforge
    sciforge.__version__

    from sciforge.config import get_settings
    settings = get_settings()

CLI 入口::

    sciforge --help
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
