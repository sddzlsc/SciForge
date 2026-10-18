"""LLM Provider / Router 测试（MockTransport，无真实网络）。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from sciforge.config import ProviderConfig, get_settings
from sciforge.core.types import ChatMessage
from sciforge.llm.base import LLMError
from sciforge.llm.mock import MockProvider, extract_topic
from sciforge.llm.openai_compat import OpenAICompatProvider
from sciforge.llm.router import LLMRouter


def _ok_response(body: str = "hello", tool_call: dict | None = None) -> dict:
    message: dict = {"role": "assistant", "content": body}
    if tool_call:
        message["tool_calls"] = [tool_call]
    return {
        "id": "x",
        "model": "deepseek-chat",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _provider(handler) -> OpenAICompatProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    config = ProviderConfig(
        base_url="https://api.deepseek.com/v1",
        default_model="deepseek-chat",
        input_cost_per_mtok=0.27,
        output_cost_per_mtok=1.10,
    )
    return OpenAICompatProvider("deepseek", config, api_key="sk-test", client=client)


def test_chat_parses_content_and_usage() -> None:
    provider = _provider(lambda request: httpx.Response(200, json=_ok_response("你好")))
    response = provider.chat([ChatMessage(role="user", content="hi")])
    assert response.content == "你好"
    assert response.usage.total_tokens == 15
    assert response.usage.cost_usd > 0
    assert response.model == "deepseek-chat"


def test_chat_parses_tool_calls() -> None:
    call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "lit_search", "arguments": json.dumps({"query": "diffusion"})},
    }
    provider = _provider(lambda request: httpx.Response(200, json=_ok_response("", call)))
    response = provider.chat([ChatMessage(role="user", content="hi")], tools=[{"type": "function"}])
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "lit_search"
    assert response.tool_calls[0].arguments["query"] == "diffusion"


def test_chat_401_gives_actionable_error() -> None:
    provider = _provider(lambda request: httpx.Response(401, json={"error": "bad key"}))
    with pytest.raises(LLMError) as excinfo:
        provider.chat([ChatMessage(role="user", content="hi")])
    assert "鉴权" in str(excinfo.value)


def test_missing_api_key_error() -> None:
    config = ProviderConfig(base_url="https://api.deepseek.com/v1", default_model="deepseek-chat", api_key_env="NOPE_KEY")
    provider = OpenAICompatProvider("deepseek", config, api_key=None)
    with pytest.raises(LLMError) as excinfo:
        provider.chat([ChatMessage(role="user", content="hi")])
    assert "API key" in str(excinfo.value)


def test_mock_provider_scripts() -> None:
    provider = MockProvider(scripts=["第一段", "第二段"])
    assert provider.chat([ChatMessage(role="user", content="a")]).content == "第一段"
    assert provider.chat([ChatMessage(role="user", content="b")]).content == "第二段"


def test_mock_provider_heuristic_topic() -> None:
    provider = MockProvider()
    response = provider.chat(
        [ChatMessage(role="user", content="研究主题：扩散模型加速\n请做文献调研")]
    )
    assert "扩散模型加速" in response.content
    assert extract_topic([ChatMessage(role="user", content="研究主题：X")]) == "X"


def test_router_role_routing_and_usage(sciforge_home: Path) -> None:
    settings = get_settings()
    router = LLMRouter(settings, force_mock=True)
    response = router.chat("planner", [ChatMessage(role="user", content="研究主题：测试")])
    assert response.content
    assert router.usage.calls == 1
    assert router.usage.total_tokens > 0
    assert router.check("planner")["provider"] == "mock"
