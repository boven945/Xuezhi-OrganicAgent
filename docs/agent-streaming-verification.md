# Agent 流式与结构化来源：实现与实测记录

本文记录 `backend-agent` 与 `backend-api` 两个模块在 2026-10-04 的联合改造：
把决策登记表 **I2**（`sources` 恒为空）与**I3**（非逐 token 流）一并解决。

- 实现分支：`feat/agent-streaming-and-sources`（从 `main` 切出）
- 基线 commit：`119f60f`
- **测试：全量 458 通过 / 36 按设计跳过**
  （agent 95 项、api 83 项；本次新增 29 项）

## 1. 为什么两件事合并做

I2 与 I3 都要求改同一个方法：`AgentLoop` 的问答循环。
分开做等于两次侵入同一个已验证模块（其 72 项测试），
而两处改动会互相影响——**流式改造改变了事件序列，
来源提取必须挂在同一次调用上才能拿到数据**。

## 2. 关键设计：run() 是 stream() 的消费者

```python
def run(self, question: str) -> dict[str, Any]:
    """执行一次完整问答（同步）。"""
    text, steps, invocations, sources = "", 0, [], []
    for event in self.stream(question):
        if event.get("type") == self.EVENT_DONE:
            ...
    return {...}
```

**这是本次最重要的决定。** 改造前的 `run()` 是独立的循环实现；
若新增 `stream()` 而保留旧的 `run()`，两条路径会各自演化——
教学系统里"同步能答、流式答不通"比慢更糟。

改造后：既有 72 项 Agent 测试**全部无需修改即通过**，
这是"行为等价"的最强证据。

### 一个必须注意的实现细节

改造过程中我一度**同时保留了两个 `run()`**（新实现 + 旧实现）。
Python 允许同名方法，**后定义者静默覆盖前者**——
若不核查，这会变成「提交了但跑的是旧代码」。

删除旧方法时还多删了 2 行，用 `grep -c "def run"` 确认只剩 1 个、
并逐个列出方法名核对完整性，才没留下隐患。

## 3. 逐 token 流：依赖与实测依据

### 3.1 上游支持（联网核实 + 容器内实测）

**华为 MaaS 支持流式**。官方文档
`support.huaweicloud.com/model-call-maas/model-call-101`
有"流式输出"示例，SSE 线格式为标准
`chat.completion.chunk`（含 `first_token_return_time` 等字段）。

依据：`llm/config.py` 记录的 MaaS 端点
`https://api.modelarts-maas.com/openai/v1` 支持 `stream: true`。

### 3.2 LangChain 侧的三条实测结论

在容器内实测（不是读文档推断）：

| 结论 | 实测方式 |
| --- | --- |
| `bind_tools` 后的 Runnable **同时**有 `.stream()` 与 `.invoke()` | 构造后 `hasattr` 检查 |
| `AIMessageChunk` 是 `AIMessage` 子类且实现 `__add__` | `type(...).__mro__` 检查 |
| 合并时会**自动按 `index` 归并** `tool_calls` 的 `arguments` 分片 | 手工构造两个分片相加，`.tool_calls` 直接给出解析好的 dict |

**第三条是好消息**：OpenAI 规范要求客户端自己按 `index` 聚合
`delta.tool_calls[].function.arguments` 分片，
但 LangChain 已经代劳了——**不需要手写聚合逻辑**。

因此 `ChatOpenAI` 只需加 `streaming=True`（构造参数），
流式与非流式共用同一条链。

### 3.3 一个必须防的陷阱

**只有半个 JSON 时访问 `.tool_calls` 不报错，而是返回 `args={}`。**

实测：
```python
a = AIMessageChunk(tool_call_chunks=[{'name':'X','args':'{"que', ...}])
a.tool_calls  # → [{'name':'X','args':{}, ...}]   ← 不报错！
```

若用"`tool_calls` 非空"判断"模型是否调用了工具"，
就会把**未完成的分片**误判为"调用了工具但参数为空"。

**规避**：`_merge_chunks` 只在**合并完整之后**才读 `.tool_calls`；
且 `delta` 事件只在本轮**无工具调用**时发出（见 §5）。

### 3.4 截断的 JSON 会解析失败

流式中途断连会留下不完整的 `arguments` 字符串：

```
json.loads('{"query": "酯化反')  # → JSONDecodeError
```

`_extract_tool_calls` 现有的形状校验会跳过这类项
（`args` 不是 dict 就给 `{}`），故不会崩溃。
测试 `test_no_delta_for_tool_call_turn` 等覆盖了相关路径。

## 4. 结构化来源（I2）

