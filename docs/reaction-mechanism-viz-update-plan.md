# 3D 分子断键/成键机理可视化 —— 更新规划

> 配套 `docs/reaction-mechanism-viz-plan.md`。本文列出**既有文档与流程需要同步更新的内容**
> 及其**顺序与门槛**，确保符合 `docs/development-workflow.md` §5「更新要求」与分支纪律。
> 本文是**规划**，不是执行记录——具体修改待模块分支开发时落盘。

---

## 1. 模块登记（development-workflow.md）

在 §2 模块划分表新增一行（模块标识 `reaction-viz`）：

| 模块标识 | 职责 | 主要目录 |
| --- | --- | --- |
| `reaction-viz` | 有机反应断键/成键 3D 机理动画：反应库、帧生成、键状态调度、前端播放器 | `backend/app/reaction/`、`frontend/src/components/ReactionMechanism.vue`、`frontend/src/viz/ReactionPlayer.ts`、`data/reactions/`、`scripts/`（可选校验脚本） |

> 与 `frontend-viz`（通用分子视图）职责区分：`frontend-viz` 管单分子渲染，
> `reaction-viz` 管"反应过程"的帧序列与键状态动画。

---

## 2. 分支纪律

- **独立分支**：`feat/reaction-viz-<简短描述>`（如 `feat/reaction-viz-frame-generator`、
  `feat/reaction-viz-player`）。不与 `feat/quiz-practice`（习题模块，仍在 `feat` 分支未提交）混改。
- 一个分支只做 `reaction-viz` 内的单一子任务；跨模块改动拆分。
- 合并前确认：文档已同步、配置键已登记、无密钥入库、无调试残留；`main` 始终保持可构建。
- **决策文档分支（本次）**：D-R1 决策及其取舍依据属纯文档工作，落在独立分支
  `docs/reaction-viz-decision`（类型 `docs`），与下方 `feat/reaction-viz-*` 实现分支分离，
  延续"不同模块/阶段单独开分支、不与 `feat/quiz-practice` 混改"的纪律。实现期 P1–P6 仍各自开
  `feat/reaction-viz-*` 分支。

---

## 3. 需同步更新的既有文档（按依赖顺序）

| 顺序 | 目标文档 | 更新内容 | 触发于 |
| --- | --- | --- | --- |
| 1 | `development-workflow.md` | 新增 `reaction-viz` 模块行（§1） | 开工第一步 |
| 2 | `decision-register.md` | 登记 **D-R1**（渲染引擎，已确认选 C，2026-10-20）+ 竞品核实结论；D-R2~D-R4 待实现期拍板 | 决策拍板后 |
| 3 | `architecture.md` | §2 逻辑组件表新增"反应机理动画"组件；§3 请求主流程补充"机理类问题→动画"分支 | 架构落定后 |
| 4 | `interface-contract.md` | 新增"反应机理动画"交互操作与 `reaction-mechanism/v1` 字段建议；注明复用 `molecule-structure/v2` 坐标 | 端点定稿后 |
| 5 | `docs/api/openapi.json` | 导出新增 `/api/v1/reaction/mechanism`；新增契约测试 + `--check` | 端点实现后 |
| 6 | `security-privacy.md` | 说明反应库为**静态 project-authored 数据**，不含个人信息；动画端点不新增凭据/日志风险 | 实现后核对 |
| 7 | `product-scope.md` | §4 范围内能力补"反应机理 3D 动画"；§5 边界补"不做 DFT/IRC 真实过渡态" | 功能定稿后 |
| 8 | `quiz-module.md` 同级的模块文档 | 新增 `docs/reaction-mechanism-viz-module.md`（结构参照 `quiz-module.md`） | 实现收尾时 |

---

## 4. 数据治理

- 反应库存于 **`data/reactions/`**，schema `reaction-library/v1`，与 `data/knowledge/`
  （`knowledge-corpus/v1`）、`data/quizzes/`（`quiz-bank/v1`）**同级治理**。
- 不写进源码目录；构建产物（若预生成帧）写入 `data/` 或 `artifacts/`（§6 提交要求）。
- `status=draft/approved`；`approved` 须化学教师审核（呼应 D3、I5）。

---

## 5. 实施阶段（建议拆分到不同分支/提交）

| 阶段 | 内容 | 产出 | 验证 |
| --- | --- | --- | --- |
| P0 调研 | 已完成（本文来源文档 + 联网核验） | 两份计划文档 | — |
| P1 schema+反应库 | `reaction-library/v1` + 首批 `draft` 反应（少量、高频） | `data/reactions/candidates.json`、schemas | schema 单测 |
| P2 后端帧生成 | `backend/app/reaction/{library,frames,schemas,routes}.py` | 端点可用 | 容器内 E2E |
| P3 前端播放器 | `ReactionPlayer.ts` + `ReactionMechanism.vue`，复用 `viewer.ts` | 浏览器可抽帧/播放/慢放/高亮 | 前端 E2E |
| P4 Agent 触发 | Agent 判定机理类问题→返回 `reaction_id`/动画触发信号 | 问答流可联动 | 集成测试 |
| P5 测试+文档 | 契约测试、模块文档、既有文档同步更新 | 全量文档一致 | `vue-tsc` + pytest + openapi --check |
| P6 教师审核 | 反应库 `draft→approved` | 内容准确性闭环 | 教师签字/记录 |

---

## 6. 验收门槛（不预设数值）

- 准确率/延迟/可用性：依 `decision-register.md` **C1–C6**，由教师/负责人基于实测批准。
- 反应库内容正确性：化学教师审核断键/成键位点与高中教材一致（D3、I5 口径）。
- 降级：LLM/Agent 不可用时动画端点仍可用；反应库未覆盖的反应退回文本讲解。

---

## 7. 待办清单（执行期勾选）

- [ ] `development-workflow.md` 登记 `reaction-viz`
- [x] `decision-register.md` 登记 D-R1（渲染引擎，已确认选 C，2026-10-20）；D-R2~D-R4 待实现期拍板
- [ ] 实现 `reaction-library/v1` schema 与 `data/reactions/candidates.json`
- [ ] 实现 `backend/app/reaction/` 帧生成 + 端点（复用 `chem/engine.py` 构象）
- [ ] 实现 `frontend/src/viz/ReactionPlayer.ts` 与 `ReactionMechanism.vue`
- [ ] Agent 侧"机理类问题→动画触发"联动
- [ ] 导出并更新 `docs/api/openapi.json` + 契约测试
- [ ] 同步 `architecture.md` / `interface-contract.md` / `security-privacy.md` / `product-scope.md`
- [ ] 编写 `docs/reaction-mechanism-viz-module.md`
- [ ] 容器内 E2E + 前端浏览器 E2E
- [ ] 反应库教师审核（draft→approved）
