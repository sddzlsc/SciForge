"""OpenAI 兼容 Chat Completions Provider。

支持 DeepSeek / OpenAI / OpenRouter / vLLM / Ollama / 硅基流动 等所有兼容服务。
只依赖 httpx，不引入官方 SDK。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import httpx

from sciforge.config import ProviderConfig
from sciforge.core.types import ChatMessage, LLMResponse, ToolCall, Usage
from sciforge.llm.base import LLMError, LLMProvider

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class OpenAICompatProvider(LLMProvider):
    def __init__(
        self,
        name: str,
        config: ProviderConfig,
        *,
        api_key: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.name = name
        self.config = config
        self.api_key = api_key if api_key is not None else config.api_key
        self.model = config.default_model
        self._client = client

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **self.config.extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @property
    def endpoint(self) -> str:
        base = self.config.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def _client_or_new(self) -> tuple[httpx.Client, bool]:
        if self._client is not None:
            return self._client, False
        return httpx.Client(timeout=self.config.timeout), True

    def _payload(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict] | None,
        model: str,
        temperature: float | None,
        max_tokens: int | None,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.to_api_dict() for m in messages],
            "stream": stream,
        }
        # deepseek-reasoner 不接受 temperature/top_p 等采样参数
        if temperature is not None and "reasoner" not in model.lower():
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    def _estimate_cost(self, usage: Usage) -> float:
        cost = (
            usage.prompt_tokens / 1_000_000 * self.config.input_cost_per_mtok
            + usage.completion_tokens / 1_000_000 * self.config.output_cost_per_mtok
        )
        return round(cost, 8)

    # ------------------------------------------------------------------
    # 调用
    # ------------------------------------------------------------------
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
        if not self.api_key:
            raise LLMError(
                f"provider [{self.name}] 缺少 API key：请设置环境变量 "
                f"{self.config.api_key_env or 'DEEPSEEK_API_KEY'}，"
                f"或在 ~/.sciforge/config.toml 的 [llm.providers.{self.name}] 配置 api_key。"
            )

        chosen_model = model or self.config.default_model
        # 有工具时走非流式（兼容性最好）；纯文本才流式
        do_stream = bool(stream and on_delta and not tools and self.config.supports_stream)
        payload = self._payload(
            messages,
            tools=tools,
            model=chosen_model,
            temperature=(
                temperature if temperature is not None else self.config.temperature
            ),
            max_tokens=max_tokens or self.config.max_tokens,
            stream=do_stream,
        )

        client, should_close = self._client_or_new()
        try:
            if do_stream:
                return self._chat_stream(client, payload, chosen_model, on_delta)
            return self._chat_once(client, payload, chosen_model)
        finally:
            if should_close:
                client.close()

    def _chat_once(
        self, client: httpx.Client, payload: dict[str, Any], chosen_model: str
    ) -> LLMResponse:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = client.post(self.endpoint, headers=self._headers(), json=payload)
            except httpx.RequestError as exc:
                last_error = exc
                time.sleep(min(2**attempt, 8))
                continue

            if response.status_code in RETRY_STATUS:
                last_error = LLMError(f"HTTP {response.status_code}: {response.text[:300]}")
                time.sleep(min(2**attempt, 8))
                continue
            if response.status_code >= 400:
                raise LLMError(self._format_http_error(response))
            return self._parse_response(response.json(), chosen_model)

        raise LLMError(f"LLM 请求失败（已重试 3 次）：{last_error}")

    def _chat_stream(
        self,
        client: httpx.Client,
        payload: dict[str, Any],
        chosen_model: str,
        on_delta: Callable[[str], None] | None,
    ) -> LLMResponse:
        chunks: list[str] = []
        usage = Usage()
        finish_reason = "stop"
        try:
            with client.stream(
                "POST", self.endpoint, headers=self._headers(), json=payload
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    raise LLMError(self._format_http_error(response))
                for line in response.iter_lines():
                    if not line:
                        continue
                    if line.startswith("data: "):
                        line = line[6:]
                    if line.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if data.get("usage"):
                        usage = self._parse_usage(data["usage"])
                    for choice in data.get("choices", []):
                        delta = choice.get("delta", {}) or {}
                        piece = delta.get("content") or ""
                        if piece:
                            chunks.append(piece)
                            if on_delta:
                                on_delta(piece)
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]
        except httpx.RequestError as exc:
            raise LLMError(f"LLM 流式请求失败：{exc}") from exc

        usage.cost_usd = self._estimate_cost(usage)
        return LLMResponse(
            content="".join(chunks),
            tool_calls=[],
            usage=usage,
            finish_reason=finish_reason,
            model=chosen_model,
        )

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------
    def _parse_response(self, data: dict[str, Any], chosen_model: str) -> LLMResponse:
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"LLM 返回异常（无 choices）：{str(data)[:500]}")
        choice = choices[0]
        message = choice.get("message") or {}
        tool_calls: list[ToolCall] = []
        for index, raw_call in enumerate(message.get("tool_calls") or []):
            function = raw_call.get("function") or {}
            arguments = function.get("arguments")
            parsed_args: dict[str, Any]
            if isinstance(arguments, dict):
                parsed_args = arguments
            else:
                try:
                    parsed_args = json.loads(arguments) if arguments else {}
                except json.JSONDecodeError:
                    parsed_args = {"__raw__": arguments}
            tool_calls.append(
                ToolCall(
                    id=raw_call.get("id") or f"call_{index}",
                    name=function.get("name") or "",
                    arguments=parsed_args,
                )
            )
        usage = self._parse_usage(data.get("usage") or {})
        usage.cost_usd = self._estimate_cost(usage)
        return LLMResponse(
            content=message.get("content") or "",
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=choice.get("finish_reason") or "stop",
            model=data.get("model") or chosen_model,
            raw=data,
        )

    @staticmethod
    def _parse_usage(raw: dict[str, Any]) -> Usage:
        return Usage(
            prompt_tokens=int(raw.get("prompt_tokens") or 0),
            completion_tokens=int(raw.get("completion_tokens") or 0),
            total_tokens=int(raw.get("total_tokens") or 0),
            calls=1,
        )

    @staticmethod
    def _format_http_error(response: httpx.Response) -> str:
        text = response.text[:600]
        if response.status_code == 401:
            return f"LLM 鉴权失败（401）：API key 无效或未配置。响应：{text}"
        if response.status_code == 404:
            return (
                f"LLM 接口不存在（404）：请检查 base_url 是否包含 /v1 且服务支持 "
                f"OpenAI Chat Completions。响应：{text}"
            )
        return f"LLM 请求失败（HTTP {response.status_code}）：{text}"


__all__ = ["OpenAICompatProvider"]