### 4.1 之前为什么拿不到

`search_knowledge` 工具**确实**返回含来源的 JSON，
但它以**字符串**形式放进 `ToolResult.content`，
经`to_model_payload()` 变成消息文本回填给模型——
`AgentLoop` 拿不到结构化数据，API 层的 `sources` 恒为空数组。

### 4.2 权威字段定义

**实测跑了一次 `search_knowledge`**（容器内，真实 chromadb + RDKit），
拿到确切形状：

```json
{
  "found": true,
  "note": "以下为知识库检索到的原文片段……",
  "passages": [
    {
      "source_id": "src-organic-001",
      "title": "有机化学自编讲义",
      "edition": "project-authored",
      "locator": "第三章烃的衍生物",
      "scope": "high_school_required",
      "text": "苯酚具有弱酸性……"
    }
  ],
  "retrieval_meta": {...}
}
```

权威定义见 `app/rag/models.py:200` 的 `RetrievalResult.to_model_payload`。
**注意方法名是 `to_model_payload` 不是 `to_json`**——
我第一版注释写错了，查 `dir()` 后改正。

### 4.3 解析时的三个安全约束

`_extract_sources` 的设计要点：

1. **只在工具成功时提取**。失败时 `content` 是
   `{"error": ..., "message": ...}`，从中提"来源"会得到伪造条目。
   测试 `test_no_source_event_on_tool_failure` 锁定。
2. **缺 `source_id` 的条目跳过**——无法溯源的条目没有展示价值。
3. **`text` 字段不进入来源**——原文已随模型答复传给学生，
   再单独发一遍会让 SSE 体积翻倍。

**解析失败静默返回空列表**：来源是附加信息，
解析不了不该让整个问答失败。原文仍在答复里。

### 4.4 顺带确认的三条语料准入规则

构造探针时连续撞到三条既有校验，说明语料准入设计得很严：

| 规则 | 实测报错 |
| --- | --- |
| 未审核内容不入生产索引 | `内容审核状态为「pending_review」，仅已批准的内容可进入生产索引` |
| 索引须绑定 embedding 模型 | `片段的 embedding 模型（probe）与当前存储配置不一致，须重建索引` |
| ChromaDB metadata 只接受标量 | `reviewed_at=None` 导致写入失败（须用空字符串） |

这三条不是本次改动引入的，但**值得记录**：它们让"随手造一条测试数据"
变得困难，写RAG 相关测试时必须知道。

## 5. delta 只在本轮无工具调用时发出

模型可能**先吐思考文本、再吐 `tool_calls`**。
若无条件转发 delta，学生会看到"让我先查一下…"这种思考过程，
以为那是答案。

**处理**：先缓存本轮的文本增量，
待合并完成、确认**本轮无工具调用**后才 yield。
测试 `test_no_delta_for_tool_call_turn` 锁定——
它验证第二轮的"答案"出现，而第一轮的思考文本不出现。

## 6. 降级策略：流式失败不让整个请求失败

```python
except Exception:
    logger.warning("流式调用失败，降级为同步: step=%d", step)
    message = None      # 随后走 invoke_with_tools
```

教学场景下「慢但有答案」远好于「快但报错」(`architecture.md` §6)。

三种降级路径各有测试：

| 情况 | 行为 | 测试 |
| --- | --- | --- |
| 客户端 `supports_streaming=False` | 直接走同步 | `test_unsupported_streaming_uses_sync` |
| 流式抛异常 | 降级重试同步 | `test_streaming_failure_falls_back_to_sync` |
| 上游不支持 `stream` | `LLMError` + `code=llm_streaming_unsupported` | 由客户端层测试覆盖 |

`LLMError` 用自定义 `code` 而**不新增异常类**——
`errors.py` 的类层次按"上游故障类型"划分，
"不支持流式"是本地配置状态，不是新的故障类别。

## 7. API 层：SSE 事件消费

### 7.1 事件序列（改造后）

```
meta     请求一开始就发（不触碰模型，故必然发出）
stage    阶段进展（thinking / continuing）
tool     **独立事件类型**（决策项 I2/I3）
delta    token 增量
source   知识来源，检索发生瞬间推送
result   最终答复，字段与同步接口一致
done     收尾
error    失败
```

### 7.2 一个被实测抓到的实现 bug

初版把 `tool` 事件**嵌在 `EVENT_STAGE` 分支里**：

```python
elif kind == AgentLoop.EVENT_STAGE:
    stage = str(event.get("stage", ""))
    if stage == "tool":   # ← 永远进不来
```

