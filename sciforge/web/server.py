"""SciForge Web 控制台启动器：``sciforge serve`` 的底层实现。

默认只监听回环地址 ``127.0.0.1:8765``（安全约束，绝不默认暴露到局域网/公网）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sciforge.config import Settings, get_settings

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_PORT = 8765


def run_server(
    host: str | None = None,
    port: int | None = None,
    settings: Settings | None = None,
) -> None:
    """启动本地 Web 控制台（阻塞直到 Ctrl+C）。

    参数优先级：显式参数 > ``settings.web`` > 默认值。
    """
    settings = settings or get_settings()
    config = settings.web

    bind_host = (host or config.host or "127.0.0.1").strip()
    if bind_host not in LOOPBACK_HOSTS:
        print(
            f"⚠️  安全限制：Web 控制台只允许监听回环地址，已忽略 {bind_host!r}，改用 127.0.0.1。"
        )
        bind_host = "127.0.0.1"

    try:
        bind_port = int(port if port is not None else config.port or DEFAULT_PORT)
    except (TypeError, ValueError):
        bind_port = DEFAULT_PORT
    if not (1 <= bind_port <= 65535):
        raise SystemExit(f"端口非法：{bind_port}")

    from sciforge.web.app import create_app

    app = create_app(settings)
    url = f"http://{bind_host}:{bind_port}"

    print("=" * 68)
    print("  SciForge 本地 Web 控制台")
    print("=" * 68)
    print(f"  访问地址 : {url}")
    print(f"  健康检查 : {url}/api/health")
    print(f"  API 文档 : {url}/api/docs")
    print(f"  项目目录 : {app.state.project_dir}")
    print("  提示     : 默认离线 Mock 可用；配置 DEEPSEEK_API_KEY 后走真实模型")
    print("  停止服务 : Ctrl+C")
    print("=" * 68)
    sys.stdout.flush()

    if config.open_browser and os.environ.get("SCIFORGE_NO_BROWSER", "") not in ("1", "true"):
        _open_browser(url)

    import uvicorn

    uvicorn.run(app, host=bind_host, port=bind_port, log_level="info")


def _open_browser(url: str) -> None:
    try:
        import webbrowser

        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - 打不开浏览器不影响服务
        pass


def serve(host: str | None = None, port: int | None = None) -> None:
    """``sciforge serve`` 的便捷别名。"""
    run_server(host=host, port=port)


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="SciForge 本地 Web 控制台")
    parser.add_argument("--host", default=None, help="监听地址（仅允许 127.0.0.1/localhost）")
    parser.add_argument("--port", type=int, default=None, help="端口（默认 8765）")
    parser.add_argument("--project", default=None, help="项目目录（默认当前目录）")
    args = parser.parse_args()
    settings = get_settings(args.project)
    if args.project:
        settings.project_dir = Path(args.project).expanduser().resolve()
    run_server(host=args.host, port=args.port, settings=settings)


if __name__ == "__main__":  # pragma: no cover
    _main()
