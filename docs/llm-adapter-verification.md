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
