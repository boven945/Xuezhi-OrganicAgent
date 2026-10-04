# 接口契约实现与实测记录

本文记录 `backend-api` 模块的实现结果与**实测数据**。凡标注「实测」的
结论均来自容器内真实运行，非依据文档推断或训练记忆。

- 实现分支：`feat/backend-api-fastapi-gateway`（从 `main` 切出，独立提交）
- 基线 commit：`73a5a5e`
- **测试：全量 429 通过 / 36 按设计跳过，其中 API 模块 77 项**
  （test_app 32、test_errors_ratelimit 26、test_streaming 12、test_e2e_http 7）
- 依赖版本（实测解析）：fastapi 0.142.2 / uvicorn 0.54.0 /
  pydantic 2.13.5 / starlette 1.7.0 / httpx2 2.13.1

## 1. 本模块解决了什么

`interface-contract.md` §2 定义了5 个交互操作，但此前**没有任何
HTTP 实现**——外部无法调用已验证的问答链路。本模块落地其中4 个：

| 交互操作（§2） | 实现 | 状态 |
| --- | --- | --- |
| 提交教学问题 | `POST /api/v1/ask` | 已实现（同步） |
| 提交教学问题（流式） | `POST /api/v1/ask/stream` | 已实现（SSE 阶段事件） |
| 获取教学答复 | 上述两者的响应体 | 已实现 |
| 执行工具调用 | —— | **未实现**（走MCP，见 §6） |
| 请求语音/数字人 | —— | 未实现（依赖 backend-speech） |
| 取消或结束请求 | —— | 未实现（见 §6） |

另加两个运维接口与一个确定性工具接口：

| 接口 | 用途|
| --- | --- |
| `GET /health` | 健康检查，**恒返回 200**，附组件明细 |
| `GET /ready` | 就绪探针，未就绪返回 503（k8s 约定） |
| `POST /api/v1/molecule` | SMILES 解析，**不需要模型**，毫秒级 |

## 2. 传输方式的决策依据（A3）

`architecture.md` §3 与决策登记表 A3 要求在实现前定传输方式。
本模块的选择与依据：

**同步 `POST /api/v1/ask` 与流式 `POST /api/v1/ask/stream` 并存。**

实测依据：openPangu 短请求中位延迟 **7.10 秒**（见
`llm-adapter-verification.md`）。7 秒同步会让前端长时间转圈且无法取消。

但**流式不能是唯一方案**，理由是实测出的机制约束：
SSE 响应头一旦发出就无法再改HTTP 状态码。实测确认——流式接口
在 Agent 抛错时返回的仍是 **HTTP 200**，错误只能塞进事件流：

```
event: error
data: {"code": "llm_upstream_unavailable", "retryable": true, ...}
```

这带来两个后果：
1. 前端处理复杂度上升（须解析事件流才能知道成败）；
2. **错误码而非 HTTP 状态码才是跨传输稳定的标识**——
   这直接决定了 `errors.py` 的设计（下层20 个错误码原样透传）。

## 3. 逐 token 流：已于 2026-10-04 实现

> **本节描述的是实现前的状态，现已过时。**
> 逐 token 流与结构化来源（`sources`）已于 2026-10-04 实现，
> 实测记录见 [`agent-streaming-verification.md`](agent-streaming-verification.md)。
> 以下保留为决策依据。

**当时的判断：SSE 是「阶段事件」流，不是逐 token 流。**

