# LLMClient

`LLMClient` 是项目内同步调用 LLM 的统一入口。当前只实现一次 OpenAI 格式的 JSON 调用：

```python
result = client.generate_json(system_prompt, user_prompt, OutputModel)
```

它负责读取配置、调用 Chat Completions，并用调用方提供的 Pydantic 模型校验返回内容。它不负责业务 Prompt，也不修改模型返回的 JSON。

## 配置

普通配置固定读取仓库根目录下的 `config/llm.toml`：

```toml
[llm]
api_key_env = "DEEPSEEK_API_KEY"
base_url = "https://api.deepseek.com"
model = "deepseek-v4-flash"
temperature = 0.0
max_tokens = 16384

[llm.extra_body.thinking]
type = "disabled"
```

API Key 固定放在仓库根目录的 `.env`：

```dotenv
DEEPSEEK_API_KEY=<your-api-key>
```

`.env` 已由 `.gitignore` 排除。代码和文档不能保存实际 Key。模型、Base URL、温度和输出上限只读取 TOML，不接受环境变量覆盖。

## 调用行为

当前请求固定使用：

- OpenAI Python SDK 的同步 Chat Completions。
- SDK 自动重试关闭；一次调用只发送一次请求。
- `response_format={"type": "json_object"}`。
- `extra_body={"thinking": {"type": "disabled"}}`。
- `max_tokens=16384`，只限制输出长度。

配置文件缺失、Key 缺失、多余配置字段、越界参数、空响应、非法 JSON 和 schema 不匹配都会直接报错。Key 只读取根目录 `.env`，已有进程环境不能覆盖它。组件不重试，不修补 JSON，也不切换模型或接口。
