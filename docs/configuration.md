# 配置

服务严格读取仓库根目录 `.env`、`config/service.toml`、`config/llm.toml` 和 `config/embedding.toml`。缺文件、缺 key、未知 TOML 字段或类型错误直接失败。三个加载器通过 `dotenv_values` 读取文件；只向进程注入同名环境变量不能替代 `.env` 文件。修改配置后重启服务。

## .env

| 名称 | 用途 | 默认值 |
| --- | --- | --- |
| `LIMEM_ADMIN_API_KEY` | 管理员凭据，自行生成的非空随机值 | 无，必填 |
| `DEEPSEEK_API_KEY` | 默认 DeepSeek 配置的凭据 | 使用 DeepSeek 模板时必填 |
| `LLM_API_KEY` | 自部署 LLM 模板的凭据 | 使用自部署模板时必填；无鉴权时显式填写 `not-required` |
| `DASHSCOPE_API_KEY` | embedding 凭据，由 `embedding.api_key_env` 指定变量名 | 无，必填 |
| `LIMEM_BIND_HOST` | Compose 主机绑定地址 | `127.0.0.1` |
| `LIMEM_PORT` | Compose 主机端口 | `8012` |

后两项由 Compose 展开，不属于 Python 服务配置。直接运行 Python 时，进程环境变量 `HOST`、`PORT` 控制监听地址和端口，默认为 `0.0.0.0`、`8000`。将它们写入 `.env` 不会自动导入进程。Dockerfile 固定容器内 `HOST=0.0.0.0`、`PORT=8000`。

## config/llm.toml

| 字段 | 当前值 | 约束与含义 |
| --- | --- | --- |
| `llm.api_key_env` | `DEEPSEEK_API_KEY` | `.env` 中的凭据变量名 |
| `llm.base_url` | `https://api.deepseek.com` | 有效 HTTP(S) URL，OpenAI compatible API |
| `llm.model` | `deepseek-v4-flash` | 非空模型名 |
| `llm.temperature` | `0.0` | 0 到 2 |
| `llm.max_tokens` | `16384` | 正整数，最大输出 token 数 |
| `llm.extra_body` | DeepSeek thinking 配置 | 可省略；省略时不发送任何供应商扩展字段 |
| `llm.extra_body.thinking.type` | `disabled` | DeepSeek 专用，只接受此值 |
| `llm.extra_body.chat_template_kwargs.enable_thinking` | 未设置 | 布尔值；仅用于支持此模板参数的 SGLang/vLLM 模型 |

LLMClient 使用同一条 OpenAI-compatible Chat Completions 请求通路。`base_url` 填 API 根地址，自部署服务通常需要 `/v1`；不要填写 `/chat/completions`。`model` 必须匹配服务端模型名。

`extra_body` 只接受两种互斥结构：DeepSeek 的 `thinking`，或模型模板的 `chat_template_kwargs`。后者只允许 `enable_thinking`。空扩展表、混用两种结构及未知字段均报错；不提供任意参数透传。Ollama 模板省略此项。

所有服务固定请求 `response_format={"type":"json_object"}`，并用 Pydantic 严格解析结果；SDK 自动重试为 0。服务端及所选模型必须支持 JSON 输出，返回的 `message.content` 必须是符合业务 schema 的 JSON。LiMem 不去除思考标签、不修复响应、不自动切换服务或输出模式。

本仓库不启动或下载模型。四种服务的模板、凭据和 Docker 网络说明见[LLM 服务接入](llm-providers.md)。

## config/embedding.toml

| 字段 | 当前值 | 约束与含义 |
| --- | --- | --- |
| `embedding.api_key_env` | `DASHSCOPE_API_KEY` | `.env` 中的凭据变量名 |
| `embedding.model` | `qwen3.7-text-embedding` | DashScope 文本 embedding 模型 |
| `embedding.dimension` | `1024` | 正整数，要求返回维数一致 |
| `embedding.batch_size` | `20` | 1 到 20 |

embedding 使用 DashScope SDK，不读取 LLM 的 base_url。服务会持久化向量。已有记忆库切换 embedding 模型或维度的迁移路径未实现；不要把新旧向量视为可比较的数据。

## config/service.toml

| 字段 | 当前值 | 约束与含义 |
| --- | --- | --- |
| `service.data_dir` | `var` | SQLite 与原始数据目录；相对仓库根目录 |
| `service.log_path` | `var/logs/service.log` | 日志文件；相对仓库根目录 |
| `service.cleanup_interval_seconds` | `3600` | 正整数，过期数据清理周期 |
| `retrieval.seed_limit` | `5` | 正整数，初始召回数量 |
| `retrieval.neighbor_limit` | `5` | 正整数，邻接扩展数量限制 |
| `retrieval.rrf_k` | `60` | 非负整数，RRF 排名融合常数 |
| `retrieval.bm25_weight` | `3.0` | 正数，BM25 路权重 |
| `retrieval.entity_match_weight` | `0.1` | 非负数，实体匹配加分权重 |
| `retrieval.evidence_support_weight` | `0.25` | 非负数，Evidence 支持加分权重 |

路径也接受绝对路径。容器默认只持久化 `/app/var`；修改路径时需同步调整 Compose 挂载。算法参数的完整定义见[检索设计](../memory-bank-design/design/05-retrieval.md)。请求方不能通过 API 修改这些内部参数。

## 离线行为 Agent

`QwenClient(base_url)` 单独接收服务地址；冻结模型名为 `Qwen3-1.7B`，temperature=0、seed=0、非思考、JSON Schema 输出。其逐步输入与输出预算由算法调用固定。它不读取以上三个 TOML 文件，也不进入 HTTP 服务主路径。详见[模块说明](../src/limem/consolidator/behavior_agent/README.md)。
