# LLMClient

`LLMClient` 是同步调用 LLM 的统一入口：

```python
result = client.generate_json(system_prompt, user_prompt, OutputModel)
```

它读取 `config/llm.toml` 和根目录 `.env`，通过 OpenAI-compatible Chat Completions 调用配置的服务，再用调用方提供的 Pydantic 模型严格校验返回 JSON。它不修改业务 Prompt 或模型响应。

## 配置

`base_url`、`model`、`temperature`、`max_tokens` 和 `api_key_env` 为必填项。凭据从 `.env` 中指定的变量读取；进程环境变量不覆盖它。

默认配置接入 DeepSeek；[四种服务模板](../../../config/examples)同时提供 Ollama、SGLang 和 vLLM 示例。自部署模型名、端口、凭据及容器网络配置见[接入指南](../../../docs/llm-providers.md)。

`extra_body` 可省略。指定时只接受以下两种强类型结构之一：

- `thinking.type = "disabled"`：DeepSeek 专用。
- `chat_template_kwargs.enable_thinking = false` 或 `true`：支持此参数的 SGLang/vLLM 模型模板。

空表、混合两种结构或未知字段直接失败。组件不自动识别服务，也不透传任意参数。

## 调用行为

- 同步 SDK 调用，一次请求，`max_retries=0`。
- 固定请求 `response_format={"type":"json_object"}`。
- 仅发送配置中明确提供的供应商扩展字段。
- `max_tokens` 使用 TOML 中的值。
- 模型内容必须是符合 `OutputModel` 的 JSON。

缺文件、缺凭据、未知配置、越界参数、上游错误、空响应、非法 JSON 和 schema 不匹配都会直接失败。不重试、不去除思考标签、不修补 JSON、不自动切换模型或接口。未鉴权的本地模型服务仍需要用户在 `.env` 中显式提供 SDK 占位 key，例如 `LLM_API_KEY=not-required`。
