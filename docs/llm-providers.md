# 接入 LLM 服务

LiMem 连接用户提供的 OpenAI-compatible Chat Completions 端口。默认接入 DeepSeek，也可使用自行部署的 Ollama、SGLang 和 vLLM。模型安装、加载和服务启动由用户完成。

## 选择配置

将对应模板复制到 `config/llm.toml`，修改地址和模型名，再重启 LiMem：

```bash
# 四选一
cp config/examples/llm-deepseek.toml config/llm.toml
# cp config/examples/llm-ollama.toml config/llm.toml
# cp config/examples/llm-sglang.toml config/llm.toml
# cp config/examples/llm-vllm.toml config/llm.toml
```

| 服务 | 模板中的 API 根地址 | 模型名示例 | .env 凭据变量 |
| --- | --- | --- | --- |
| DeepSeek | `https://api.deepseek.com` | `deepseek-v4-flash` | `DEEPSEEK_API_KEY` |
| Ollama | `http://127.0.0.1:11434/v1` | `qwen3:8b` | `LLM_API_KEY` |
| SGLang | `http://127.0.0.1:30000/v1` | `Qwen/Qwen3-8B` | `LLM_API_KEY` |
| vLLM | `http://127.0.0.1:8000/v1` | `Qwen/Qwen3-8B` | `LLM_API_KEY` |

地址和模型名只是示例。以部署时设置的端口及 `/v1/models` 返回的模型 ID 为准。`base_url` 不包含 `/chat/completions`。输出预算 `max_tokens` 应符合所选模型的上下文和输出限制。

`api_key_env` 指定从 `.env` 读取哪一个变量。使用自部署模板时，DeepSeek key 可以留空。无服务端鉴权时显式填写 `LLM_API_KEY=not-required`；SDK 仍会发送这个占位值，服务端需允许无鉴权请求。启用鉴权后必须填写真正的服务端 key。Ollama 本地 OpenAI-compatible API 忽略该占位凭据。

LiMem 自身的 `LIMEM_ADMIN_API_KEY` 与模型服务的 key 独立。embedding 当前仍使用 DashScope；切换 LLM 不改变 embedding 服务。

## Docker 如何访问模型端口

| 模型部署位置 | LiMem 中的地址示例 |
| --- | --- |
| LiMem 直接运行在同一主机 | `http://127.0.0.1:11434/v1` |
| LiMem 在 Docker，模型在宿主机 | `http://host.docker.internal:11434/v1` |
| 模型在另一台服务器 | `http://模型服务器地址:端口/v1` |
| 两个容器在同一个 Docker 网络 | `http://模型服务名:容器端口/v1` |

Compose 已添加 `host.docker.internal:host-gateway`。它只提供宿主机地址解析，不会改变模型服务的监听地址。Linux 上仅绑定 `127.0.0.1` 的模型端口通常无法通过宿主机网关访问，需要绑定容器可达的接口，并允许 Docker 网段访问该端口。宿主机自身能访问不代表容器也能访问。

例如 Ollama 可以在其运行环境中设置 `OLLAMA_HOST=0.0.0.0:11434` 后重启；SGLang/vLLM 可在服务启动时配置 `--host 0.0.0.0`。按实际网络配置访问范围。不要在 LiMem 容器配置中用 `localhost` 指向宿主机。

修改 `config/llm.toml` 或 `.env` 后：

```bash
docker compose restart limem
```

从旧版本升级本次代码与 Compose 配置时，执行 `docker compose up -d --build --wait`。

## JSON 输出与思考参数

LiMem 对四种服务都发送 `response_format={"type":"json_object"}`，并严格校验返回 JSON。模型与服务版本必须支持此模式。只有 URL 可访问，不足以证明模型能够完成记忆抽取。

DeepSeek 模板显式关闭思考：

```toml
[llm.extra_body.thinking]
type = "disabled"
```

自部署模板默认不发送任何扩展字段。若 SGLang/vLLM 所加载的模型模板支持 `enable_thinking`，可以显式追加：

```toml
[llm.extra_body.chat_template_kwargs]
enable_thinking = false
```

两种扩展不能同时配置。不要把 DeepSeek 的 `thinking` 参数带到其他服务。Ollama 模板不使用这些扩展；思考行为需按其模型和版本在服务端配置。即使配置关闭思考，仍需实际验证所选模型返回符合 schema 的 JSON。

LiMem 不自动探测供应商，不在失败后重试、切换模型或修补 JSON。当前离线行为 Agent 的独立 `QwenClient` 不经过这个配置入口。

## 检查连接

在能访问模型端口的环境中执行；`MODEL_API_URL` 和 `MODEL_API_KEY` 是下列 shell 命令使用的临时变量，不是 LiMem 配置项：

```bash
export MODEL_API_URL=http://127.0.0.1:11434/v1
export MODEL_API_KEY=not-required
curl --fail-with-body "$MODEL_API_URL/models" \
  -H "Authorization: Bearer $MODEL_API_KEY"
```

确认模型 ID 后，将它填写到 `config/llm.toml`。再通过 LiMem 写入一个符合车辆协议的完整 Session，验证模型抽取和 embedding 链路。`/health` 只验证 LiMem 进程。

本次实现通过固定响应的本地 HTTP/SDK 合同验证：配置地址、模型名、凭据、扩展字段、JSON 校验和不重试行为。未对四种真实模型服务分别开展端到端效果评测。本次验证主机上，容器到宿主机模拟端口的 TCP 连接超时，尽管名称解析及宿主机自身访问正常；该网络路径未通过验证，具体网络阻断原因未确认。

## 接口依据

- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
- [SGLang OpenAI-compatible APIs](https://docs.sglang.io/docs/basic_usage/openai_api)
- [SGLang OpenAI 请求 schema](https://github.com/sgl-project/sglang/blob/main/python/sglang/srt/entrypoints/openai/protocol.py)
- [vLLM OpenAI-compatible server](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/)
- [vLLM Chat Completions 请求 schema](https://github.com/vllm-project/vllm/blob/main/vllm/entrypoints/openai/chat_completion/protocol.py)
