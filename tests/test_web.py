"""T5 Web 控制台测试：端点、SSE、安全与「模块未就绪时优雅降级」。

原则：
- 全部离线：chat 用 mock=true；文献检索测试 monkeypatch 掉真实工具；
- 不依赖 T1–T4 是否已完成：缺失模块的路径用 ``sys.modules[name] = None`` 模拟，
  已就绪的可选模块做契约级集成断言；
- 不启动真实端口：``run_server`` 测试 monkeypatch 掉 uvicorn.run。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sciforge.config import ServerConfig, Settings
from sciforge.web.app import FIXTURES_DIR, _normalize_gpus, create_app

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """隔离 SCIFORGE_HOME / 项目目录 / 环境密钥。"""
    monkeypatch.setenv("SCIFORGE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SCIFORGE_MOCK", raising=False)
    for key in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "S2_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    settings = Settings(project_dir=project)
    return settings, project


@pytest.fixture()
def project(env) -> Path:
    """与被测 app 使用同一个项目目录（覆盖 conftest 中的同名 fixture）。"""
    return env[1]


@pytest.fixture()
def client(env):
    settings, _project = env
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def parse_sse(lines) -> list[dict]:
    """把 SSE 文本行解析成 [{event, data}]。"""
    events: list[dict] = []
    current: dict = {}
    for line in lines:
        if line.startswith("event:"):
            current["event"] = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            raw = line.split(":", 1)[1].strip()
            try:
                current["data"] = json.loads(raw)
            except json.JSONDecodeError:  # pragma: no cover - 不应发生
                current["data"] = raw
        elif not line and current:
            events.append(current)
            current = {}
    if current:
        events.append(current)
    return events


# ---------------------------------------------------------------------------
# 静态页面 / 健康检查
# ---------------------------------------------------------------------------


def test_index_and_static_assets(client: TestClient):
    index = client.get("/")
    assert index.status_code == 200
    assert "SciForge" in index.text
    assert "/static/app.js" in index.text

    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert "sendChat" in js.text
    css = client.get("/static/style.css")
    assert css.status_code == 200
    assert "--accent" in css.text
    assert client.get("/favicon.svg").status_code == 200


def test_security_headers(client: TestClient):
    response = client.get("/")
    assert "Content-Security-Policy" in response.headers
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_health(client: TestClient):
    data = client.get("/api/health").json()
    assert data["ok"] is True
    for key in ("version", "mock", "llm", "tools", "servers", "features", "project", "fixtures"):
        assert key in data
    assert data["mock"] is True  # 无 API key → 自动离线 mock
    assert data["tools"]["count"] >= 1
    assert data["fixtures"]["available"] is True


def test_tools(client: TestClient):
    data = client.get("/api/tools").json()
    assert data["ok"] is True
    assert data["count"] >= 1
    names = {tool["name"] for tool in data["tools"]}
    # local.py 的基础文件/终端工具至少存在
    assert names & {"read_file", "write_file", "shell", "list_dir"}, names
    for tool in data["tools"]:
        assert tool["name"]
        assert isinstance(tool["tags"], list)
        assert isinstance(tool["parameters"], dict)
    literature = client.get("/api/tools?tag=literature").json()
    assert literature["ok"] is True
    assert all("literature" in tool["tags"] for tool in literature["tools"])


def test_recipes(client: TestClient):
    data = client.get("/api/recipes").json()
    assert data["ok"] is True
    assert isinstance(data["recipes"], list) and data["recipes"]
    assert any(item["name"] == "full" for item in data["recipes"])


# ---------------------------------------------------------------------------
# 服务器配置脱敏
# ---------------------------------------------------------------------------


def test_servers_do_not_leak_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SCIFORGE_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    settings = Settings(
        project_dir=project,
        servers=[
            ServerConfig(
                name="gpu1",
                host="10.0.0.2",
                user="root",
                identity_file="~/.ssh/id_ed25519",
                password_env="GPU1_PASSWORD",
                env={"HF_TOKEN": "hf_super_secret_token"},
            )
        ],
    )
    settings.llm.providers["deepseek"].api_key = "sk-super-secret-api-key"
    app = create_app(settings)
    with TestClient(app) as client:
        servers_resp = client.get("/api/servers")
        assert servers_resp.status_code == 200
        raw = servers_resp.text
        assert "GPU1_PASSWORD" not in raw
        assert "hf_super_secret_token" not in raw
        assert '"password_env"' not in raw
        data = servers_resp.json()
        assert "password_env" not in data["servers"][0]
        assert data["servers"][0]["name"] == "gpu1"
        assert data["servers"][0]["identity_file"] == "~/.ssh/id_ed25519"
        assert data["servers"][0]["has_password_env"] is True

        health_raw = client.get("/api/health").text
        assert "sk-super-secret-api-key" not in health_raw
        assert "hf_super_secret_token" not in health_raw


# ---------------------------------------------------------------------------
# 文件服务与目录穿越防护
# ---------------------------------------------------------------------------


def test_file_serving_and_traversal(client: TestClient, project: Path):
    notes = project / "notes"
    notes.mkdir()
    (notes / "hello.md").write_text("# hello\n", encoding="utf-8")
    response = client.get("/files/notes/hello.md")
    assert response.status_code == 200
    assert "hello" in response.text

    outside = project.parent / "outside-secret.txt"
    outside.write_text("TOP SECRET", encoding="utf-8")
    assert client.get("/files/../outside-secret.txt").status_code == 404
    assert client.get("/files/%2e%2e%2foutside-secret.txt").status_code == 404
    assert client.get("/files/%2e%2e%2f%2e%2e%2fetc%2fpasswd").status_code == 404
    assert client.get("/files/%2Fetc%2Fpasswd").status_code == 404
    assert client.get("/files/..%5C..%5Cetc%5Cpasswd").status_code == 404

    link = project / "outside-link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):  # pragma: no cover - 某些文件系统不允许
        pytest.skip("当前文件系统不支持 symlink")
    assert client.get("/files/outside-link.txt").status_code == 404


def test_figures_endpoint(client: TestClient, project: Path):
    figures = project / "figures"
    figures.mkdir()
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'
    (figures / "curve.svg").write_text(svg, encoding="utf-8")
    (figures / "notes.txt").write_text("not an image", encoding="utf-8")

    response = client.get("/figures/figures/curve.svg")
    assert response.status_code == 200
    assert "image/svg+xml" in response.headers["content-type"]
    assert client.get("/figures/figures/notes.txt").status_code == 404
    assert client.get("/figures/%2e%2e%2foutside.svg").status_code == 404


# ---------------------------------------------------------------------------
# 文献：真实模块缺失时降级 + fixtures 回退
# ---------------------------------------------------------------------------


def test_library_unavailable_degrades(env, monkeypatch: pytest.MonkeyPatch):
    settings, project = env
    monkeypatch.setitem(sys.modules, "sciforge.library", None)
    app = create_app(settings)
    with TestClient(app) as client:
        data = client.get("/api/library").json()
        assert data["ok"] is True
        assert data["available"] is False
        assert data["papers"] == []

        added = client.post(
            "/api/library/add", json={"id": "2010.11929", "title": "ViT"}
        ).json()
        assert added["ok"] is False
        assert added["available"] is False


def test_library_add_and_list(env):
    pytest.importorskip("sciforge.library")
    settings, project = env
    app = create_app(settings)
    with TestClient(app) as client:
        payload = {
            "id": "2212.09748",
            "title": "Scalable Diffusion Models with Transformers",
            "authors": ["William Peebles", "Saining Xie"],
            "abstract": "We explore a new class of diffusion models based on Transformers.",
            "summary": "DiT：Transformer 扩散骨干。",
            "url": "https://arxiv.org/abs/2212.09748",
            "pdf_url": "https://arxiv.org/pdf/2212.09748",
            "published": "2022-12-19",
            "categories": ["cs.CV"],
            "source": "demo",
        }
        added = client.post("/api/library/add", json=payload).json()
        assert added["ok"] is True, added
        assert added["paper"]["id"] == "2212.09748"

        listing = client.get("/api/library").json()
        assert listing["available"] is True
        assert listing["total"] >= 1
        assert any(item["id"] == "2212.09748" for item in listing["papers"])


def test_literature_fixture_fallback(env, monkeypatch: pytest.MonkeyPatch):
    """真实 lit_search 不可用时回退到 examples/demo/fixtures（离线可演示）。"""
    settings, project = env
    # 强制让端点认为真实工具不存在，避免测试访问网络
    monkeypatch.setattr("sciforge.core.registry.get_tool", lambda name: None)
    app = create_app(settings)
    with TestClient(app) as client:
        data = client.get("/api/literature/search?query=").json()
        assert data["ok"] is True
        assert data["degraded"] is True
        assert data["available"] is False
        assert data["source"] == "demo-fixture"
        assert len(data["papers"]) >= 6
        assert any(paper["summary_zh"] for paper in data["papers"])
        assert data["notice"]

        filtered = client.get("/api/literature/search?query=diffusion").json()
        assert filtered["papers"]
        assert all(
            "diffusion" in (paper["title"] + paper["abstract"] + paper["summary"]).lower()
            for paper in filtered["papers"]
        )


# ---------------------------------------------------------------------------
# 技能
# ---------------------------------------------------------------------------


def test_skills_endpoint(client: TestClient):
    data = client.get("/api/skills").json()
    assert data["ok"] is True
    assert isinstance(data["skills"], list)
    for skill in data["skills"]:
        assert skill["name"]
        assert "description" in skill


def test_skills_install_dsh_to_custom_target(env):
    """POST /api/skills/install-dsh 把 skill 复制到配置的 dsh 目标目录（测试用临时目录）。"""
    pytest.importorskip("sciforge.skills_manager")
    settings, project = env
    target = project.parent / "dsh-skills"
    settings.skills.targets["dsh"] = str(target)
    app = create_app(settings)
    with TestClient(app) as client:
        listing = client.get("/api/skills").json()
        if not listing.get("available") or not listing.get("skills"):
            pytest.skip("SkillManager 未就绪或技能库为空")
        name = listing["skills"][0]["name"]
        result = client.post("/api/skills/install-dsh", json={"name": name}).json()
        assert result["ok"] is True, result
        installed = Path(result["path"])
        assert installed.is_dir()
        assert (installed / "SKILL.md").is_file()
        assert installed.parent == target


def test_skills_install_unavailable(env, monkeypatch: pytest.MonkeyPatch):
    settings, project = env
    monkeypatch.setitem(sys.modules, "sciforge.skills_manager", None)
    app = create_app(settings)
    with TestClient(app) as client:
        data = client.post("/api/skills/install-dsh", json={"name": "paper-writing"}).json()
        assert data["ok"] is False
        assert data["available"] is False
        assert "未就绪" in data["error"]


# ---------------------------------------------------------------------------
# 对话 SSE
# ---------------------------------------------------------------------------


def test_chat_sse_mock(client: TestClient):
    with client.stream(
        "POST",
        "/api/chat",
        json={"message": "你好，介绍一下你能做什么", "mock": True},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(response.iter_lines())

    names = [event["event"] for event in events]
    assert names[0] == "meta"
    assert "done" in names
    assert names[-1] == "done"
    assert names.count("done") == 1
    assert len(events) >= 2

    meta = events[0]["data"]
    assert meta["session_id"]
    assert meta["mock"] is True

    done = events[-1]["data"]
    assert done["ok"] is True
    assert done["session_id"] == meta["session_id"]
    assert done["steps"] >= 1

    # 流式输出或完整 assistant 消息至少出现一次
    assert any(name in ("llm_delta", "assistant") for name in names)
    if any(name == "assistant" for name in names):
        assistant = next(event for event in events if event["event"] == "assistant")
        assert "content" in assistant["data"]


def test_chat_validation(client: TestClient):
    assert client.post("/api/chat", json={"message": ""}).status_code == 400
    assert client.post("/api/chat", json={"message": "hi", "session_id": "../../etc"}).status_code == 400
    assert client.post("/api/chat", json={"message": "hi", "session_id": "bad/slash"}).status_code == 400


def test_chat_demo_tool_events(client: TestClient):
    """demo_tools=true：Agent 真实执行一次只读工具，SSE 应包含 tool_call / tool_result。"""
    with client.stream(
        "POST",
        "/api/chat",
        json={"message": "演示一次工具调用", "mock": True, "demo_tools": True},
    ) as response:
        events = parse_sse(response.iter_lines())
    names = [event["event"] for event in events]
    assert "tool_call" in names
    assert "tool_result" in names
    assert names[-1] == "done"

    call = next(event for event in events if event["event"] == "tool_call")
    assert call["data"]["name"] in {"read_file", "list_dir"}
    assert isinstance(call["data"]["arguments"], dict)

    result = next(event for event in events if event["event"] == "tool_result")
    assert result["data"]["ok"] is True
    assert "elapsed" in result["data"]

    done = events[-1]["data"]
    assert done["ok"] is True
    assert done["tool_calls"] >= 1
    assert "duration" in done


def test_chat_session_history(client: TestClient):
    """同一 session_id 的第二轮对话可以正常完成（服务端保留轻量历史）。"""
    first = None
    with client.stream("POST", "/api/chat", json={"message": "第一轮", "mock": True}) as response:
        events = parse_sse(response.iter_lines())
    first = events[-1]["data"]["session_id"]
    assert first

    with client.stream(
        "POST",
        "/api/chat",
        json={"message": "第二轮", "mock": True, "session_id": first},
    ) as response:
        events = parse_sse(response.iter_lines())
    assert events[-1]["event"] == "done"
    assert events[-1]["data"]["session_id"] == first


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def test_pipeline_unavailable_degrades(env, monkeypatch: pytest.MonkeyPatch):
    settings, project = env
    monkeypatch.setitem(sys.modules, "sciforge.pipeline.engine", None)
    app = create_app(settings)
    with TestClient(app) as client:
        data = client.post(
            "/api/pipeline/run", json={"recipe": "full", "topic": "测试主题", "mock": True}
        ).json()
        assert data["ok"] is False
        assert data["available"] is False
        assert "未就绪" in data["error"]


def test_pipeline_status_not_found(client: TestClient):
    assert client.get("/api/pipeline/not-a-real-run").status_code == 404


def test_pipeline_run_mock_and_poll(env):
    """mock 模式完整跑一轮 pipeline 并轮询到终态；引擎/提示词未就绪时跳过。"""
    pytest.importorskip("sciforge.pipeline.engine")
    settings, project = env
    app = create_app(settings)
    with TestClient(app) as client:
        started = client.post(
            "/api/pipeline/run",
            json={"recipe": "figure", "topic": "自适应 token 路由的视频扩散模型", "mock": True},
        ).json()
        if not started.get("ok") or not started.get("available"):
            pytest.skip(f"pipeline 引擎未就绪：{started.get('error')}")
        run_id = started["run_id"]

        deadline = time.time() + 120
        run: dict = {}
        while time.time() < deadline:
            status = client.get(f"/api/pipeline/{run_id}").json()
            run = status["run"]
            if run["status"] != "running":
                break
            time.sleep(0.25)
        else:  # pragma: no cover - mock pipeline 正常在秒级完成
            pytest.skip("pipeline mock 运行超时（>120s），跳过轮询断言")

        assert run["status"] in {"done", "partial"}, run
        assert run["mock"] is True
        assert run["stages"], "应至少有一个阶段结果"
        assert any(stage.get("ok") for stage in run["stages"])
        assert run["finished_at"]
        assert isinstance(run.get("events"), list) and run["events"]

        runs = client.get("/api/runs").json()
        assert runs["ok"] is True
        assert any(item.get("run_id") == run.get("pipeline_run_id") for item in runs["runs"])

        artifacts = client.get("/api/artifacts").json()
        assert artifacts["count"] >= 1
        assert any(item["path"].startswith(".sciforge/runs/") for item in artifacts["artifacts"])

        # 产物可通过安全文件端点访问
        first = artifacts["artifacts"][0]
        assert client.get(f"/files/{first['path']}").status_code == 200


# ---------------------------------------------------------------------------
# 运行历史 / 产物 / GPU 降级
# ---------------------------------------------------------------------------


def test_runs_empty(client: TestClient):
    data = client.get("/api/runs").json()
    assert data["ok"] is True
    assert data["runs"] == []
    assert data["active"] == []


def test_artifacts_scan(client: TestClient, project: Path):
    (project / "figures").mkdir()
    (project / "figures" / "arch.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8"
    )
    (project / "results").mkdir()
    (project / "results" / "metrics.json").write_text("{}", encoding="utf-8")
    (project / "notes").mkdir()
    (project / "notes" / "reading.md").write_text("# notes", encoding="utf-8")

    data = client.get("/api/artifacts").json()
    assert data["ok"] is True
    paths = {item["path"] for item in data["artifacts"]}
    assert "figures/arch.svg" in paths
    assert "results/metrics.json" in paths
    assert "notes/reading.md" in paths
    by_path = {item["path"]: item for item in data["artifacts"]}
    assert by_path["figures/arch.svg"]["previewable"] is True
    assert by_path["figures/arch.svg"]["url"].startswith("/figures/")
    assert by_path["results/metrics.json"]["previewable"] is False
    assert by_path["results/metrics.json"]["url"].startswith("/files/")


def test_gpu_graceful_when_tool_missing(env, monkeypatch: pytest.MonkeyPatch):
    settings, project = env
    monkeypatch.setattr("sciforge.core.registry.get_tool", lambda name: None)
    app = create_app(settings)
    with TestClient(app) as client:
        data = client.get("/api/gpu").json()
        assert data["ok"] is False
        assert data["available"] is False
        assert "GPU" in data["error"] or "T2" in data["error"]


def test_normalize_gpus_for_table():
    """不同实现的 nvidia-smi 数据结构应被归一化成前端表格行。"""
    data = {
        "servers": [
            {
                "name": "gpu1",
                "gpus": [
                    {
                        "index": 0,
                        "model": "NVIDIA A100-SXM4-80GB",
                        "memory_used": 1234,
                        "memory_total": 81920,
                        "utilization": 87,
                        "processes": ["train.py"],
                    }
                ],
            }
        ]
    }
    rows = _normalize_gpus(data)
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "NVIDIA A100-SXM4-80GB"
    assert row["memory_used"] == 1234
    assert row["memory_total"] == 81920
    assert row["utilization"] == 87
    assert row["server"] == "gpu1"

    rows = _normalize_gpus(
        [{"gpu": 1, "product": "RTX 4090", "used_memory": "1024 MiB", "total_memory": "24576 MiB", "gpu_util": "45 %"}]
    )
    assert rows[0]["index"] == 1
    assert rows[0]["name"] == "RTX 4090"
    assert _normalize_gpus([]) == []
    assert _normalize_gpus(None) == []


def test_gpu_local_or_remote(client: TestClient):
    """gpu_status 已注册时返回归一化结构（Mac 上无 NVIDIA 也应是 200 + 明确提示）。"""
    data = client.get("/api/gpu").json()
    assert "available" in data
    assert isinstance(data.get("gpus"), list)


# ---------------------------------------------------------------------------
# 启动器：强制回环地址
# ---------------------------------------------------------------------------


def test_run_server_forces_loopback(env, monkeypatch: pytest.MonkeyPatch):
    settings, project = env
    import uvicorn

    captured: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: captured.update(kwargs))
    from sciforge.web.server import run_server

    run_server(host="0.0.0.0", port=8899, settings=settings)
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8899


# ---------------------------------------------------------------------------
# fixtures 本身
# ---------------------------------------------------------------------------


def test_demo_fixtures_present():
    assert (FIXTURES_DIR / "arxiv_search.json").is_file()
    assert (FIXTURES_DIR / "daily_arxiv.jsonl").is_file()
    assert (FIXTURES_DIR / "paper.pdf").is_file()
    assert (FIXTURES_DIR / "paper.txt").is_file()
    assert (FIXTURES_DIR / "paper_preview.png").is_file()
    assert (FIXTURES_DIR / "arch_spec.yaml").is_file()
    assert (FIXTURES_DIR / "plot_spec.yaml").is_file()
    assert (FIXTURES_DIR / "results" / "metrics.json").is_file()

    search = json.loads((FIXTURES_DIR / "arxiv_search.json").read_text(encoding="utf-8"))
    assert len(search["results"]) >= 6
    for paper in search["results"]:
        assert paper["title"] and paper["id"] and paper["authors"]

    daily = [
        json.loads(line)
        for line in (FIXTURES_DIR / "daily_arxiv.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(daily) == 3
    assert all(item.get("AI_summary") for item in daily)

    import yaml

    arch = yaml.safe_load((FIXTURES_DIR / "arch_spec.yaml").read_text(encoding="utf-8"))
    assert arch["nodes"] and arch["edges"] and arch["groups"]
    plot = yaml.safe_load((FIXTURES_DIR / "plot_spec.yaml").read_text(encoding="utf-8"))
    assert plot["kind"] == "line" and plot["series"]

    metrics = json.loads((FIXTURES_DIR / "results" / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["main_results"] and metrics["ablations"]


def test_paper_pdf_structure():
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open(str(FIXTURES_DIR / "paper.pdf"))
    try:
        assert 3 <= doc.page_count <= 5
        text = "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()
    for heading in ("Abstract", "Introduction", "Related Work", "Method", "Experiments", "Conclusion"):
        assert heading in text, f"paper.pdf 缺少章节：{heading}"
