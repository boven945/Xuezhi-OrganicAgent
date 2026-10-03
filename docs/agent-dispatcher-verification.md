# Agent 编排层实现与验证状态

本文记录 `backend-agent` 模块的实现范围、实测协议契约与验证结果。

- 分支：`feat/backend-agent-tool-dispatcher`
- 模块标识：`backend-agent`（见 `docs/development-workflow.md` §2）
- 基线：Python 3.12 + langchain-openai 1.6.7

## 1. 实现范围

| 文件 | 职责 |
| --- | --- |
| `backend/app/agent/__init__.py` | 模块导出与职责边界 |
| `backend/app/agent/errors.py` | 受控错误类型（6 类） |
| `backend/app/agent/tools.py` | 工具定义、白名单注册表、参数校验、化学工具 |
| `backend/app/agent/dispatcher.py` | 工具调度与 Agent 主循环 |
| `backend/tests/agent/test_dispatcher.py` | 45 个用例 |

另在 `backend/app/llm/client.py` 增加 `invoke_with_tools()`，供 Agent 绑定工具。

## 2. 实测协议契约（关键差异，非假设）

本模块的实现完全依据 2026-10-03 的真实调用结果，**LangChain 层与 OpenAI
原始 SDK 的结构差异是本模块最容易出错的地方**：

| | OpenAI 原始 SDK | **LangChain `bind_tools`（实测）** |
| --- | --- | --- |
| 返回值 | 响应对象，含 `choices[0].message` | **`AIMessage`，没有 `choices`** |
| tool_call 元素 | 对象：`call.function.name` / `.arguments` | **dict：`{"name","args","id","type"}`** |
| `args` 类型 | **JSON 字符串** | **已解析的 dict** |
| assistant 回填 | 需手工构造 dict | **`AIMessage` 可原样追加回传** |

> 我最初按 OpenAI 原始结构编写（`response.choices[0].message`、
> `call.function.name`、`json.loads(arguments)`），真实调用时全部报错。
> **LangChain 已做一层封装，不要按 OpenAI 规范想当然。**

其他实测约束：

- `tool_choice` **不支持**指定具体函数，只接受 `none` / `auto` / `required`。
  因此本层**不使用** `tool_choice`，工具收敛完全依赖白名单
  （与 `architecture.md` §5 的白名单机制一致）。
- `tool_calls[i]["id"]` 形如 `chatcmpl-tool-<hex>`，回填用
  `{"role":"tool","tool_call_id":<id>,"content":...}`。
- 模型默认开启深度思考，`max_completion_tokens` 不可设小。

## 3. 落实的文档约束

| 约束来源 | 实现方式 |
| --- | --- |
| `architecture.md` §5：**工具白名单是强制边界** | `ToolRegistry` 只暴露显式注册的工具；未注册时**抛 `ToolNotFoundError`**，不静默降级。错误消息列出可用工具 |
| `security-privacy.md` §4：禁止执行模型提供的任意代码 | 参数只走 `json.loads`，**不 eval**；不拼接 shell；测试含恶意参数回归用例 |
| `security-privacy.md` §4：参数须 schema 校验 | 逐字段类型校验；**未声明字段直接拒绝**；`bool` 不被当作整数通过 |
| `architecture.md` §5：工具失败须结构化返回 | 失败返回 `ToolResult(ok=False, error_code=...)`，并把错误明确告知模型，让其如实说明而非猜测 |
| `architecture.md` §6：独立超时与失败路径 | 每个工具有独立 `timeout`，超时转 `tool_timeout` |
| `architecture.md` §6：防止循环 | `max_steps` 上限，超出抛 `AgentStepLimitError` |
| `product-scope.md` §5：不声称已完成机理证明 | 化学工具**不提供**判断反应是否发生的能力；测试断言工具名不含 reaction/mechanism/kinetic |
| `architecture.md` §1：模型不单独担任验证器 | 工具结果标记 `source=tool_verified`，与模型推断区分 |
| `security-privacy.md` §4：错误不泄露内部细节 | 参数错误消息不回显原始参数；异常 `detail` 只记类型名 |

### 错误分类

| 错误码 | 触发条件 | 可重试 |
| --- | --- | --- |
| `tool_not_found` | 请求未注册工具（白名单边界） | 否 |
| `tool_argument_invalid` | 参数非法（JSON 错误、缺字段、类型不符、多余字段） | 否 |
| `tool_execution_failed` | 工具执行失败（含化学错误） | 否 |
| `tool_timeout` | 工具执行超时 | 是 |
| `agent_step_limit_reached` | 迭代超限或模型未返回有效结果 | 否 |

