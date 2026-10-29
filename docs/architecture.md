# 算法与服务边界

## 写入

`RawDataParser` 严格解析完整车辆 Session，保留来源顺序及原始记录。`EpisodeBuilder` 将一个 Session 组织成一个 Episode，并固定必须覆盖的 Required Action。

车辆状态池只为抽取构造差分 Context 视图。事件、动作和 Required Action 不变。HTTP 写入通过 `create_workflow1_extractor` 执行 v4：G生成主张；T重构空目标；C/N筛选实体目标及提及；B在无Required Action时用CPU BERT决定保留或清空；I执行身份边界。程序使用完整Episode校验引用、拼接context并复核必保覆盖。实体解析完成后生成embedding，在同一事务中保存原始Session、MemoryItem、Evidence、词法索引和向量。详见[Workflow1](workflow1/README.md)。

例如驾驶员在一次 Session 中两次调节座椅通风，来源动作分别保留；Episode 不是独立发生次数。一次动作附近的天气可进入 Evidence.context，不能自动成为 MemoryItem.condition。

## 检索

`QueryResolver` 将明确名称链接到已有 Entity。`Retriever` 融合 BM25 与 embedding 初始召回，再做两轮 Entity 邻接并排序诊断路径。它不调用 LLM，也不判断路径是否足以回答问题。

`RetrievalResultFormatter` 输出 Agent 可读取的 Markdown；每条主张附带按 query 优先 BM25 选择的最多三条 Direct Evidence 完整历史背景。历史背景不表示当前环境。详见[检索设计](../memory-bank-design/design/05-retrieval.md)。

## 巩固

HTTP 服务调用 `RepeatedBehaviorConsolidator`，从同一主体的 Evidence 发现固定动作支持组，再归纳条件。条件事实及完整 AND 条件都必须得到固定组超过一半 Evidence 支持。结果保存为 candidate 及可追溯的 Consolidation Support。

离线行为 Agent 保存另一个已迁入源码的归纳版本，当前未接入 HTTP pipeline，不写入 MemoryStore；本仓库保留其源码与使用边界，不附带离线评测输入。

## 服务

一把用户 API key 对应独立身份；每个 Memory Bank 使用独立 SQLite 文件。管理员只在合同允许的接口跨库操作。同库操作使用进程内锁，服务固定单 worker。临时库 24 小时失效、48 小时清理，持久库不按时间过期。

自动巩固由接口显式启动，运行状态位于进程内；它不是后台持续学习循环。Profile 由调用方维护，不参与检索。完整约束由领域术语、当前决策、组件说明与 HTTP schema 共同说明。