原因在既有代码：`:class:`app.agent.dispatcher.AgentLoop` 是
**同步迭代**——它在 `run()` 内部跑完整个「模型→工具→回填→再模型」
循环后才返回，中间状态不外露。API 层拿不到 token。

真正的 token 级流式需要把 `AgentLoop.run` 改造成生成器，
属对已验证模块的侵入式改动（须重跑其 72 项测试），
**已登记为决策项 A3-follow，不在本次范围**。

当前流式仍有真实价值，且这些行为**已实测**：

| 事件 | 时机 | 作用 |
| --- | --- | --- |
| `meta` | **第一个yield**，不触碰任何模型 | 前端立即渲染加载态 |
| `stage`（thinking） | 调用模型前 | 阶段提示 |
| `stage`（tool） | 每次真实工具调用后 | 展示依据来源 |
| `result` | 完成后 | 完整答复，字段与同步接口一致 |
| `done` | 收尾 | 关闭流 |
| `error` | 失败 | 结构化错误 |

`meta` 首发的意义：若首个事件要等模型跑完才来，用户会看到空白页。
实测 `meta` 不触碰模型与向量库，故**无论后端是否可用都能发出**。

## 4. 实测发现的框架行为（与文档/记忆不符，须记录）

这一节是本文最有价值的部分——四条结论都与凭印象推断的结果不同。

### 4.1 SSE 必须在声明 `response_class` 后直接 `yield`

**实测对照**（同一份生成器，两种接法）：

| 接法 | 结果 |
| --- | --- |
| 声明 `response_class=EventSourceResponse` + `yield` | ✅ 200，格式正确 |
| 不声明 + `return EventSourceResponse(...)` | ❌ `AttributeError: 'ServerSentEvent' object has no attribute 'encode'` |
| 声明 `response_class` + `return EventSourceResponse(...)` | ❌ `TypeError: 'EventSourceResponse' object is not iterable` |

**且必须显式 `response_model=None`**：否则装饰器执行期即抛
`FastAPIError: Invalid args for response field! Hint: ... Iterator[X]
is not a valid Pydantic field type`。报错信息本身给出了解法。

### 4.2 中文在 SSE 线格式中被转义

实测 `text` 为「苯酚」时，线上格式是：

```
event: result
data: {"explanation": "\u82ef\u915a\u9178"}
```

FastAPI 默认 `ensure_ascii=True`。**前端必须 `JSON.parse` 才能还原**，
直接用 `event.data` 会显示转义串。此约束已写入 `streaming.py`
docstring，并有回归测试锁定（若框架改了行为，测试会失败并提示复核）。

### 4.3 FastAPI 0.132.0 起严格校验 Content-Type

`strict_content_type=True` 是 `FastAPI.__init__` 的**构造参数默认值**
（实测签名确认；注意 `app.strict_content_type` 属性**不存在**，
不能那样查）。

实测行为：客户端发 `Content-Type: text/plain` 时，请求体
**不会**被解析为 JSON，直接返回 **422**（**不是 415**），且：

```json
{"detail": [{"type": "model_attributes_type",
             "msg": "Input should be a valid dictionary or object...",
             "input": "{\"text\": \"hi\"}"}]}
