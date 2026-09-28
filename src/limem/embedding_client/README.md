# EmbeddingClient

`EmbeddingClient` 是项目内同步生成文本向量的统一入口。它只公开两个方法：

```python
from limem.embedding_client import EmbeddingClient

client = EmbeddingClient()
document_vectors = client.embed_documents(["座椅偏硬", "用户喜欢低风量"])
query_vector = client.embed_query("用户对座椅有什么偏好？")
```

```text
embed_documents(list[str]) -> list[list[float]]
embed_query(str) -> list[float]
```

`embed_documents()` 使用 `text_type="document"`。`embed_query()` 使用
`text_type="query"`。文档按配置的 `batch_size` 确定性分批，返回结果按
DashScope 的 `text_index` 恢复为输入顺序。空文档列表直接返回空列表。

成功响应中的每一项必须包含 `type="text"`、`text_index` 和指定维度的
`embedding`。这些字段按真实 `qwen3.7-text-embedding` 响应严格校验。

## 配置

普通配置固定读取仓库根目录的 `config/embedding.toml`：

```toml
[embedding]
api_key_env = "DASHSCOPE_API_KEY"
model = "qwen3.7-text-embedding"
dimension = 1024
batch_size = 20
```

API Key 固定放在仓库根目录的 `.env`：

```dotenv
DASHSCOPE_API_KEY=<your-api-key>
```

`batch_size` 必须在 1 到 20 之间。模型和维度由配置显式指定。

## 失败行为

模块通过同步 `dashscope.TextEmbedding.call()` 调用默认 DashScope 地址。
任一批返回非 200 状态、非法结构、缺失、重复或越界的 `text_index`，或者
向量维度与配置不一致时，整个调用直接失败。模块不重试、不切换模型，也不
返回已经完成的部分结果。
