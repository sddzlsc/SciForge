# LLM 配置：DeepSeek 与任意 OpenAI 兼容 API

SciForge 只依赖 OpenAI Chat Completions 协议，因此任何兼容服务都能接入：
DeepSeek、OpenAI、OpenRouter、vLLM、Ollama、LM Studio、硅基流动、公司内网网关等。

## 配置文件位置

- 全局：`~/.sciforge/config.toml`（推荐，`chmod 600`）
- 项目：`<project>/.sciforge/project.toml`（只放项目级设置，不放密钥）
- 敏感 key 建议用环境变量（配置里写 `api_key_env`）

## DeepSeek（默认）

```toml
[llm]
default = "deepseek"

[llm.providers.deepseek]
type = "openai-compatible"
base_url = "https://api.deepseek.com/v1"
api_key_env = "DEEPSEEK_API_KEY"
default_model = "deepseek-flash"
models = ["deepseek-flash", "deepseek-v4-pro"]
input_cost_per_mtok = 0.27
output_cost_per_mtok = 1.10

[llm.roles.planner]
provider = "deepseek"
model = "deepseek-v4-pro"

[llm.roles.executor]
provider = "deepseek"
model = "deepseek-flash"

[llm.roles.writer]
provider = "deepseek"
model = "deepseek-flash"

[llm.roles.reviewer]
provider = "deepseek"
model = "deepseek-v4-pro"
```

## 本地 vLLM

```bash
vllm serve Qwen/Qwen2.5-72B-Instruct --port 8000
```

```toml
[llm.providers.local]
type = "openai-compatible"
base_url = "http://127.0.0.1:8000/v1"
api_key_env = "LOCAL_API_KEY"     # 本地可不校验，随便设一个
default_model = "Qwen/Qwen2.5-72B-Instruct"

[llm.roles.executor]
provider = "local"
model = "Qwen/Qwen2.5-72B-Instruct"
```

## Ollama

```toml
[llm.providers.ollama]
type = "openai-compatible"
base_url = "http://127.0.0.1:11434/v1"
api_key = "ollama"
default_model = "qwen2.5:32b"
```

## OpenRouter / 其他云服务

```toml
[llm.providers.openrouter]
type = "openai-compatible"
base_url = "https://openrouter.ai/api/v1"
api_key_env = "OPENROUTER_API_KEY"
default_model = "deepseek/deepseek-chat"  # OpenRouter 用其自有模型名
extra_headers = { "HTTP-Referer" = "http://localhost", "X-Title" = "SciForge" }
```

## 路由与降级策略

当前 DeepSeek 默认（2026-10 起）：**`deepseek-flash`（快） + `deepseek-v4-pro`（强推理）**。

- `planner`：调研、创新点、实验设计 → `deepseek-v4-pro`
- `executor`：工具调用密集（文献、远程执行）→ `deepseek-flash`
- `writer`：长文写作 → `deepseek-flash`（长上下文需求高时可改 v4-pro）
- `reviewer`：审稿自检 → `deepseek-v4-pro`
- `vision`：多模态辅助 → `deepseek-flash`
- 任一角色可指向同一个 provider/model；成本敏感时全用 `deepseek-flash`。
- 推理模型（`deepseek-v4-pro` / `-reasoner` 等）不接受 temperature 等采样参数，SciForge 会自动忽略。

## 验证

```bash
sciforge llm providers
sciforge llm test --role planner
sciforge llm test --role executor --prompt "用一句话说明什么是 MoE"
```

## 常见错误

| 错误 | 原因/解决 |
|---|---|
| 401 | key 无效；检查 `api_key_env` 指向的环境变量是否 export |
| 404 | base_url 少了 `/v1` 或服务不支持 Chat Completions |
| 400 + temperature | `deepseek-v4-pro` / reasoner 类模型不支持采样参数，SciForge 已自动忽略 |
| 429 | 限流：降低并发/换 key/给 provider 加 `timeout` |
| 工具调用乱码 | 模型 function calling 不稳定：换 executor 模型，或降低 `agent.max_steps` |
