# 开发流程与分支规范

本文规定 Xuezhi-OrganicAgent 的模块划分、分支策略和更新要求。所有参与本仓库的开发、
更新和评审人员都必须遵守。

## 1. 基本原则

1. **一个模块一个分支**。每个模块的开发或更新在独立分支上进行，不在 `main` 直接提交。
2. **一个分支只做一件事**。分支内变更范围限定在所属模块；跨模块改动必须拆分。
3. **变更可追溯**。每次提交对应一个明确意图，提交信息说明改了什么和为什么。
4. **文档与代码同更新**。模块行为、配置或接口发生变化时，同一分支内同步更新对应文档，
   不留"代码已改、文档未改"的中间状态。
5. **不写猜测值**。端口、配置键、阈值、模型标识一律以实测或服务方文档为准，
   未确认前登记为待决策（见 `decision-register.md`）。

## 2. 模块划分

模块划分与 `architecture.md` 的逻辑组件对应。分支命名必须使用下表标识。

| 模块标识 | 职责 | 主要目录 |
| --- | --- | --- |
| `docs` | 文档体系、评审基线、决策登记 | `docs/`、`README.md` |
| `backend-api` | FastAPI 应用服务、路由、输入校验、响应整形 | `backend/app/api/` |
| `backend-agent` | Agent 编排与 MCP 工具调度层 | `backend/app/agent/` |
| `backend-llm` | 模型适配层（ModelArts MaaS / 本地推理） | `backend/app/llm/` |
| `backend-rag` | 知识过滤、嵌入、召回、来源元数据 | `backend/app/rag/` |
| `backend-chem` | RDKit 解析、结构检查、反应规则校验 | `backend/app/chem/` |
| `backend-speech` | Edge-TTS 与 Fay 数字人适配 | `backend/app/speech/` |
| `frontend-web` | Vue 3 页面、输入输出呈现、状态管理 | `frontend/src/` |
| `frontend-viz` | Three.js / Molstar 分子视图与动画 | `frontend/src/viz/` |
| `knowledge-data` | 知识库源文件、元数据、审核与索引构建 | `data/knowledge/` |
| `infra` | 依赖锁定、Docker、配置模板、CI、环境脚本 | 仓库根、`docker/`、`scripts/` |

新增模块时，先在本文表格登记标识与职责，再开始写代码。

## 3. 分支命名

格式：`类型/模块标识-简短描述`

- 类型：`feat`（新功能）、`fix`（缺陷修复）、`docs`（文档）、`chore`（工程基建）、
  `refactor`（不改变外部行为的重构）
- 模块标识：必须取自第 2 节表格
- 描述：小写、连字符分隔、说明意图而非文件名

示例：

- `feat/backend-chem-valid-smiles-gate`
- `fix/backend-rag-embedding-model-mismatch`
- `docs/interface-contract-openapi-draft`
- `chore/infra-dependency-locking`

## 4. 分支生命周期

1. **开分支**：从最新 `main` 创建，命名符合第 3 节。
2. **开发**：在分支内完成该模块的变更；提交信息说明意图与影响范围。
3. **自检**：合并前确认文档已同步、配置键已登记、无密钥入库、无调试残留。
4. **合并**：模块开发完成后合回 `main`；`main` 必须始终保持可构建、文档与代码一致。
5. **清理**：合并后删除已合并分支。

`main` 分支不接受直接提交，也不接受夹带多个模块的混合变更。

## 5. 更新要求

模块变更时必须同步核对以下内容，缺项视为未完成：

| 变更类型 | 必须同步的内容 |
| --- | --- |
| 新增/修改接口 | `docs/interface-contract.md`；定稿后生成 OpenAPI 并更新契约测试 |
| 新增/修改配置键 | 配置清单（键名、用途、必需性、默认行为、密钥级别、覆盖方式） |
| 知识库内容或处理流程 | `docs/knowledge-base.md` 的版本与评估记录 |
| 部署方式或依赖 | `docs/deployment-operations.md` 的命令、端口、健康检查 |
| 权限与数据处理 | `docs/security-privacy.md` 的最小化与凭据要求 |
| 架构边界或组件职责 | `docs/architecture.md`；新增待决策项登记到 `decision-register.md` |

## 6. 提交要求

- 提交信息首行使用中文或英文祈使句，说明本次改动的**意图**，不只描述文件变化。
- 不提交密钥、真实 `.env`、模型权重、知识库原始受版权保护材料、构建产物。
- 不提交临时调试脚本；确需保留的回归脚本应带断言并进入对应模块目录。
- 脚本产物（报告、索引、缓存）写入 `data/` 或 `artifacts/` 等产物目录，不写在源码目录。
- 大规模重命名单独成一次提交，不与逻辑改动混在一起。

## 7. 评审要点

合并前至少确认：

- 分支只包含单一模块的变更，提交历史可读。
- 新增依赖有明确用途、版本来源和许可证说明。
- 外部服务不可用时有降级路径，不静默替换为编造结果。
- 模型生成内容与工具校验结果在返回结构上可区分。
- 新增未确认信息已进入决策登记表，而不是以默认值形式写进代码或配置。
