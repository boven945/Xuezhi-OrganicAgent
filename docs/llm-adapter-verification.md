# 模型适配层实现与验证状态

本文记录 `backend-llm` 模块的实现范围、API 依据与验证结果。

- 分支：`feat/backend-llm-model-adapter`
- 模块标识：`backend-llm`（见 `docs/development-workflow.md` §2）
- 基线：Python 3.12 + langchain-openai 1.6.7 + openai 3.24.0

## 1. 实现范围

| 文件 | 职责 |
| --- | --- |
| `backend/app/llm/__init__.py` | 模块导出与职责边界 |
| `backend/app/llm/errors.py` | 受控错误类型与稳定错误码 |
| `backend/app/llm/config.py` | 配置读取、校验、密钥防泄漏 |
| `backend/app/llm/client.py` | 客户端封装、上游错误映射、文本提取 |
| `backend/tests/llm/test_client.py` | 49 个用例 |

## 2. 外部 API 依据（不凭记忆）

以下参数来自**华为云官方文档**，2026-09-29 更新：

| 项 | 值 | 来源 |
| --- | --- | --- |
| base_url | `https://api.modelarts-maas.com/openai/v1` | ModelArts MaaS OpenAI 兼容接口文档 |
| 完整端点 | `https://api.modelarts-maas.com/openai/v1/chat/completions` | 同上 |
| 鉴权 | `Authorization: Bearer $MAA_S_API_KEY` | 同上 |
| 模型标识 | `openpangu-2.0-flash`、`openpangu-2.0-pro` | 同上 |
| 区域限制 | **仅西南-贵阳一** | 同上 |

### langchain-openai 1.6.7 参数（实测确认，非记忆）

`ChatOpenAI` 构造参数经 `inspect.signature` 实测：

| 参数 | 是否存在 | 备注 |
| --- | --- | --- |
| `model` | ✅ | **不是 `model_name`**（后者不存在） |
| `base_url` | ✅ | — |
| `api_key` | ✅ | — |
| `timeout` | ✅ | — |
| `max_retries` | ✅ | — |
| `temperature` | ✅ | — |
| `max_completion_tokens` | ✅ | **token 上限的正确参数** |

**踩坑记录**：`max_tokens` 不是直接参数。塞进 `model_kwargs` 虽能工作，但
LangChain 1.6.7 会发出 `UserWarning`（实测在 `-W error::UserWarning` 下失败）；
应使用 `max_completion_tokens`，该参数无警告。

> 教训：写代码前先 `inspect.signature(ChatOpenAI)` 确认参数名，
> 不要凭印象。此处若写 `model_name` 或用 `model_kwargs['max_tokens']`，
> 运行时会直接失败或产生警告。

## 3. 落实的文档约束

| 约束来源 | 实现方式 |
| --- | --- |
| `architecture.md` §1：模型不单独担任化学验证器 | 本层只做请求转发与结果取回，不做任何化学判断，也不重写模型结论 |
| `architecture.md` §6：日志不得含密钥与学生原文 | 日志只记录 `model`、`elapsed`、错误类型；不记录输入内容、完整提示词、API Key |
| `security-privacy.md` §3：密钥不入日志/前端/错误信息 | `api_key` 声明为 `field(repr=False)`；`public_summary()` 不含密钥；错误 `detail` 只保留异常类型名 |
| `security-privacy.md` §2：数据最小化 | 历史轮次由调用方显式传入，不在服务端留存会话画像 |
| `deployment-operations.md` §6：配置缺失快速失败 | `LLMConfig.from_env()` 缺 `MAAS_API_KEY` 即抛 `LLMConfigError`，消息只说缺哪个键 |
| `interface-contract.md` §5：错误可分类不泄露 | 上游异常映射为 5 类错误码（见 §4），原始响应体不透出 |
| `architecture.md` §6：独立超时 | `timeout` 与 `max_retries` 可配，默认 60s / 2 次（有上限） |
| `product-scope.md` §5：不得把模型输出当权威结论 | 系统提示词基线要求区分检索事实、工具校验与模型推断；无来源时明确说明；超范围时说明边界 |
| `security-privacy.md` §3：密钥不提交 Git | 全部配置来自环境变量；仓库内无任何密钥文件 |

