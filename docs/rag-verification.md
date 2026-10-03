# 知识检索层实现与验证状态

本文记录 `backend-rag` 模块的实现范围、**关键实测发现**与验证结果。

- 分支：`feat/backend-rag-knowledge-retrieval`
- 模块标识：`backend-rag`（见 `docs/development-workflow.md` §2）
- 基线：Python 3.12 + chromadb 1.5.9

## 1. 实现范围

| 文件 | 职责 |
| --- | --- |
| `backend/app/rag/__init__.py` | 模块导出与职责边界 |
| `backend/app/rag/errors.py` | 受控错误类型（5 类） |
| `backend/app/rag/models.py` | 来源元数据、片段、检索结果、审核状态与课程范围枚举 |
| `backend/app/rag/store.py` | 向量存储、准入校验、检索与阈值处理 |
| `backend/tests/rag/test_store.py` | 44 个用例（含真实 chromadb 集成） |

## 2. ⚠️ 关键实测发现：chromadb 内置 embedding **对中文无效**

这是本模块最重要的发现，直接决定生产部署方案。

### 2.1 内置 DefaultEmbeddingFunction 的两个问题

chromadb 1.5.9 内置 `DefaultEmbeddingFunction`（基于 ONNX 运行时），
但实测发现：

**问题一：首次调用需联网下载 79.3 MB 模型**

```
/root/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx.tar.gz: 79.3M
```

模型缓存于 `~/.cache/chroma/onnx_models/`。**断网环境会直接失败**——
这与 `deployment-operations.md` §5「断网演示」的要求冲突。

**问题二：该模型是英文模型，中文检索排序完全错误**

实测（cosine 空间，查询"酯化反应"）：

| 文档内容 | 距离 | 应当排名 |
| --- | --- | --- |
| 酯化反应是酸和醇生成酯和水 | **0.8358** | 第 1（最相关） |
| 乙醇的分子式是 C2H6O | 0.6063 | 第 2 |
| 今天天气很好 | **0.4943** | 第 3（最不相关） |

**目标片段反而距离最远，排序完全颠倒。**

同一测试改用英文查询 `esterification reaction`，排序完全正确：

| 文档内容 | 距离 | 排名 |
| --- | --- | --- |
| Esterification is the reaction of an acid and an alcohol... | **0.1664** | 1 ✓ |
| Ethanol molecular formula is C2H6O | 0.6059 | 2 ✓ |
| The weather is nice today | 0.9921 | 3 ✓ |

**结论：`all-MiniLM-L6-v2` 是英文模型。中文知识库必须显式提供中文/多语言
embedding 模型，不可依赖 chromadb 默认 EF。**

> 该结论修正了 `docs/h1-h3-verification.md` §2.3 中"chromadb 基础依赖不含
> embedding 模型"的表述——更准确的说法是：不含 Python 依赖，但**内置了
> ONNX 运行时并会自动下载英文模型**。

### 2.2 距离度量默认是 l2，不是余弦

实测 `col.configuration['hnsw']['space']` 为 `'l2'`（欧氏距离）。
阈值语义随度量变化（余弦距离 0 最优、l2 越小越优但量纲不同）。

本层新建集合时**显式设为 `cosine`**，使距离落在 `[0, 2]`（0 为完全相同），
便于阈值解释与实测。测试断言该配置生效。

### 2.3 集合名有格式约束

```
InvalidArgumentError: name: Expected a name containing 3-512 characters
from [a-zA-Z0-9._-], starting and ending with a character in [a-zA-Z0-9]
```

即：3–512 字符、仅含字母数字与 `._-`、首尾须为字母数字。
测试中曾用 `t1`（2 字符）失败，已记入测试用例。

## 3. 落实的文档约束

| 约束来源 | 实现方式 |
| --- | --- |
| `knowledge-base.md` §2：无来源/授权不明内容不得入索引 | `KnowledgeSource.missing_required_fields()` 校验 6 个必填字段（source_id/title/publisher/edition/topic/reviewer），缺失即拒绝 |
| `knowledge-base.md` §3：仅已批准内容进入生产索引 | `VerificationStatus.is_retrievable` 仅 `APPROVED` 为真；草稿/待审/已弃用/已撤回全部拒绝 |
| `knowledge-base.md` §4：超纲内容须过滤 | `ScopeLevel.is_in_scope` 排除 `UNDERGRADUATE`；高中拓展内容允许但带标记 |
| `knowledge-base.md` §3：变更 embedding 模型须重建索引 | 入库时校验片段声明的模型与存储配置一致；同批次混用多个模型直接拒绝 |
| `knowledge-base.md` §3：不得在未记录模型变化时复用旧向量 | `KnowledgeChunk.embedding_model` 为**必填**，缺失即拒绝入库 |
| `knowledge-base.md` §5：结果须携带来源信息 | 每条结果含 source_id/title/publisher/edition/locator/reviewer/status/scope/content_hash |
| `knowledge-base.md` §5：无来源时不伪造 | `RetrievalResult.to_model_payload()` 在无结果时明确告知模型"未找到"，并要求其标注这不是检索结果 |
| `knowledge-base.md` §5：阈值须实测确定，**不使用未经评估的固定阈值** | `query()` 的 `threshold` 参数**无默认值**（默认 `None` 表示不过滤），必须由调用方按标注问答集传入 |
| `knowledge-base.md` §7：索引须关联版本与模型标识 | 集合 metadata 记录 `index_version` 与 `embedding_model` |
| `architecture.md` §5：模型推断与检索事实分离 | `RetrievalResult.source = "retrieval"`，不含任何模型生成内容 |
| `architecture.md` §6：嵌入不可用须明确报错 | 捕获后转 `EmbeddingUnavailableError`（可重试），**不降级为"无检索"并伪装正常** |
| `architecture.md` §6：独立失败路径 | 写入与查询失败分别包装为 `RetrievalError` |