```

两个问题：英文技术文案不适合高中生；**`input` 回显了原始输入**。
本模块的 `validation_handler` 已重写：中文文案 + **丢弃 input**。

### 4.4 starlette 1.x 的 TestClient 已迁移到 httpx2

实测 starlette 解析为 **1.7.0**，其 `testclient` 优先 `import httpx2`，
失败才回落 `httpx` 并发 `StarletteDeprecationWarning`。

本项目 `pytest.ini` 设了 `error::DeprecationWarning`（针对 `app.*`），
故测试镜像装`httpx2==2.13.1` 而非 httpx。
（供应链提示：starlette 1.1.0/1.3.0/1.3.1 修复了 CVE-2026-48818、
CVE-2026-48817、CVE-2026-54282、CVE-2026-54283，本项目随fastapi
0.142.2 自动获得这些修复。）

## 5. 安全设计（对齐 `security-privacy.md`）

### 5.1 一处实测发现的真实漏洞及修复

初版实现直接透传下层异常的 `user_message`。**实测发现这是漏洞**：

```python
raise LLMUpstreamError("上游返回: key=sk-abcdef1234")
# → 该字符串原样出现在 SSE 响应体中
```

`user_message` 的来源是**上游服务**，不是本项目——把它当
"自己写的文案"透传，等于把上游内容当作可信输入。

**修复**：`public_message` 只对 `api_` 前缀（本项目自己登记的）
错误码使用登记文案，下层错误码一律通用文案。代价是失去了
下层更精准的措辞（如"请检查是否写错括号"），换来的是不会泄露上游原文。
回归测试 `test_error_event_hides_internal_details` 锁定该行为。

### 5.2 官能团必须过滤 matched=False（本模块抓到的最严重缺陷）

真实 HTTP 冒烟测试时发现：`/api/v1/molecule` 对乙醇返回
**全部 11 个官能团**（含醛基、羧基、酯基、氨基、碳碳三键…），
苯酚、乙酸、乙腈同样如此——看起来像"检测完全失效"。

**根因不在 chem 模块**：`FunctionalGroupHit` 对全部 11 个模式
都返回一条记录，未命中的用 `matched=False` +空 `atom_indices` 标记，
底层数据完全正确（乙醇只有羟基 `matched=true`）。
**是API 层未过滤**——把"检测清单"当成了"检测结果"报给用户。

修复后实测（`matched=True` 才输出）：

| 结构 | SMILES | 命中 |
| --- | --- | --- |
| 乙醇 | `CCO` | 羟基 |
| 苯酚 | `c1ccccc1O` | 羟基、苯环 |
| 乙酸 | `CC(=O)O` | 羟基、羧基 |
| 乙腈 | `CC#N` | （无） |
| 乙酸乙酯 | `CC(=O)OC` | 酯基、醚键 |
| 苯 | `c1ccccc1` | 苯环 |
| 丙酮 | `CC(=O)C` | 酮羰基 |
| 乙醛 | `CC=O` | 醛基 |

回归保护：`test_only_matched_functional_groups_are_returned`、
`test_ethanol_has_no_false_positive_groups`、
`test_acetic_acid_hits_carboxyl_not_ester_or_aldehyde`。

**残留局限（非 bug）**：乙酸同时命中"羟基"与"羧基"。
SMARTS `[OX2H]` 只描述"连两个原子且带氢的氧"，无法区分醇羟基与
羧酸羟基。实测确认羟基命中的原子（atom 3）正是羧基命中
（atoms=[1,2,3]）里的那个氧。已登记 I5，建议前端在
`羟基` 与 `羧基` 同时存在时不重复展示羟基。

**这个缺陷说明了一件事**：TestClient 层的 64 项测试全绿，
但真实端到端冒烟立刻抓到了它。**两者不可互相替代。**

### 5.3 校验失败不回显输入

见 §4.3。测试 `test_validation_error_does_not_echo_input` 锁定。

### 5.4 错误码到 HTTP 状态码的映射（实测纠正）

初版实现对下层错误码一律用保守默认（500、不可重试）。**实测发现这对
四个关键码是错的**：

| 错误码 | 初版（错） | 现版 | 理由 |
| --- | --- | --- | --- |
| `llm_not_configured` | 500 | **503** | 编排系统会当成内部错误反复重启，而真正需要重启的恰恰只有未配置 |
| `llm_upstream_unavailable` | 500 | **503** | 服务暂时不可用，重试有意义 |
| `llm_timeout` | 500 | **504** | 上游超时，客户端可安全重试 |
| `llm_rate_limited` | 500 | **429** | 上游在限流，客户端应退避 |
| `chem_invalid_structure` | 500 | **400** | 用户输入问题，不是服务故障 |

区分两种限流：`api_rate_limited`（429）是"**你**请求太频繁"，
`llm_rate_limited`（429）是"模型服务在限流**我们**"——错误码不同，
客户端才能采取不同动作。

未登记的下层错误码仍走保守默认（500、不可重试），错误码原样保留
以便排障定位。测试 `test_retryability` 与
`test_unregistered_domain_code_falls_back_conservatively` 锁定。

另注：全局兜底处理器的状态码取自 `classify()` 而非硬编码 500，
否则缺配置仍会返回 500。

### 5.5 CORS 白名单