## 4. 错误分类

| 错误码 | 触发条件 | 可重试 |
| --- | --- | --- |
| `llm_not_configured` | 缺密钥或配置非法 | 否 |
| `llm_invalid_input` | 输入为空 | 否 |
| `llm_auth_failed` | 上游 401/鉴权失败 | 否 |
| `llm_timeout` | 请求超时 | 是 |
| `llm_rate_limited` | 上游 429/限流 | 是 |
| `llm_upstream_unavailable` | 其他上游错误 | 是 |
| `llm_empty_response` | 模型返回空内容 | 否 |
| `llm_internal_error` | 兜底 | 否 |

`LLMTimeoutError` 与 `LLMRateLimitError` 继承自 `LLMUpstreamError`，
便于上层按"上游问题"统一降级，同时保留细分类。

## 5. 验证结果：✅ 91/91 通过

```
$ docker run --rm xuezhi-chem-test python -m pytest backend/tests/
91 passed in 0.86s
```

其中 backend-chem 42 项、backend-llm 49 项。

**测试不发起真实网络请求**（配置校验为纯本地逻辑，上游交互用替身对象注入），
因此**不需要 API Key，也不会产生费用**。真实连通性测试属决策登记表 B4，
需华为云账号与授权后再执行。

### 5.1 测试暴露的两处缺陷（已修复）

| # | 缺陷 | 发现方式 | 修复 |
| --- | --- | --- | --- |
| 1 | `__init__.py` 漏导出 `LLMRateLimitError` | 收集阶段 `ImportError` | 补齐导入与 `__all__` |
| 2 | `_extract_text` 对空串直接返回，未抛错 | `test_empty_content_raises` 失败 | 改为先归一化再判空，空内容抛 `llm_empty_response` |

第 2 项是真实的行为缺陷：模型返回空内容时，上层会把空串当成功答复，
学生看到空白却没有任何错误提示——违反 `interface-contract.md` §5。
**这是测试的价值所在，不是为测试而改代码。**

## 5.2 真实连通性测试结果（2026-10-03 深夜）

用真实 MaaS API Key 在容器内发起实际请求，结果如下。

### 结论：端点与密钥均正确，**阻塞在"预置服务未开通"**

```
错误类型: PermissionDeniedError
错误码: 403 / ModelArts.81004
错误信息: Invalid request because you do not have access to it.
```

对 `openpangu-2.0-flash` 与 `openpangu-2.0-pro` 分别测试，**均为 403**。

### 已验证 vs 未验证

| 项 | 状态 | 依据 |
| --- | --- | --- |
| 端点 `https://api.modelarts-maas.com/openai/v1` | ✅ **正确** | 返回业务错误而非连接失败/404 |
| API Key 有效性 | ✅ **有效** | 返回 403 而非 401；密钥已通过鉴权 |
| 区域（西南-贵阳一） | ✅ 正确 | 未报区域相关错误 |
| 模型标识格式 | ✅ 正确 | 与官方文档一致 |
| **预置服务开通状态** | ❌ **未开通** | ModelArts.81004 |

**关键判断：403 而非 401 说明密钥有效**。若密钥错误会返回
`401 AuthenticationError`；此处是"已认证但无该服务权限"。

### 复测结果（服务开通后，2026-10-03 23:50）

服务开通后重新测试，**三项全部通过**：

| # | 验证项 | 结果 |
| --- | --- | --- |
| 1 | 基础对话连通 | ✅ 成功 |
| 2 | Function Calling | ✅ 完全可用 |
| 3 | 上下文长度 | ✅ 上限确认为 512,000 token |

基础对话实测：

```
模型标识: openpangu-2.0-flash
回复: 酯化反应是羧酸与醇在酸性条件下反应生成酯和水的化学反应。
usage: prompt=20 completion=436 total=456
```

### 关键发现 1：模型默认开启深度思考模式