### 错误分类

| 错误码 | 触发条件 | 可重试 |
| --- | --- | --- |
| `rag_invalid_document` | 片段不合规（元数据缺失/未批准/超纲/无 embedding 模型标识） | 否 |
| `rag_embedding_unavailable` | 嵌入模型不可用或返回数量不匹配 | 是 |
| `rag_index_not_ready` | 索引不可用（依赖缺失、目录不可写） | 否 |
| `rag_retrieval_failed` | 写入或查询执行失败 | 是 |

## 4. 验证结果：44/44 通过（含真实 chromadb）

```
$ docker run --rm xuezhi-chem-test python -m pytest backend/tests/rag/
44 passed in 0.69s
```

全量：**180/180 通过**（chem 42 + llm 49 + agent 45 + rag 44）。

### 测试策略说明

使用 `HashEmbedding`（确定性哈希嵌入）而非真实语义模型，理由：

1. 不依赖网络与模型下载，可离线可复现；
2. **真实语义模型的召回质量评估属 `knowledge-base.md` §6 范畴**，
   须由化学教师用标注问答集评估，不在单元测试内断言；
3. 本测试聚焦**治理机制**——准入校验、来源可追溯、阈值行为、
   embedding 模型一致性——这些与嵌入语义无关。

`HashEmbedding` 在代码中明确标注"仅用于测试与离线自检，不是语义嵌入"，
避免被误用于生产。

### 覆盖范围

- **准入校验**：6 个必填元数据字段逐一验证、4 种未批准状态、超纲拒绝、
  拓展内容允许、空文本、空 embedding 模型标识
- **批次一致性**：不合规批次**整批不写入**（避免索引不一致）
- **模型一致性**：模型不匹配、批次内混用模型均被拒
- **索引配置**：断言 `cosine` 空间与 metadata 中的版本/模型标识
- **元数据往返**：写入→检索后 9 个来源字段完整带回
- **阈值行为**：高于/低于阈值的过滤差异、`threshold_not_met` 原因记录
- **失败隔离**：写入失败、查询失败、嵌入崩溃、向量数量不匹配、索引不可用
- **模型载荷**：有结果时带完整来源；无结果时明确要求模型标注
- **真实 chromadb 集成**：完整 add→query 往返、集合名约束

## 5. 测试暴露的缺陷

| # | 缺陷 | 发现方式 | 修复 |
| --- | --- | --- | --- |
| 1 | 两处测试片段的 `embedding_model` 与替身 `model_id` 不匹配 | 先被模型一致性校验拦下，未到目标断言 | 测试数据对齐为同一标识 |
| 2 | `backend/tests/rag/` 缺 `__init__.py` | pytest 报 "no tests ran" | 补上包初始化文件 |

第 1 项说明**模型一致性校验确实在生效**——它拦下了测试数据的不一致，
这正是 `knowledge-base.md` §3 要求的机制。

## 6. 尚未实现（属后续）

| 能力 | 归属模块 | 说明 |
| --- | --- | --- |
| **中文 embedding 模型接入** | `backend-rag` | **当前最高优先级**：见 §2.1，内置 EF 中文不可用 |
| 文档切分器 | `knowledge-data` | `knowledge-base.md` §3 要求按语义单元切分，不在关键条件中间截断 |
| 索引构建流水线 | `knowledge-data` | §3 的 8 步流程（登记→提取→清理→切分→附加元数据→审核→建索引→发布） |
| 检索评估 | `knowledge-data` | §6 的 Recall@k、来源命中率等，须用版本化测试集 |
| Agent 工具封装 | `backend-agent` | 把 `query` 包成 Agent 工具，让模型可调用检索 |
| ChromaDB 部署形态 | 待 A6 决策 | 持久化卷、备份周期、并发访问策略 |

## 7. 复核方式

```bash
# 单元测试
docker run --rm xuezhi-chem-test python -m pytest backend/tests/rag/ -v

# 核实 chromadb 内置 EF 对中文的表现（重要）
docker run --rm xuezhi-chem-test python -c "
import warnings; warnings.filterwarnings('ignore')
import chromadb, tempfile
c = chromadb.PersistentClient(path=tempfile.mkdtemp(),
    settings=chromadb.config.Settings(anonymized_telemetry=False))
col = c.get_or_create_collection(name='zh_probe')
col.add(ids=['a','b'], documents=['酯化反应是酸和醇生成酯和水','今天天气很好'])
r = col.query(query_texts=['酯化反应'], n_results=2)
for d, dist in zip(r['documents'][0], r['distances'][0]):
    print(dist, d)
"
```