但 `AgentLoop` 发的是**独立的 `"type": "tool"` 事件**，
`event["stage"]` 根本不存在。结果：**两个工具调用一个都没出现在流里**。

**怎么发现的**：直接打印真实 SSE 线格式（而非读代码），
发现 `event: tool` 完全缺失。这是"真实端到端探针优于代码审阅"的又一例证。

已拆成独立分支，测试 `test_reports_actual_tool_invocations_only` 锁定。

### 7.3 `meta` 的 note 已更新

初版写着"当前为阶段事件流，非逐 token 流"，
改造后改为"逐 token 流已启用：delta 事件须追加拼接"。

## 8. API 层的 sources 映射

Agent 层给纯 dict，API 层映射到契约模型并**兜住超长字段**。

| 关注点 | 处理 | 原因 |
| --- | --- | --- |
| 超长字段 | 按契约 `max_length` 截断 | 否则 `ValidationError` 会让请求 500 |
| 缺 `title` | 填"未命名来源" | 来源是展示信息，不该让问答失败 |
| `locator_kind` | 文本形态推断（`p.42`/`第N页` → PAGE） | 判不准就落 `unknown`，不猜 |
| `review_status` | 恒 `"unknown"` | **不写 "approved"**——那是审核结论不是事实 |

测试 `test_overlong_source_fields_are_truncated` 与
`test_missing_source_fields_get_defaults` 覆盖。

## 9. 测试：新增 29 项

| 文件 | 项数 | 覆盖 |
| --- | --- | --- |
| `agent/test_streaming_sources.py` | 23 | chunk 合并、来源提取、流式事件、降级 |
| `api/test_streaming.py`（新增用例） | +2 | delta 转发、source 事件 |
| `api/test_app.py`（新增用例） | +4 | sources 映射、截断、默认值、页码判定 |

### 替身不合约引发的三处假失败

改造过程中，测试替身接连暴露问题——**每一次看起来都像生产 bug**：

| 现象 | 真实原因 |
| --- | --- |
| `TypeError: unsupported operand type(s) for +` | `_FakeChunk` 没实现 `__add__`（真实 `AIMessageChunk` 有） |
| `LLMError: 模型返回内容为空` | `_FakeChunk` 只有 `.text`，真实 `AIMessage` 同时有 `.content` |
| 6 项 API 测试 `AttributeError` | `_FakeLoop` 只有 `run()`，改造后 SSE 走 `stream()` |

**教训**：替身必须实现被替对象的**全部**入口与属性，
不能只实现"当前被测的那条路径"。否则接口一变，测试就以
误导性的方式失败——排查时会先怀疑生产代码。

还有一处：`Exception` 与 `NotImplementedError` 的区分。
替身用 `_NoChunks(Exception)` 表示"该轮没分片"，
若误继承 `NotImplementedError`，实现里单独捕获
`NotImplementedError` 的分支会把它当成"客户端不支持流式"——
语义不同，会走错降级路径。

## 10. 未验证的部分

**本轮全部用替身测试，未发真实模型请求。**
故以下结论**仍是"依据文档与实测机制"而非"端到端实测"**：

- 华为 MaaS 的 `stream:true` 在 openpangu-2.0-flash 上的实际行为
  （首 token 延迟、chunk 数量、中文是否被转义）
- 流式下 `tool_calls` 分片的**实际**分片方式
  （官方文档描述已确认，但 openPangu 的实现是否一致未验）
- 长答复下 `max_completion_tokens=2048` 是否够用

**须在演示机实测**，方法见
`docs/interface-contract-verification.md` §9 的启动命令：
设 `MAAS_API_KEY` 后用 `curl -N` 打 `/api/v1/ask/stream`，
观察 `delta` 事件是否逐字出现。

## 11. 复现命令

```bash
export https_proxy=http://127.0.0.1:7897 http_proxy=http://127.0.0.1:7897
docker build -t xuezhi-chem-test -f backend/tests/Dockerfile.test .
docker run --rm xuezhi-chem-test# 全量

# 只看流式与来源
docker run --rm xuezhi-chem-test \
  python -m pytest backend/tests/agent/test_streaming_sources.py -v

# 真实流式（需密钥）
docker run --rm -p 8000:8000 -e MAAS_API_KEY=... xuezhi-chem-test \
  uvicorn app.api.app:app --host 0.0.0.0 --port 8000 --app-dir backend
# 另开终端：
curl -N -X POST http://127.0.0.1:8000/api/v1/ask/stream \
  -H 'Content-Type: application/json' \
  -d '{"question":"苯酚的酸性为何比碳酸弱"}'
```