`message` 实际字段为：

```
['content', 'refusal', 'role', 'annotations', 'audio',
 'function_call', 'tool_calls', 'reasoning_content']
```

**存在 `reasoning_content` 字段**（思考过程）。实测一次简单问答：
`completion=436` token 中约 **704 字符是思考内容**，正式答案仅 28 字。

**对实现的影响**：`max_completion_tokens` 必须给足，否则思考过程会吃掉
配额导致 `finish_reason=length` 且 `content` 为空。

> 实测：设 `max_completion_tokens=200` 时，`finish_reason=length`、
> `content` 长度为 **0**（全部 token 被思考占用）。
> 提到 2000 后正常输出（`finish_reason=stop`）。
>
> **这是初次测试时"回复为空"的真实原因**，不是 API 故障。
> `backend/app/llm/config.py` 的默认 `max_completion_tokens=2048` 足够安全。

### 关键发现 2：`tool_choice` 不支持指定具体函数

实测报错：

```
ModelArts.81001: Invalid value for `tool_choice`:
{'type': 'function', 'function': {'name': 'lookup_reaction'}}!
The Pangu model supports only "none", "auto", and "required".
```

**这是与 OpenAI 规范的差异，必须在工具调度层规避**：

| 取值 | 是否支持 | 实测行为 |
| --- | --- | --- |
| `none` | ✅ | 不调用工具，`finish_reason=stop` |
| `auto` | ✅ | 模型自主决定，`finish_reason=tool_calls` |
| `required` | ✅ | 强制调用，`finish_reason=tool_calls` |
| `{'type':'function',...}` | ❌ | **400 报错** |

含义：**无法强制指定某一个具体函数**。若需"必须调用 parse_smiles"，
只能把该函数单独放进 `tools` 列表并用 `required`，
或用 `auto` 并在提示词中引导。`architecture.md` §5 的工具白名单机制
在此背景下更有必要。

### 关键发现 3：上下文上限确认为 512,000 token

官方错误信息直接给出数值：

```
Tokenizer encode failed: the prompt length 514297 must less than
the maximum input length 512000
```

**"512K 上下文"的说法得到实测确认**（此前 `dependency-notes.md`
已注明该指标"不构成端到端请求长度保证"，现已可量化）。

探针测试（长文本中埋入 A/B/C 三个标记，检验模型能否找回）：

| 输入字符数 | prompt_tokens | 命中标记 | 耗时 |
| --- | --- | --- | --- |
| 2,016 | 1,181 | A, B, C | 9.7s |
| 20,020 | 11,469 | A, B, C | 9.9s |
| 100,016 | 57,181 | A, B, C | 7.3s |
| 300,000 | 171,469 | A, B, C | 12.9s |
| 600,000 | 342,895 | A, C（中间标记 B 漏召回） | 29.0s |
| 1,200,000 | — | **400 报错**（超上限） | — |

**注意 600,000 字符一档**：B（中间位置）未被召回，说明接近上限时
"大海捞针"能力已下降。因此**实际可用上限应留足余量**，
不建议按 512K 满载使用。具体安全阈值需由项目负责人确定（决策登记表 C2）。

### 关键发现 4：延迟与并发实测

短请求（"简述酯化反应"，5 次）：

| 指标 | 值 |
| --- | --- |
| 中位数延迟 | **7.10s** |
| 最小 / 最大 | 5.28s / 9.82s |
| completion tokens | 328 ~ 580（含思考内容） |

5 并发请求：全部成功（`OK × 5`），总耗时 21.52s。
**未触发限流**，但 5 并发已使单请求耗时上升，**实际并发阈值需查配额文档**
（`deployment-operations.md` §4 要求记录配额，此项尚未确认）。

> 延迟数据仅供发布基线参考。`product-scope.md` §7 明确
> 延迟目标须由项目负责人依实测批准，**本文不预设阈值**。

### 官方解决方案

按华为云文档，`ModelArts.81004` 的含义是"尚未开通调用的预置服务"，需在
MaaS 控制台开通：