## 4. 验证结果

### 4.1 单元测试：45/45 通过

```
$ docker run --rm xuezhi-chem-test python -m pytest backend/tests/agent/
45 passed in 2.25s
```

测试替身按**实测的 LangChain 结构**编写（dict 形态 tool_calls、
`AIMessage` 对象形态 assistant 消息），而非 OpenAI 原始结构——
否则测试会验证错误的契约。

覆盖：白名单边界、参数校验（6 类非法输入）、工具失败隔离、
超时处理、Agent 循环（直接回答 / 工具调用 / 多工具 / 轮数上限 / 历史）、
形状异常的 tool_call 跳过、恶意参数不执行。

### 4.2 真实端到端验证：闭环跑通

用真实 MaaS API Key + openpangu-2.0-flash：

```
问：请帮我解析乙醇的结构，它的 SMILES 是 CCO
迭代轮数: 2
工具调用: [{'tool': 'parse_smiles', 'ok': True, 'error_code': None}]
```

模型第 1 轮发起 `parse_smiles` 调用 → 调度层执行 RDKit → 回填结构化结果 →
第 2 轮生成答复，内容包含正确的分子式、分子量、官能团命中情况，
并**主动附上**"本结果仅确认结构合法性与客观属性，不代表反应机理已被验证"。

这验证了：工具调用发起 → 参数解析 → 白名单校验 → 工具执行 →
结果回填 → 基于结果作答的**完整链路**。

### 4.3 安全边界实测

| 测试 | 结果 |
| --- | --- |
| 模型请求 `execute_shell_command` | `ToolNotFoundError` / `tool_not_found` |
| 传入 `CC(`（非法 SMILES） | `ok=False`，`code=tool_execution_failed`，给模型的提示为结构化错误 JSON |
| 参数含 `__import__('os').system(...)` | 仅作字符串回显，**未执行** |

## 5. 测试暴露的缺陷（已修复）

| # | 缺陷 | 发现方式 | 修复 |
| --- | --- | --- | --- |
| 1 | `_pick_choice` 假设响应有 `choices` | 真实调用抛 `AgentStepLimitError` | 改为直接使用 `AIMessage` |
| 2 | 按 OpenAI 结构读 `call.function.name` / `arguments` | 真实调用报 `AttributeError: 'dict' object has no attribute 'name'` | 改用 dict 键访问，`args` 直接是 dict |
| 3 | 手工构造 assistant dict 回填 | 实测可直接追加 `AIMessage` | 改为原样回传，保留 `tool_calls` |
| 4 | 测试替身用 OpenAI 结构 | 契约错误，测试会验证错误行为 | 替身改为 LangChain 真实结构 |
| 5 | `pytest.raises(...) as e` 后访问 `e.user_message` | `AttributeError: 'ExceptionInfo'` | 改用 `e.value.user_message` |
| 6 | class 级 fixture 在 pytest 10 弃用 | `PytestRemovedIn10Warning` | 改为静态方法内部构造 |
| 7 | 消息角色读取未兼容对象形态 | `'_FakeMessage' object has no attribute 'get'` | 加 `_role_of()` 兼容 dict 与对象 |

**第 1-4 项都源于同一个根因：凭 OpenAI 规范想当然，没先实测 LangChain
的实际返回结构。** 这与 chem 模块的 `GetSmarts` 属同类问题。

## 6. 尚未实现（属后续）

| 能力 | 归属模块 | 说明 |
| --- | --- | --- |
| 流式输出 | 待 A3 决策 | 同步迭代已可用，流式需先定传输架构 |
| RAG 工具 | `backend-rag` | 依赖 ChromaDB 检索层就绪 |
| Fay 语音工具 | `backend-speech` | 依赖数字人服务接入 |
| 会话持久化 | 待 A2 决策 | 依赖存储方案确定 |
| 工具并行调用 | 本模块（后续） | 实测模型单轮可返回多个 tool_calls，当前已支持（`test_multiple_tools_in_one_turn`） |

## 7. 复核方式

```bash
# 单元测试
docker run --rm xuezhi-chem-test python -m pytest backend/tests/agent/ -v

# 核实 LangChain 返回结构（避免凭 OpenAI 规范想当然）
docker run --rm xuezhi-chem-test python -c "
import sys; sys.path.insert(0,'/work/backend')
from langchain_openai import ChatOpenAI
c = ChatOpenAI(model='m', api_key='k', base_url='http://x')
print(type(c.bind_tools([]).invoke([{'role':'user','content':'hi'}])))
"
```