`allow_credentials=True` 时FastAPI 禁止 `allow_origins=["*"]`
（启动即 `ValueError`）。本模块从配置层就不产生通配符组合，
来源由 `XUEZHI_CORS_ORIGINS` 给出，留空则**完全不注册**跨域。

### 5.6 限流：为什么不用 slowapi

`slowapi` 最新版本 **0.1.10**（PyPI 实测），维护活跃度低，
且 `Limiter` 依赖全局状态与 `request.app` 绑定，测试难以隔离。
本模块用标准库实现令牌桶（60 行），少一个依赖即少一处供应链风险。

**限流实现在内存态**：多 worker 部署时每个进程各算一份，
实际总配额是 `workers × rate`。这是刻意取舍（免引入 Redis），
**生产多 worker 前必须换成分布式实现**。已登记为待办。

限流豁免 `/health` 与 `/ready`——否则排障时探针会被自己的限流挡住
（`deployment-operations.md` §6）。

## 6. 未实现与原因

| 项| 原因 |
| --- | --- |
| 取消/中断请求 | 需任务状态存储（决策项 A2 会话存储方式未定）。当前 `AgentLoop` 同步执行，中断需在线程层实现 |
| 工具调用接口 | 走 MCP 标准（决策项 A1未定），不宜先做私有接口 |
| 语音/数字人 | 依赖 backend-speech 模块（Fay 未接入） |
| `sources` 填充 | `AgentLoop.run` 只返回 `text/steps/tool_invocations`，**不返回检索片段**。见下|

### `sources` 为空数组是当前的实际状态，不是遗漏

`interface-contract.md` §3 要求 `sources` 含来源 ID、标题、版本、定位。
但既有 `AgentLoop` 的返回结构里**没有这个字段**——检索结果只以
文本形式回填给模型，API 层拿不到结构化的来源对象。

当前实现返回**空数组**，即 `interface-contract.md` §3 的
"无来源时为空，不伪造"。要让 `sources` 有值，须扩展
`AgentLoop.run` 的返回结构——已登记为待办。

**这意味着前端目前无法展示"依据来自哪本教材第几页"。**
在补齐前，前端不应显示来源区域（而非显示空列表）。

## 7. 未做的事（明确的取舍）

**没有做 token 级流式**（§3）、**没有做取消接口**（§6）、
**没有填充 sources**（§6）。三者都需要改动已验证的既有模块，
按项目纪律应单独开分支并重跑其全部测试。

**没有引入 pydantic-settings**：`ServiceSettings` 用dataclass +
`from_env` 实现即可，与既有 `llm/config.py` 的做法一致，
不引入额外配置框架。

## 8. 测试覆盖

**77 项**（逐类实测，非估算），按行为而非实现组织：

| 测试类 | 项数 | 锁定的行为 |
| --- | --- | --- |
| `TestErrorClassification` | 17 | 错误码透传、状态码映射、可重试性、上游文案不泄露 |
| `TestRateLimiter` | 9 | 令牌补充、突发上限、时间倒流防护、内存上界 |
| `TestMoleculeEndpoint` | 9 | 真实 RDKit 解析、官能团过滤、无需模型可用 |
| `TestValidationAndRedaction` | 8 | 中文报错、结构化脱敏、多余字段拒绝 |
| `TestStreamFormat` | 7 | 事件顺序、meta 首发、中文转义、空行收尾 |
| `TestHealthEndpoints` | 7 | 恒 200、组件明细无密钥、健康检查豁免限流、探测缓存 |
| `TestStreamErrorHandling` | 4 | 错误走事件通道、细节不泄露、无 result 事件 |
| `TestCors` | 4 | 白名单、预检、无通配符 |
| `TestOpenAPI` | 3 | 契约可生成、schema 进文档 |
| `TestStreamValidation` | 1 | 建流前先校验 |
| `TestDependencyOverride` | 1 | 依赖可替换（可测性前提） |
| **`TestRealHttpEndpoints`** | **7** | **真实 socket 上的端到端行为**（见下） |
| **合计** | **77** | |