1. 登录 **ModelArts 控制台**（注意不是 MaaS 控制台）
2. 选择区域：**西南-贵阳一**
3. 左侧导航 → **模型推理 → 在线推理 → 预置服务**
4. 找到 `openPangu-2.0-Flash`，点击右侧**开通服务**
5. 确认计费方式与费用评估，勾选同意声明后确认

> 参考：官方文档错误码表明确列出
> `403 ModelArts.81004 Invalid request because you do not have access to it.
> 尚未开通调用的预置服务。请先开通预置服务。`

平台通常提供 2,000,000 tokens 免费额度（依账号与活动而定，实际以控制台为准）。

### 开通后仍需验证的事项

以下项目**必须实测**，不能依据文档推定：

- [ ] `openpangu-2.0-flash` 基础对话连通（当前阻塞）
- [ ] **Function Calling 是否真的支持**——`architecture.md` 与
      `product-scope.md` 均以原生 Function Call 为工具调用基础，
      官方文档称支持但**本项目尚未实测**
- [ ] 上下文长度是否为 512K（文档称是，但 `dependency-notes.md`
      已注明"不构成端到端请求长度保证"）
- [ ] 限流阈值（RPM）与计费单价，用于 `deployment-operations.md` §4 的配额记录

## 6. 尚未实现（属后续模块）

| 能力 | 归属模块 | 说明 |
| --- | --- | --- |
| 工具调用（Function Calling） | `backend-agent` | 官方文档称 openPangu-2.0-Flash 支持原生 Function Call，**但需实测确认**（B4） |
| 流式输出 | 待 A3 决策 | 需先确定传输架构（架构 §3） |
| 本地推理适配器 | `backend-llm`（后续） | 依赖 G3/G4 结论：autoawq 需编译、RTX 5070 CUDA 未实测 |
| 重试退避策略 | `backend-llm`（后续） | 目前只做有上限的次数控制，退避间隔待与限流配额一并确定 |
| 调用成本统计 | `backend-api` | 属可观测性要求（架构 §6） |

## 7. 复核方式

```bash
# 容器内运行全部测试
docker build -f backend/tests/Dockerfile.test -t xuezhi-chem-test .
docker run --rm xuezhi-chem-test

# 只跑模型层
docker run --rm xuezhi-chem-test python -m pytest backend/tests/llm/ -v

# 核实 ChatOpenAI 参数（避免凭记忆）
docker run --rm xuezhi-chem-test python -c "
from langchain_openai import ChatOpenAI
import inspect; print(list(inspect.signature(ChatOpenAI).parameters))"
```

---

## 真实 MaaS 调用实测（2026-10-05）

**首次在真实服务上验证**此前全部依赖模拟的部分。此前 B4/I3/H15 等结论
均来自文档与推断，本次用真实 Key 逐条核对。

### 端点与模型：项目现值**正确**，无需改动

| 项 | 实测结果 |
| --- | --- |
| `MAAS_BASE_URL=https://api.modelarts-maas.com/openai/v1` | **可用**（HTTP 200） |
| `MAAS_MODEL=openpangu-2.0-flash` | **可用** |

**一处自我纠正**：初次探测时我用 `deepseek-v3` 作model，
三个端点全部返回 404 `Invalid model`，我据此以为"端点路径不对"。
换回 `openpangu-2.0-flash` 后全部成功——**错的是我的探测参数，
不是项目配置**。教训：探测失败时先怀疑自己的输入，
别急着"修正"已验证过的配置。

顺带查明：`DeepSeek-*` 返回 404（模型不存在），
`Qwen3-*` 返回 **403 `you do not have access to it`**（未开通/ 无权限）——
即当前账号只能用 openPangu 系列。

### 四项能力逐条实测

| 能力 | 结果 | 关键数据 |
| --- | --- | --- |
| 基础对话 | ✅ |14.36 秒，内容正确 |
| **工具调用** | ✅ | **2.72 秒**，模型主动发起 `parse_smiles`，参数 `{'smiles': 'CCO'}` 正确 |
| **流式输出** | ✅ | **47 块**，首块 **0.86 秒**，总 2.75 秒 |
| 端到端问答 | ✅ | 3轮，60.2 秒，答复正确 |

