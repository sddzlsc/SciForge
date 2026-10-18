#!/usr/bin/env python
"""训练日志快速体检：NaN/Inf、OOM、异常栈、梯度爆炸与 loss 震荡。

用法::

    python diagnose_nan.py runs/exp001/train.log
    python diagnose_nan.py runs/exp001/*.log --json --tail 5000

纯标准库实现，不依赖 torch/numpy，适合在远端或本地快速扫描日志。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

NAN_PATTERNS = (
    re.compile(r"(?i)\bnan\b"),
    re.compile(r"(?i)\binf\b"),
    re.compile(r"(?i)not\s+finite"),
)
OOM_RE = re.compile(r"(?i)out\s+of\s+memory|CUDA\s+error:\s+out\s+of\s+memory|OOM")
TRACEBACK_RE = re.compile(r"Traceback \(most recent call last\)")
CUDA_ERROR_RE = re.compile(r"(?i)CUDA error|device-side assert|illegal memory access|NCCL")
LOSS_RE = re.compile(r"(?i)(?:^|[\s,;])(?:train[_/\s-]?)?loss[^0-9+-]{0,12}([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)")
GRAD_RE = re.compile(r"(?i)(?:grad[_/\s-]?norm|gnorm)[^0-9+-]{0,12}([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)")


def _read_lines(path: Path, tail: int) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"[错误] 无法读取 {path}: {exc}", file=sys.stderr)
        return []
    lines = text.splitlines()
    return lines[-tail:] if tail > 0 else lines


def analyze(path: Path, *, tail: int = 4000, grad_threshold: float = 1e3) -> dict:
    """扫描单个日志文件并返回结构化诊断结果。"""
    lines = _read_lines(path, tail)
    nan_lines: list[tuple[int, str]] = []
    oom_lines: list[str] = []
    traceback_count = 0
    cuda_errors: list[str] = []
    losses: list[float] = []
    grad_norms: list[float] = []

    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if any(pattern.search(stripped) for pattern in NAN_PATTERNS):
            nan_lines.append((index, stripped[:200]))
        if OOM_RE.search(stripped):
            oom_lines.append(stripped[:200])
        if TRACEBACK_RE.search(stripped):
            traceback_count += 1
        if CUDA_ERROR_RE.search(stripped):
            cuda_errors.append(stripped[:200])
        loss_match = LOSS_RE.search(stripped)
        if loss_match:
            try:
                losses.append(float(loss_match.group(1)))
            except ValueError:
                pass
        grad_match = GRAD_RE.search(stripped)
        if grad_match:
            try:
                grad_norms.append(float(grad_match.group(1)))
            except ValueError:
                pass

    findings: list[str] = []
    if nan_lines:
        first_line, first_text = nan_lines[0]
        findings.append(f"发现 {len(nan_lines)} 行 NaN/Inf，最早在第 {first_line} 行：{first_text}")
        findings.append("建议：开 torch.autograd.set_detect_anomaly 找第一个非有限算子；检查数据 NaN、log/除零、fp16 溢出。")
    if oom_lines:
        findings.append(f"发现 {len(oom_lines)} 处 OOM（示例：{oom_lines[0]}）")
        findings.append("建议：梯度累积/AMP/梯度检查点/降 batch；确认 backward 后没有保留计算图。")
    if traceback_count:
        findings.append(f"发现 {traceback_count} 个 Python Traceback，先修最早的异常。")
    if cuda_errors:
        findings.append(f"发现 {len(cuda_errors)} 条 CUDA/NCCL 错误（示例：{cuda_errors[0]}）")
    spike_grads = [value for value in grad_norms if value > grad_threshold]
    if spike_grads:
        findings.append(f"梯度范数超过 {grad_threshold:g} 共 {len(spike_grads)} 次，最大 {max(grad_norms):.3g}；考虑梯度裁剪/warmup/降 lr。")
    if len(losses) >= 10:
        head = sum(losses[:5]) / 5.0
        tail_avg = sum(losses[-5:]) / 5.0
        if tail_avg > head * 1.2 and head > 0:
            findings.append(f"loss 上升：前 5 个均值 {head:.4g} → 后 5 个均值 {tail_avg:.4g}，检查 lr/数据顺序。")
        if head > 0 and all(value > head * 0.99 for value in losses[-20:]):
            findings.append("最近 20 条 loss 几乎不降，先过拟合 1 个 batch 验证实现。")
    if not findings:
        findings.append("未发现明显 NaN/OOM/Traceback；建议继续看 GPU 利用率与验证指标曲线。")

    return {
        "file": str(path),
        "lines_scanned": len(lines),
        "nan_count": len(nan_lines),
        "oom_count": len(oom_lines),
        "traceback_count": traceback_count,
        "cuda_error_count": len(cuda_errors),
        "loss_samples": len(losses),
        "grad_samples": len(grad_norms),
        "max_grad_norm": max(grad_norms) if grad_norms else None,
        "findings": findings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="训练日志 NaN/OOM/梯度异常体检")
    parser.add_argument("logs", nargs="+", help="一个或多个日志文件")
    parser.add_argument("--tail", type=int, default=4000, help="只扫描文件末尾 N 行，0 表示全文件")
    parser.add_argument("--grad-threshold", type=float, default=1e3, help="梯度范数告警阈值")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    reports = []
    for raw in args.logs:
        path = Path(raw)
        if not path.exists():
            print(f"[错误] 文件不存在: {path}", file=sys.stderr)
            continue
        reports.append(analyze(path, tail=args.tail, grad_threshold=args.grad_threshold))

    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    else:
        for report in reports:
            print(f"\n== {report['file']}（扫描 {report['lines_scanned']} 行）==")
            for finding in report["findings"]:
                print(f"- {finding}")
    return 0 if reports else 1


if __name__ == "__main__":
    raise SystemExit(main())
