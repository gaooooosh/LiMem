# 文档索引

## 使用与部署

- [部署](deployment.md)
- [LLM 服务接入](llm-providers.md)
- [完整配置](configuration.md)
- [算法与服务边界](architecture.md)
- [版本来源与验证边界](distribution.md)

## HTTP API

- [LiMem Agent Memory Service](service/README.md)
- [鉴权](service/authentication.md)
- [记忆巩固](service/consolidations.md)
- [错误处理](service/errors.md)
- [记忆库](service/memory-banks.md)
- [vehicle_session_v1](service/session-formats/vehicle-session-v1.md)

HTTP 使用指南内的 `/guide/*` 链接用于正在运行的服务；离线阅读可用上面的文件链接。

## 领域与设计

- [领域术语](../CONTEXT.md)
- [记忆库当前设计](../memory-bank-design/design/00-decisions.md)
- [L2 语义层 Schema](../memory-bank-design/design/01-l2-schema.md)
- [情境与条件](../memory-bank-design/design/02-situation-condition-model.md)
- [L2 抽取与写入](../memory-bank-design/design/03-extraction-write.md)
- [记忆巩固](../memory-bank-design/design/04-consolidation.md)
- [检索设计边界](../memory-bank-design/design/05-retrieval.md)
- [L0/L1 原始与分组层 Schema](../memory-bank-design/design/06-l0-l1-schema.md)
- [实体设计](../memory-bank-design/design/07-entity.md)
- [L2 持久化](../memory-bank-design/design/08-memory-store.md)
- [黄金样例一：车舱记忆最小通路](../memory-bank-design/design/examples/01-car-cabin-lifecycle.md)
- [黄金样例二：检索接口验证 Case](../memory-bank-design/design/examples/02-retrieval-cases.md)

## 组件

- [Consolidator](../src/limem/consolidator/README.md)
- [行为偏好Agent](../src/limem/consolidator/behavior_agent/README.md)
- [EmbeddingClient](../src/limem/embedding_client/README.md)
- [EpisodeBuilder](../src/limem/episode_builder/README.md)
- [LLMClient](../src/limem/llm_client/README.md)
- [MemoryItemExtractor](../src/limem/memory_item_extractor/README.md)
- [MemoryStore](../src/limem/memory_store/README.md)
- [RawDataParser](../src/limem/raw_data_parser/README.md)
- [Resolver](../src/limem/resolver/README.md)
- [Retriever](../src/limem/retrieval/README.md)
- [Service](../src/limem/service/README.md)