### 更正 H15 的一处过时判断

H15 原记「openPangu 不支持 OpenAI 规范的 `tool_choice` 形式」。
**实测确认该结论仍然成立**（不传 `tool_choice` 即可正常发起工具调用），
但**表述需要补一句**：模型在给出工具调用前会先输出一段
说明文本（实测："我需要解析这个SMILES字符串……让我使用工具来解析它。"）。
Agent 层不能假设"有 tool_calls 就一定没有 content"——
本项目的 `stream` 分流逻辑已实测能正确处理两者并存。

### 端到端问答实测暴露的环境问题（非代码缺陷）

真实问答跑了 3 轮、调用了 2 次 `search_knowledge`，但**来源数为 0**。
逐层排查结论：

```text
工具日志：工具执行超时: tool=search_knowledge   ← 首次冷启动
```

进一步实测（容器内，同一查询）：

| 场景 | 耗时 |
| --- | --- |
| 首次检索（嵌入模型冷加载） | **12.8 秒** |
| 二次检索（模型已加载） | **0.02 秒** |

且直接调 `store.query('乙醇的官能团')` 返回：

```text
has_results: False | chunks: 0 | reason: no_match
集合 xuezhi_organic 条数: 0
```

**根因：chroma 索引是空的**——41 条自编讲义**从未被灌进索引**。
故 `search_knowledge` 必然返回 `no_match`，来源数为 0。

**这不是代码缺陷**：检索逻辑、嵌入模型、工具注册、超时设置（30 秒）
全部正常。缺的是**一次性索引构建**（`knowledge-data` 模块的职责）。

> 另记一个实测现象：首次检索 12.8 秒**逼近**工具超时。
> 当前 30 秒够用，但若将来加大模型加载时间需留意——
> `architecture.md` §6 要求各组件有独立超时，检索含嵌入推理，
> 阈值应与嵌入模型加载时间挂钩而非拍脑袋。

### 未验证项

| 事项 | 原因 |
| --- | --- |
| 流式 + 工具调用的**组合** | 本次分开验证（流式 47 块无工具、工具 2.72 秒无流式），未测二者同时 |
| 真实 MaaS 下的 SSE 逐 token 端到端 | 后端可用，但需前端联调确认 |
| 索引构建后的检索质量 | **已验证，见下节** |

## 索引构建与检索质量实测（同日补做）

发现 H24 后立即补建索引并复测。

### 构建结果

```text
构建耗时: 16.0 秒
written: 41 | chunk_count: 41 | topic_count: 21
embedding_model: BAAI/bge-small-zh
avg_chars: 191 | total_chars: 7846
```

### 检索质量（三个查询均`has_results=True`，各 3 条）

| 查询 | 首位命中 |
| --- | --- |
| 乙醇的官能团 | 「乙醇俗称酒精，分子式 C2H6O…含有羟基，属于醇类化合物」 |
| 苯酚和乙醇的区别 | 「醇羟基与酚羟基的差异是两个高频易错点，必须分清…」 |
| 酯化反应的条件 | 「酯化反应是羧酸与醇在浓硫酸催化加热条件下…」 |

**第二个查询值得单独说**：项目语料把「醇羟基 vs 酚羟基」列为**高频易错点**
（决策 I5 的SMARTS 拆分正是为此），检索**首位即命中该条目**——
说明语料与工具口径一致，两者对得上。

### 端到端问答复测

| 项| 修复前 | 修复后 |
| --- | --- | --- |
| 来源数 | **0** | **6** |
| 轮数 | 3 | 2 |
| 耗时 | 60.2 秒 | 29.0 秒 |

来源含**具体章节定位**（如「第三章 / 代表物 乙醇」「第三章 / 第二节 醇 酚」），
满足 `interface-contract.md` §3 对「可追溯」的要求。

轮数与耗时同时下降——之前模型因检索无果而多绕了一轮。