### 为什么需要 E2E 这一层

`TestRealHttpEndpoints` 不是前11 项的重复——它起真实 uvicorn、
走真实 TCP，用标准库 urllib 发请求（刻意不用 httpx，
避免与 TestClient 共享代码路径）。

**它的价值已被证明**：写这个文件时，TestClient 的 64 项测试**全绿**，
但真实冒烟立刻抓到「官能团未过滤 `matched`」这个缺陷（§5.2）。
单元测试经过 ASGI 应用的完整栈，但**不经过真实 socket**——
两者覆盖的不是同一件事。

### 开发过程中被测试抓到的实现缺陷

这四个都不是"测试写错"，是实现问题，记录在此供追溯：

1. **官能团未过滤 `matched`**（`routes.py`）—— 乙醇被报成含全部 11 个基团。
   **由真实 HTTP 冒烟发现，TestClient 测试当时全绿**（见 §5.2）。
   这条最能说明问题：单元测试通过不等于端到端正确。

2. **限流器多放行一次**（`ratelimit.py`）
   新客户端建桶时给满 `capacity` 个令牌**且直接 return 不扣减**，
   导致实际能通过 `capacity + 1` 次。演示中即"限流 8/s 却能连发 9 次"。
   修复：建桶后落入统一扣减路径。测试
   `test_new_client_gets_full_bucket` 捕获。

3. **上游错误原文泄露**（`errors.py`）
   见 §5.1。测试 `test_error_event_hides_internal_details` 捕获。

4. **配置校验位置错误**（`deps.py`）
   `ServiceSettings(rate_limit_burst=0)` 能构造成功，
   却要等到 `RateLimiter` 构造时才抛错——报错位置离原因很远。
   修复：把校验移到 `ServiceSettings.__post_init__`。
   测试 `test_rate_limit_config_is_validated` 捕获。

另有一处 `MoleculeResponse` 漏了 `properties` 字段：RDKit 真实返回里
有分子式等性质，但响应模型没暴露，前端拿不到。测试
`test_parses_ethanol` 捕获。

还有一处自查发现：`probe()` 每次调用都重跑 RDKit 解析，
而 `/health` 高频调用它。已加缓存，测试
`test_probe_results_are_cached` 锁定。

### 真实 HTTP 端到端冒烟（TestClient 之外）

除单元测试外，实际起了 uvicorn、走 TCP 验证：

| 检查项 | 实测结果 |
| --- | --- |
| `GET /health` | 200，`status=not_ready`（未配密钥），组件明细 chem/llm/rag 分明 |
| `GET /ready` | **503**（k8s 约定） |
| `POST /api/v1/molecule` | 200，苯酚 C6H6O、分子量 94.113，附带 notes 声明 |
| 缺 `Content-Type` | **400**（经自定义处理器），且**未泄露 `input`** |
| `GET /openapi.json` | 200，5 路径 / 13 schema |
| `POST /api/v1/ask/stream`（无密钥） | 200 + `text/event-stream`，事件序列 `meta → stage → error` |

最后一项是关键证据：**SSE 在后端不可用时仍返回 200 并走事件通道报错误**，
这正是 §2 所述"响应头发出后无法改状态码"的直接体现。

## 9. 复现命令

```bash
# 构建测试镜像（含 fastapi/uvicorn/pydantic/httpx2）
export https_proxy=http://127.0.0.1:7897 http_proxy=http://127.0.0.1:7897
docker build -t xuezhi-chem-test -f backend/tests/Dockerfile.test .

# 全量测试
docker run --rm xuezhi-chem-test

# 只跑 API 模块
docker run --rm xuezhi-chem-test python -m pytest backend/tests/api/ -v

# 启动服务（需先设 MAAS_API_KEY）
docker run --rm -p 8000:8000 -e MAAS_API_KEY=... xuezhi-chem-test \
  uvicorn app.api.app:app --host 0.0.0.0 --port 8000 --app-dir backend
```

