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
default_model = "deepseek-chat"
models = ["deepseek-chat", "deepseek-reasoner"]
input_cost_per_mtok = 0.27
output_cost_per_mtok = 1.10

[llm.roles.planner]
provider = "deepseek"
model = "deepseek-reasoner"

[llm.roles.executor]
provider = "deepseek"
model = "deepseek-chat"

[llm.roles.writer]
provider = "deepseek"
model = "deepseek-chat"

[llm.roles.reviewer]
provider = "deepseek"
model = "deepseek-reasoner"
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
default_model = "deepseek/deepseek-chat"
extra_headers = { "HTTP-Referer" = "http://localhost", "X-Title" = "SciForge" }
```

## 路由与降级策略

- `planner`：重推理任务（调研、创新点、实验设计）→ 建议 reasoner 类模型
- `executor`：工具调用密集（文献、远程执行）→ 建议稳定 function calling 的模型
- `writer`：长文写作 → 建议长上下文模型
- `reviewer`：审稿自检 → 建议强推理模型
- 任一角色可指向同一个 provider/model；成本敏感时把 writer/executor 换成便宜模型。

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
| 400 + temperature | `deepseek-reasoner` 不支持采样参数，SciForge 已自动忽略 |
| 429 | 限流：降低并发/换 key/给 provider 加 `timeout` |
| 工具调用乱码 | 模型 function calling 不稳定：换 executor 模型，或降低 `agent.max_steps` |
