"""生成 SciForge 离线 demo fixtures 中的示例论文（PDF + 纯文本）。

只生成 ``examples/demo/fixtures/paper.pdf`` 与 ``paper.txt``；其余 fixtures
（arxiv_search.json / daily_arxiv.jsonl / arch_spec.yaml / plot_spec.yaml /
results/metrics.json）是手写并随仓库提交的。

用法::

    python examples/demo/make_fixtures.py          # 生成
    python examples/demo/make_fixtures.py --check  # 只校验现有文件

依赖：pymupdf（dev extras 已含；``uv pip install -e ".[dev]"``）。
PDF 为 4–5 页、含 Abstract / Introduction / Related Work / Method /
Experiments / Conclusion / References 的标准论文结构，供 paper_read /
paper_ask / RAG 精读演示使用。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
PAPER_PDF = FIXTURES_DIR / "paper.pdf"
PAPER_TXT = FIXTURES_DIR / "paper.txt"

TITLE = "Adaptive Token Routing for Efficient Video Diffusion Transformers"
AUTHORS = "Lin Chen, Yuki Tanaka, Minghao Zhou, Elena Petrova"
AFFILIATION = "SciForge Demo Lab - https://github.com/sciforge/sciforge"
ARXIV_LIKE_ID = "arXiv:2601.00042 [cs.CV]"

ABSTRACT = (
    "Diffusion Transformers (DiTs) have become the dominant backbone for high-fidelity image "
    "and video generation, but their quadratic attention cost over long spatio-temporal token "
    "sequences makes training and inference expensive. We observe that the computational "
    "importance of video tokens is highly non-uniform: a small fraction of patches carries most "
    "of the motion and appearance information, while background tokens can be processed at much "
    "lower cost. Building on this observation, we introduce ATR-VDiT, a diffusion transformer "
    "augmented with a lightweight adaptive token router that predicts a per-sample, per-layer "
    "token importance score and routes only the top-k tokens through the expensive attention "
    "blocks. Unselected tokens are merged into their nearest selected neighbours and scattered "
    "back before decoding. The router is trained end-to-end with a straight-through estimator "
    "and a consistency regularizer that stabilizes the selection across diffusion timesteps. On "
    "class-conditional UCF-101 video generation, ATR-VDiT improves FVD from 252.1 to 236.4 while "
    "reducing training GPU-hours by 44.7% and increasing inference throughput by 1.42x. "
    "Ablations show that both adaptive routing and token merging are necessary for the gain, "
    "and that a single router generalizes across resolutions and clip lengths without "
    "retraining."
)

SECTIONS: list[tuple[str, Any]] = [
    ("h1", "1  Introduction"),
    ("p", "Video generation has progressed rapidly from early GAN-based models to diffusion "
          "models that synthesize temporally coherent clips at high resolution. A key ingredient "
          "of this progress is the replacement of the convolutional U-Net backbone with a "
          "Diffusion Transformer (DiT) that operates on sequences of latent patches. DiTs scale "
          "predictably with compute and data, and they unify image and video generation under a "
          "single architecture. However, scaling comes at a cost: attention is quadratic in the "
          "number of tokens, and a 16-frame 256x256 video already produces thousands of latent "
          "tokens per sample. Training a state-of-the-art video DiT therefore requires thousands "
          "of GPU-hours, which limits research reproducibility and deployment."),
    ("p", "Existing efficiency methods follow three main directions. The first reduces the "
          "number of diffusion steps through better solvers or distillation. The second "
          "compresses the latent space with stronger autoencoders. The third sparsifies or "
          "factorizes attention, for example by restricting attention to local windows or by "
          "separating spatial and temporal attention. While effective, these approaches apply a "
          "fixed computation pattern to every sample: all tokens receive the same treatment "
          "regardless of content, and the resulting speedup is often paid for with a visible "
          "drop in motion fidelity."),
    ("p", "In this paper we argue that the computation should follow the content. In natural "
          "videos, a large fraction of tokens belongs to static background regions whose "
          "representations change little across frames, whereas a small number of tokens covers "
          "moving objects, hands, text, and other fine-grained details. We therefore propose to "
          "learn a token router that, conditioned on the noisy latent and the diffusion "
          "timestep, estimates how much each token contributes to the denoising objective, and "
          "to process only the most informative tokens in the expensive global attention layers."),
    ("p", "Our contributions are threefold. (i) We introduce a lightweight adaptive token "
          "router that is trained jointly with the diffusion objective and requires less than "
          "1% additional parameters. (ii) We design a token merge-and-scatter mechanism that "
          "keeps the residual stream dense, so that unselected tokens still receive gradient "
          "signal and can be routed back in later layers. (iii) We show on UCF-101 that the "
          "method improves FVD by 6.2% relative to a strong video DiT baseline while cutting "
          "training cost by nearly half, and we provide detailed ablations of the router, the "
          "merge operation, and the consistency regularizer."),
    ("h1", "2  Related Work"),
    ("h2", "2.1  Diffusion models for video generation"),
    ("p", "Denoising diffusion probabilistic models (DDPM) learn a data distribution by "
          "reversing a gradual noising process, and they have been extended from images to "
          "video by factorizing the denoising network into spatial and temporal attention. "
          "Latent diffusion models move the process into a compressed autoencoder space, "
          "substantially reducing the cost of high-resolution synthesis. More recent video "
          "models replace the U-Net with a transformer over spatio-temporal patches, following "
          "the success of DiT for images. Our work keeps this transformer backbone unchanged and "
          "instead reduces the number of tokens that participate in global attention."),
    ("h2", "2.2  Efficient transformers and token sparsification"),
    ("p", "Token pruning and merging have been studied extensively for discriminative vision "
          "transformers, where redundant patches are removed in deeper layers. In generative "
          "models the situation is different: the token set must remain dense at the output, and "
          "the selection must be stable across denoising steps to avoid flicker. Prior work on "
          "efficient diffusion transformers uses fixed sparsity patterns or distills a smaller "
          "student. In contrast, our router is content-adaptive, differentiable, and trained "
          "with the same denoising loss as the backbone, and our merge-and-scatter design keeps "
          "the computation graph dense."),
    ("h2", "2.3  Parameter-efficient and conditional computation"),
    ("p", "Conditional computation routes inputs through different parts of a network, and "
          "mixture-of-experts layers are a prominent example. Low-rank adaptation shows that a "
          "small number of trainable parameters can steer a large frozen model. We borrow the "
          "idea of a small learned controller but apply it to token selection rather than to "
          "weights, and we regularize the controller with a temporal consistency loss that is "
          "specific to diffusion."),
    ("h1", "3  Method"),
    ("h2", "3.1  Preliminaries"),
    ("p", "Let x be a video clip encoded by a 3D variational autoencoder into a latent tensor "
          "z of shape C x T x H x W, which is flattened into N = T·H·W tokens. A diffusion "
          "transformer with L blocks learns to predict the noise epsilon added at timestep t. "
          "Each block applies layer normalization, global multi-head self-attention, a "
          "feed-forward network, and residual connections. The training objective is the "
          "standard epsilon-prediction loss L_diff = E[||epsilon - epsilon_theta(z_t, t)||^2]."),
    ("h2", "3.2  Adaptive token router"),
    ("p", "The router is a two-layer MLP with hidden width 128 that takes as input the "
          "normalized token feature, a sinusoidal timestep embedding, and a learned layer "
          "embedding. It outputs a scalar importance score s_i for every token. During training "
          "we sample a selection mask with a Gumbel-sigmoid relaxation, and during inference we "
          "simply take the top-k scores. The selection ratio k/N is not fixed: the router "
          "predicts a global budget from the mean score and the timestep, and the budget is "
          "clamped to [0.40, 0.90]. Early layers, where features are still coarse, tend to keep "
          "more tokens; deeper layers operate on more abstract features and select fewer tokens."),
    ("h2", "3.3  Token merge and scatter"),
    ("p", "Simply dropping unselected tokens creates a mismatch between the dense residual "
          "stream and the sparse attention output. We therefore merge unselected tokens into "
          "their nearest selected neighbour in feature space, run attention over the selected "
          "tokens plus one aggregated token per neighbourhood, and scatter the updated selected "
          "features back to their original positions. The merge weights are computed with a "
          "softmax over negative feature distances and are differentiable, so gradients flow to "
          "both the router and the backbone."),
    ("h2", "3.4  Consistency regularization"),
    ("p", "Because the router runs at every denoising step, small changes in selection between "
          "consecutive steps can produce visible temporal flicker. We add a consistency loss "
          "L_con = ||m_t - m_{t-1}||_1 that penalizes changes in the soft selection mask between "
          "adjacent timesteps, and we anneal its weight from 0.1 to 0.05 during training. The "
          "total objective is L = L_diff + 0.05 · L_con. The router adds 0.74M parameters to a "
          "458M-parameter backbone, an overhead of 0.16%."),
    ("h2", "3.5  Implementation details"),
    ("p", "We train with AdamW, learning rate 1e-4, cosine schedule, batch size 64, and "
          "100k optimization steps on 8 A100-80GB GPUs. The 3D autoencoder is frozen. We use "
          "classifier-free guidance with a guidance scale of 4.5 at inference and 100 DDIM "
          "sampling steps. The router is initialized so that the initial budget is 0.90 and is "
          "annealed toward the learned budget over the first 10k steps."),
    ("h1", "4  Experiments"),
    ("h2", "4.1  Setup"),
    ("p", "We evaluate class-conditional video generation on UCF-101 at 256x256 resolution "
          "with 16 frames per clip. We report Frechet Video Distance (FVD, lower is better), "
          "Inception Score (IS), VBench total score, inference throughput on a single A100, and "
          "training GPU-hours. All numbers are averaged over three random seeds. Our baseline is "
          "a video DiT with the same backbone size and training budget; we also compare against "
          "a U-Net video diffusion model and Latte-XL."),
    ("table", {
        "caption": "Table 1: Class-conditional video generation on UCF-101. FVD (lower is "
                   "better) / IS (higher is better) / VBench (higher is better) / throughput "
                   "measured with batch size 8 on one A100.",
        "header": ["Method", "FVD", "IS", "VBench", "Throughput"],
        "rows": [
            ["Video Diffusion (U-Net)", "252.1", "66.4", "78.9", "3.41 it/s"],
            ["Latte-XL (VDiT)", "244.0", "68.7", "80.4", "4.02 it/s"],
            ["DiT baseline (ours impl.)", "252.1", "66.4", "79.0", "3.41 it/s"],
            ["ATR-VDiT (ours)", "236.4", "70.9", "82.1", "4.84 it/s"],
        ],
        "widths": [0.38, 0.14, 0.13, 0.15, 0.20],
    }),
    ("p", "As shown in Table 1, ATR-VDiT improves FVD by 15.7 points over the video DiT "
          "baseline, a relative reduction of 6.2%, and improves VBench by 3.1 points. The gain "
          "is not simply a by-product of more parameters: the router adds only 0.74M parameters, "
          "while throughput increases by 1.42x because fewer tokens enter global attention."),
    ("h2", "4.2  Ablation study"),
    ("p", "Table 2 isolates the effect of each component. Removing the router and using a fixed "
          "90% token ratio degrades FVD by 5.3 points, confirming that content-adaptive "
          "selection matters. Removing token merging increases FVD by 12.9 points, which shows "
          "that the dense residual path is essential for preserving background detail. Disabling "
          "the consistency regularizer increases FVD by 2.8 points and noticeably increases "
          "temporal flicker in qualitative samples."),
    ("table", {
        "caption": "Table 2: Ablation study on UCF-101. Delta FVD is relative to the full model.",
        "header": ["Variant", "FVD", "Delta FVD", "Throughput"],
        "rows": [
            ["Full model", "236.4", "-", "4.84 it/s"],
            ["w/o adaptive router (fixed k=90%)", "241.7", "+5.3", "3.55 it/s"],
            ["w/o consistency regularization", "239.2", "+2.8", "4.79 it/s"],
            ["w/o token merge", "249.3", "+12.9", "4.61 it/s"],
            ["fixed k=40%", "253.8", "+17.4", "5.12 it/s"],
            ["router input = first frame only", "243.6", "+7.2", "4.80 it/s"],
        ],
        "widths": [0.46, 0.16, 0.16, 0.22],
    }),
    ("h2", "4.3  Analysis of routing behaviour"),
    ("p", "The learned budget decreases with depth: the average selection ratio drops from 0.90 "
          "in the first block to 0.40 in the last block, matching the intuition that deeper "
          "features are more redundant. Per-sample budgets are correlated with motion magnitude: "
          "clips with large camera motion keep 12% more tokens than static clips. Finally, the "
          "router trained at 256x256 transfers to 512x512 inference without retraining, "
          "suggesting that it learns a resolution-independent notion of token importance."),
    ("h1", "5  Conclusion"),
    ("p", "We presented ATR-VDiT, an adaptive token routing method for efficient video diffusion "
          "transformers. A lightweight router predicts token importance, top-k selection reduces "
          "global attention cost, and differentiable merge-and-scatter keeps the residual stream "
          "dense. On UCF-101 the method improves FVD by 6.2% relative to a strong baseline while "
          "reducing training cost by 44.7%. In future work we plan to combine adaptive routing "
          "with distillation, to study the interaction between routing and classifier-free "
          "guidance, and to evaluate the method on longer clips and higher resolutions."),
    ("h1", "References"),
]

REFERENCES = [
    "[1] J. Ho, A. Jain, and P. Abbeel. Denoising Diffusion Probabilistic Models. NeurIPS 2020.",
    "[2] R. Rombach, A. Blattmann, D. Lorenz, P. Esser, and B. Ommer. High-Resolution Image "
    "Synthesis with Latent Diffusion Models. CVPR 2022.",
    "[3] W. Peebles and S. Xie. Scalable Diffusion Models with Transformers. ICCV 2023.",
    "[4] J. Ho, T. Salimans, A. Gritsenko, W. Chan, M. Norouzi, and D. J. Fleet. Video Diffusion "
    "Models. NeurIPS 2022.",
    "[5] A. Vaswani, N. Shazeer, N. Parmar, J. Uszkoreit, L. Jones, A. N. Gomez, L. Kaiser, and "
    "I. Polosukhin. Attention Is All You Need. NeurIPS 2017.",
    "[6] A. Dosovitskiy et al. An Image is Worth 16x16 Words: Transformers for Image Recognition "
    "at Scale. ICLR 2021.",
    "[7] P. Esser et al. Scaling Rectified Flow Transformers for High-Resolution Image Synthesis. "
    "ICML 2024.",
    "[8] X. Ma et al. Latte: Latent Diffusion Transformer for Video Generation. arXiv:2401.03048, "
    "2024.",
    "[9] E. J. Hu et al. LoRA: Low-Rank Adaptation of Large Language Models. ICLR 2022.",
    "[10] Y. Liu et al. Sora: A Review on Background, Technology, Limitations, and Opportunities "
    "of Large Vision Models. arXiv:2402.17177, 2024.",
]


# ---------------------------------------------------------------------------
# 纯文本版本
# ---------------------------------------------------------------------------


def build_text() -> str:
    lines: list[str] = [TITLE, "", AUTHORS, AFFILIATION, ARXIV_LIKE_ID, "", "Abstract", "", ABSTRACT, ""]
    for kind, payload in SECTIONS:
        if kind == "table":
            lines.append(payload["caption"])
            lines.append(" | ".join(payload["header"]))
            lines.append("-" * 72)
            for row in payload["rows"]:
                lines.append(" | ".join(row))
            lines.append("")
        elif kind == "h1":
            lines.extend(["", payload, ""])
        elif kind == "h2":
            lines.extend(["", payload, ""])
        else:
            lines.append(payload)
    lines.append("")
    lines.extend(REFERENCES)
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# PDF 渲染
# ---------------------------------------------------------------------------


def _wrap(pymupdf: Any, text: str, fontname: str, fontsize: float, width: float) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = (current + " " + word).strip()
        if pymupdf.get_text_length(trial, fontname=fontname, fontsize=fontsize) <= width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


class _PdfWriter:
    """极简学术排版：A4、衬线正文 10pt、标题加粗、表格三线、页脚页码。"""

    page_width = 595.0
    page_height = 842.0
    margin_x = 64.0
    top = 72.0
    bottom = 770.0

    def __init__(self, pymupdf: Any) -> None:
        self.pymupdf = pymupdf
        self.doc = pymupdf.open()
        self.page: Any = None
        self.page_no = 0
        self.y = self.top

    @property
    def content_width(self) -> float:
        return self.page_width - 2 * self.margin_x

    def new_page(self) -> None:
        self._finish_page()
        self.page = self.doc.new_page(width=self.page_width, height=self.page_height)
        self.page_no += 1
        self.y = self.top

    def _finish_page(self) -> None:
        if self.page is None:
            return
        label = str(self.page_no)
        width = self.pymupdf.get_text_length(label, fontname="helv", fontsize=8)
        self.page.insert_text(
            (self.page_width / 2 - width / 2, 812),
            label,
            fontsize=8,
            fontname="helv",
            color=(0.45, 0.45, 0.45),
        )

    def ensure_space(self, needed: float) -> None:
        if self.page is None:
            self.new_page()
        if self.y + needed > self.bottom:
            self.new_page()

    def paragraph(
        self,
        text: str,
        *,
        fontname: str = "tiro",
        fontsize: float = 10.0,
        leading: float = 14.0,
        color: tuple[float, float, float] = (0.06, 0.06, 0.06),
    ) -> None:
        for line in _wrap(self.pymupdf, text, fontname, fontsize, self.content_width):
            self.ensure_space(leading)
            self.page.insert_text(
                (self.margin_x, self.y), line, fontname=fontname, fontsize=fontsize, color=color
            )
            self.y += leading
        self.y += 4

    def centered(self, text: str, *, fontname: str, fontsize: float, leading: float) -> None:
        for line in _wrap(self.pymupdf, text, fontname, fontsize, self.content_width):
            self.ensure_space(leading)
            width = self.pymupdf.get_text_length(line, fontname=fontname, fontsize=fontsize)
            self.page.insert_text(
                ((self.page_width - width) / 2, self.y), line, fontname=fontname, fontsize=fontsize
            )
            self.y += leading

    def heading(self, text: str, level: int = 1) -> None:
        fontsize = 12.5 if level == 1 else 11.0
        leading = 17.0 if level == 1 else 15.0
        self.ensure_space(leading + 18)
        self.y += 10 if level == 1 else 6
        self.page.insert_text(
            (self.margin_x, self.y),
            text,
            fontname="hebo",
            fontsize=fontsize,
            color=(0.05, 0.05, 0.05),
        )
        self.y += leading

    def table(self, spec: dict[str, Any]) -> None:
        caption_lines = _wrap(self.pymupdf, spec["caption"], "tiro", 9.0, self.content_width)
        self.ensure_space(20 + 16 * (len(spec["rows"]) + 2) + len(caption_lines) * 12)
        self.y += 4
        self.paragraph(spec["caption"], fontname="tiro", fontsize=9.0, leading=12.0)
        widths = spec["widths"]
        col_x: list[float] = []
        x = self.margin_x
        for fraction in widths:
            col_x.append(x)
            x += self.content_width * fraction
        row_height = 15.0
        header = spec["header"]
        for index, cell in enumerate(header):
            self.page.insert_text(
                (col_x[index], self.y), cell, fontname="hebo", fontsize=8.5, color=(0, 0, 0)
            )
        self.y += 3
        self.page.draw_line(
            (self.margin_x, self.y), (self.page_width - self.margin_x, self.y), width=0.7
        )
        self.y += row_height - 3
        for row in spec["rows"]:
            for index, cell in enumerate(row):
                self.page.insert_text(
                    (col_x[index], self.y), cell, fontname="tiro", fontsize=8.5
                )
            self.y += row_height
        self.page.draw_line(
            (self.margin_x, self.y - 3), (self.page_width - self.margin_x, self.y - 3), width=0.7
        )
        self.y += 10

    def save(self, path: Path) -> None:
        self._finish_page()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.doc.save(str(path))
        self.doc.close()


def build_pdf(path: Path) -> None:
    try:
        import pymupdf  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - 环境缺依赖时给出可操作提示
        raise SystemExit(
            "缺少 pymupdf，无法生成 demo 论文 PDF。安装：uv pip install -e \".[dev]\""
        ) from exc

    writer = _PdfWriter(pymupdf)
    writer.new_page()
    writer.y = 96
    writer.centered(TITLE, fontname="hebo", fontsize=17, leading=22)
    writer.y += 8
    writer.centered(AUTHORS, fontname="tiro", fontsize=10.5, leading=14)
    writer.centered(AFFILIATION, fontname="tiro", fontsize=9, leading=12)
    writer.centered(ARXIV_LIKE_ID, fontname="tiro", fontsize=9, leading=12)
    writer.y += 14
    writer.page.draw_line(
        (writer.margin_x, writer.y), (writer.page_width - writer.margin_x, writer.y), width=0.6
    )
    writer.y += 16
    writer.heading("Abstract", level=1)
    writer.paragraph(ABSTRACT, fontname="tiro", fontsize=10.0, leading=14.2)
    writer.y += 4

    for kind, payload in SECTIONS:
        if kind == "h1":
            writer.heading(payload, level=1)
        elif kind == "h2":
            writer.heading(payload, level=2)
        elif kind == "table":
            writer.table(payload)
        elif kind == "p":
            writer.paragraph(payload)
    for reference in REFERENCES:
        writer.paragraph(reference, fontname="tiro", fontsize=8.8, leading=12.2)
    writer.save(path)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def validate_fixtures() -> list[str]:
    """校验除 PDF 外的 fixtures 是否存在且能被解析。"""
    problems: list[str] = []
    json_file = FIXTURES_DIR / "arxiv_search.json"
    if not json_file.is_file():
        problems.append("缺少 arxiv_search.json")
    else:
        data = json.loads(json_file.read_text(encoding="utf-8"))
        count = len(data.get("results", []))
        if count < 6:
            problems.append(f"arxiv_search.json 只有 {count} 篇（要求 ≥6）")
        else:
            print(f"  ✓ arxiv_search.json: {count} 篇论文")

    daily_file = FIXTURES_DIR / "daily_arxiv.jsonl"
    if not daily_file.is_file():
        problems.append("缺少 daily_arxiv.jsonl")
    else:
        lines = [line for line in daily_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        for line in lines:
            json.loads(line)
        if len(lines) < 3:
            problems.append(f"daily_arxiv.jsonl 只有 {len(lines)} 行（要求 3 行）")
        else:
            print(f"  ✓ daily_arxiv.jsonl: {len(lines)} 行")

    try:
        import yaml

        for name in ("arch_spec.yaml", "plot_spec.yaml"):
            spec_file = FIXTURES_DIR / name
            if not spec_file.is_file():
                problems.append(f"缺少 {name}")
                continue
            spec = yaml.safe_load(spec_file.read_text(encoding="utf-8"))
            if not isinstance(spec, dict):
                problems.append(f"{name} 不是 YAML 映射")
            else:
                print(f"  ✓ {name}: {len(spec)} 个顶层字段")
    except ImportError:  # pragma: no cover
        problems.append("缺少 pyyaml，无法校验 YAML fixtures")

    metrics_file = FIXTURES_DIR / "results" / "metrics.json"
    if not metrics_file.is_file():
        problems.append("缺少 results/metrics.json")
    else:
        metrics = json.loads(metrics_file.read_text(encoding="utf-8"))
        print(f"  ✓ results/metrics.json: main_results {len(metrics.get('main_results', []))} 行")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 SciForge 离线 demo fixtures")
    parser.add_argument("--check", action="store_true", help="只校验现有 fixtures，不重新生成 PDF")
    args = parser.parse_args()

    print(f"fixtures 目录：{FIXTURES_DIR}")
    problems = validate_fixtures()

    if not args.check:
        build_pdf(PAPER_PDF)
        PAPER_TXT.write_text(build_text(), encoding="utf-8")
        print(f"  ✓ paper.pdf: {PAPER_PDF.stat().st_size / 1024:.1f} KB")
        print(f"  ✓ paper.txt: {PAPER_TXT.stat().st_size / 1024:.1f} KB")
    else:
        for name in ("paper.pdf", "paper.txt"):
            path = FIXTURES_DIR / name
            print(f"  {'✓' if path.is_file() else '✗'} {name}: "
                  f"{path.stat().st_size / 1024:.1f} KB" if path.is_file() else f"  ✗ 缺少 {name}")

    if problems:
        print("\n发现 fixtures 问题：")
        for problem in problems:
            print("  ✗", problem)
        return 1
    print("\n全部 fixtures 就绪。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