首次构建约 **20 分钟**（要下torch 554MB + triton 248MB），
之后命中缓存仅需数秒。

> **Git Bash 路径陷阱**：`docker run -v "F:/..."` 会被 MSYS 转换，
> 须先设 `MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*"`；
> `-w /work` 也须写成长选项 `--workdir /work`。

### 镜像构建的两个实测坑

**1. 大包下载会超时**：torch（554MB）触发
`ReadTimeoutError`——本机直连仅 30 kB/s，必须走代理。
已在 Dockerfile 设 `PIP_DEFAULT_TIMEOUT=180 PIP_RETRIES=8`。

**2. 更隐蔽的一种：代理返回内容损坏的包**。
一次构建中 `zstandard`（5.5MB）传输完成后
**sha256 不匹配**，pip 报：

```
ERROR: THESE PACKAGES DO NOT MATCH THE HASHES FROM THE REQUIREMENTS FILE.
    Expected sha256 0a759da5...
         Got        cdf58836...
```

这个错看起来像"依赖被篡改"，实为**网络传输损坏**。
排查耗时且容易误判为供应链问题。

**处置：Dockerfile 已改成分层安装**——重依赖链条与Web 层分开。
这样 Web 层失败时只重跑 Web 层，不必重下 554MB。
若再遇同样报错，直接重跑构建即可（pip 会重新下载损坏的包）。

## 10. 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MAAS_API_KEY` | 无 | **必填**，缺失则 `/ready` 返回 503 |
| `XUEZHI_STRICT_STARTUP` | `0` | 设 `1` 则配置不全时启动失败（生产建议） |
| `XUEZHI_CORS_ORIGINS` | 空 | 逗号分隔；留空不注册 CORS |
| `XUEZHI_RATE_LIMIT_RPS` | `1.0` | 每秒补令牌数 |
| `XUEZHI_RATE_LIMIT_BURST` | `8` | 突发上限 |
| `XUEZHI_REQUEST_TIMEOUT` | `120` | 单请求超时（秒） |
| `XUEZHI_RETRIEVAL_THRESHOLD` | 空 | 检索阈值；**留空表示不设**（见 I1） |
| `XUEZHI_RETRIEVAL_TOP_K` | `4` | 检索返回条数 |
| `XUEZHI_CHROMA_PATH` | `data/chroma` | 向量库目录 |
| `XUEZHI_COLLECTION` | `xuezhi_organic` | 集合名 |
| `XUEZHI_EMBEDDING_PATH` | 空 | 本地权重目录；设了则离线加载 |

**不提供任何带默认值的密钥**，与 `llm/config.py` 同一原则
（`deployment-operations.md` §6：配置缺失时快速失败）。

### 关于 `XUEZHI_RETRIEVAL_THRESHOLD` 默认留空

语料从 6 条增至 41 条后，None/0.4/0.5/0.6/0.7 五个阈值的
top3 命中率**完全相同**（均 96.97%，见 `organic-corpus-verification.md`）。
设一个没有实测依据的阈值只会制造"已调优"的假象，故留空。

---

## 静态契约与漂移检测（F4，2026-10-04）

### 为什么需要

改接口字段却不更新契约，是**前端静默出错**的根源：
后端测试全绿（它测自己的实现），前端测试也全绿（它对着旧字段断言），
两边都对，只是**彼此不再匹配**。

`docs/interface-contract.md` 是双方共同的约定，
静态契约文件是它的**机器可读形式**。

### 导出

```bash
python -m scripts.export_openapi# 写入 docs/api/openapi.json
python -m scripts.export_openapi --check        # 只校验，不写入（CI 用）
```

实测产出：**5 端点 / 13 schema**，OpenAPI 3.1.x。

### 三个实测踩坑点

#### ① 必须用 `python -m`，且 `scripts/` 需要 `__init__.py`

