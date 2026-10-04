# 项目文档

本目录按产品、架构、部署、知识治理、安全和接口基线组织项目文档。文档描述目标系统与上线前要求；当前仓库是否包含对应实现，以根目录 README 和实际源码为准。

| 文档 | 读者 | 用途 |
| --- | --- | --- |
| [开发流程与分支规范](development-workflow.md) | 全体开发人员 | 模块划分、分支策略、提交与更新要求 |
| [产品范围与验收标准](product-scope.md) | 项目负责人、教师、测试人员 | 定义用户、范围、非目标及验收基线 |
| [系统架构与组件职责](architecture.md) | 开发与架构人员 | 描述组件边界、数据流、运行模式和待定决策 |
| [部署与运维手册](deployment-operations.md) | 开发、部署与演示人员 | 环境准备、发布检查、故障处理和回滚要求 |
| [知识库内容治理](knowledge-base.md) | 教师、知识库维护者 | 来源准入、审核、索引构建、评估和撤回流程 |
| [依赖选型实测记录](dependency-notes.md) | 开发与运维 | 版本约束的 PyPI 实测依据、未锁定阻塞项与复核方式 |
| [Python 版本升级评估](python-version-evaluation.md) | 项目负责人、开发 | 3.10 与 3.12 的依赖兼容性实测对比与基线选型建议 |
| [H1/H3 核查结论](h1-h3-verification.md) | 开发与运维 | conda rdkit 与 chromadb 的实测核查，含前次误判更正 |
| [3.12 环境安装验证报告](py312-install-verification.md) | 开发与运维 | Python 3.12 venv 中的依赖安装实测结果与发现的文档缺陷 |
| [化学引擎实现与验证状态](chem-engine-verification.md) | 开发、化学审核者 | backend-chem 模块的实现范围、验证状态与未实跑项 |
| [模型适配层实现与验证状态](llm-adapter-verification.md) | 开发 | backend-llm 的 API 依据、实现范围与验证结果 |
| [Agent 编排层实现与验证状态](agent-dispatcher-verification.md) | 开发 | backend-agent 的工具白名单、调度层与 LangChain 协议契约 |
| [知识检索层实现与验证状态](rag-verification.md) | 开发、教师 | backend-rag 的准入治理与中文 embedding 限制 |
| [中文嵌入模型验证报告](embedding-model-verification.md) | 开发、教师 | bge-small-zh 选型依据与中文检索质量实测 |
| [知识检索工具接入验证](agent-knowledge-tool-verification.md) | 开发、教师 | RAG 接入 Agent 的工具契约、失败语义与端到端验证 |
| [知识数据层实现与验证](knowledge-base-implementation.md) | 开发、教师 | 教材切分依据、版权边界与检索质量实测 |
| [有机化学自编讲义验证](organic-corpus-verification.md) | 开发、教师 | 41 条语料的课标依据、易错点梳理与检索实测 |
| [开发留痕 2026-10](development-log-2026-10.md) | 全体 | 首批模块实施记录：被推翻的判断、环境约束、失误清单 |
| [接口契约实现与实测](interface-contract-verification.md) | 开发、前端 | API 网关实现、实测到的框架行为差异、SSE 契约与安全设计 |
| [Agent 流式与来源验证](agent-streaming-verification.md) | 开发、前端 | 逐 token 流与结构化 sources 的实现、实测依据与三个陷阱 |
| [官能团 SMARTS 精度修正](chem-smarts-verification.md) | 开发、教师 | 醇羟基与酚羟基的拆分依据、16 个结构实测与三种写法的对比 |
| [torch 移除方案评估](h8-torch-removal-evaluation.md) | 项目负责人、开发 | 云端模式是否移除 sentence-transformers 以避免 torch 的权衡分析 |
| [安全与隐私基线](security-privacy.md) | 项目负责人、开发与运维 | 数据最小化、密钥、工具安全和事件响应要求 |
| [前后端逻辑接口基线](interface-contract.md) | 前后端与 Agent 开发人员 | 实现前统一语义；后续由 OpenAPI 和契约测试定稿 |
| [决策登记表](decision-register.md) | 项目负责人、开发与运维 | 集中登记未决事项、实测缺口与文档回填项 |

## 文档维护规则

- 每个模块的开发、更生在独立分支上进行，规则见[开发流程与分支规范](development-workflow.md)。
- 发布能力前，应将目标描述更新为经测试和验收的实际行为。
- API、配置键、端口、模型标识和运维目标只记录经实现或服务方文档确认的值。
- 内容范围、模型、依赖、数据来源或外部服务发生变化时，更新相关文档并保留评审记录。
- 将未决事项显式登记为待决策，不使用猜测值填充生产配置；统一登记在[决策登记表](decision-register.md)。
- 模块行为变化时同步更新对应文档，不留"代码已改、文档未改"的中间状态。