直接 `python scripts/export_openapi.py` 会把 `scripts/` 放到
`sys.path` 最前，`from app.api.app import ...` 失败
（`app` 在 `backend/` 下）——而同样的导入在 pytest 和 uvicorn 里正常。

但 `python -m scripts.export_openapi` 又要求 `scripts` 是包，
否则报 `No module named 'scripts'`。
**两个问题一起解**：加 `scripts/__init__.py`，并统一用 `-m` 从仓库根跑。

#### ② 确定性三要素，缺一项 diff 就失效

`sort_keys=True` + `indent=2` + 末尾换行。

不排序的话，字典插入顺序变化会产生格式噪声，
审阅者要在格式抖动里找真正的契约变更。
实测：连续两次导出**字节完全一致**。

#### ③ 报错要说"哪里变了"，不是逐行 diff

契约文件上千行，逐行 diff 会淹没关键信息。
故 `--check` 失败时报告：**新增端点 / 删除端点 / 新增 schema / 删除 schema**。
删除项加 `**` 标记——那是会破坏前端的部分。

### 测试（15 项）

| 组 | 覆盖 |
| --- | --- |
| `TestContractFile` | 文件存在、合法 JSON、必备段、`summary` 非空、**`operationId` 存在且全局唯一** |
| `TestContractMatchesCode` | 契约与代码一致、**导出确定性**、序列化三要素、**漂移检测反向验证**、文件缺失时的报错 |
| `TestContractMatchesResponses` | `sources` 字段存在且类型为 `SourceItem` 数组、`SourceItem` 含契约 §3 要求的字段、每个操作声明响应、错误码枚举须已登记 |

**`operationId` 唯一性**值得单说：重复会导致前端代码生成器
产生重名函数并**静默覆盖**其中一个——
这类问题在契约 diff 里只看得出"没变"，极难排查。

### 反向验证（关键）

篡改契约（把 `/api/v1/molecule` 改名）后：

```text
AssertionError: 契约与代码不一致。
E   **契约已变更**（代码与已提交文件不一致）
E   新增端点：['/api/v1/molecule']
E   **删除端点：['/api/v1/molecule_RENAMED']**
E   若**不**预期，说明有人改了接口却没更新契约
```

确认检测有效且报错可用。

### 写这个测试时自己犯的三个错

**① 断言基于错误假设**：`assert "sources" in required`。
查模型才发现 `sources` 用的是 `Field(default_factory=list)`
——**有默认值，故契约里正确地不列为 required**。
强制它必填反而与实现不符。改为校验真实不变量：
字段**存在**、类型是 `array`、元素指向 `SourceItem`。
（真正的风险不是"必填与否"，而是字段整体消失。）

**② 按前缀盲抓导致两次误报**：
先用 `tool_` 前缀正则扫全文，抓到 `"default": "tool_verified"`
（那是 `Verification` 枚举的默认值，与错误码无关）；
改用排除名单后，又抓到 `tool_invocations`（那是 `AnswerResponse` 的**字段名**）。

两次都说明：**"长得像错误码的字符串"远多于错误码**，
排除名单是打地鼠，加一个漏一个。
最终改为**结构化遍历 `enum` 数组**——错误码若被正式枚举必然在此，
不猜、不排除。

**③ 缩进期望值凭想象写的**：断言 `'\n  "x": 3'`，
但 `x` 在第二层，`indent=2` 下应是 **4 空格**。
实际跑一次才看得出。**断言写出来也得实跑验证**，
这与本项目其他测试的教训同源。

### 仍未做

| 事项 | 原因 |
| --- | --- |
| CI 工作流 | 仓库暂无 `.github/`，属 infra |
| 前端类型自动生成 | 需引入 `openapi-typescript` 等工具，须先定前端方案 |

### 与 MaaS 的 schema 关系

若将来 `servers` 指向 MaaS，注意两者 OpenAPI 版本可能不同：
本项目 FastAPI 0.142.2 默认输出 **3.1.x**（实测），
而部分 codegen 与 diff 工具只支持 3.0——接入时须先核实工具的版本支持。
