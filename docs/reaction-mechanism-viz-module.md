# 反应机理 3D 可视化模块（feat/reaction-viz-schema）

## 1. 目标与定位

为学生提供**断键/成键过程的 3D 动画**：不只说"这根键断了"，而是让他看见
原子怎么动、键怎么淡出、新键怎么长出来。

- **不替代文字讲解**：动画是**增强**，文本答复永远是主交付
  （`architecture.md` §6）。RDKit 缺失或反应未收录时，动画消失、讲解照常。
- **可信优先**：断键/成键位点来自**教师编审**的反应库
  （`status: draft → approved`），模型只决定"要不要展示动画"，
  不决定"动画里断哪根键"。
- **确定性生成**：帧由 RDKit 从反应库的 SMILES 确定性算出，
  **不依赖 LLM、不依赖向量库**。同一反应两次生成结果一致。

## 2. 方案 C（复用 Three.js）：为什么不引入 3Dmol.js

| 方案 | 取舍 |
| --- | --- |
| 引入 3Dmol.js | 开箱即用、省代码；但多引入 ~1MB 依赖，且其动画模型面向"分子间跳转"，不直接支持"同一套原子在帧间连续插值 + 单根键独立淡入淡出" |
| **复用现有 Three.js 栈（采用）** | 零新依赖、与单分子视图共用场景/主题/取景/球缓存；代价是帧插值逻辑要自己写 |

依据见 `reaction-mechanism-viz-plan.md` §4（决策 D-R1，已登记 `decision-register.md`）。

## 3. 后端结构（`backend/app/reaction/`）

| 文件 | 职责 |
| --- | --- |
| `schemas.py` | `reaction-library/v1`（静态定义）与 `reaction-mechanism/v1`（接口输出）。**只校验结构**，化学正确性由教研审核把关 |
| `library.py` | `ReactionLibrary`：加载 JSON 到内存；文件缺失或损坏**不致命**，返回空库并让端点降级 |
| `frames.py` | `build_frames`（纯插值，可脱离 RDKit 单测）与 `generate_mechanism`（调 RDKit 生成坐标） |
| `routes.py` | 单端点 `POST /api/v1/reaction/mechanism` |

### API 端点

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/v1/reaction/mechanism` | 给定 `reaction_id` 返回逐帧动画数据 |

**三种结果，三套语义**（三者都已由测试锁定）：

| 情形 | HTTP | 语义 |
| --- | --- | --- |
| 正常 | 200 + `ok=true` | `data` 为 `reaction-mechanism/v1` |
| 反应未收录 / 数据校验失败 | **200 + `ok=false`** | 学生的正常输入结果，不是服务故障；前端退回文本讲解 |
| RDKit 不可用 | **503** `api_component_unavailable` | 化学引擎缺失，与分子端点状态处理一致 |

> 刻意不把"未收录"做成 4xx/5xx：它与"问了个没收录的问题"是同一件事。

## 4. 数据模型要点

### 4.1 原子映射用 SMILES 的 `:n` 映射号

```json
"reactant_smiles": "[C:1]=[C:2].[Br:3].[Br:4]",
"product_smiles":  "[Br:3][C:1][C:2][Br:4]"
```

**为什么不用手写位置索引**：映射号直接写在 SMILES 里，review 时能一眼看出对错；
位置索引则会随原子顺序变动而静默错位。
⚠️ 映射号**必须加方括号** `[C:1]`——写成 `C:1` 会被 RDKit 当成未闭合的环标识而解析失败（实测）。

### 4.2 断键/成键"推导 + 标注"双轨

`breaking_bonds` / `forming_bonds` 是**可校验标注**，不是权威来源。
权威来源是"反应物结构 + 产物结构 + 映射号"的推导结果；
`scripts/validate_reactions.py` 会比对两者，不一致即报错（防手误）。

### 4.3 键状态基于**存在性**，不是键级

| 取值 | 判据 | opacity |
| --- | --- | --- |
| `intact` | 反应物与产物中**都存在**（键级可降） | 1 |
| `breaking` | 反应物中存在、产物中**完全消失** | 1 → 0 |
| `forming` | 产物中存在、反应物中**没有** | 0 → 1 |

> ⚠️ **最高频的错误**：把 C=C→C–C 标成 `breaking`。
> σ 键仍在，只是 π 组分消失——它是 `intact`，由 `order` 从 2 插值到 1 表现。
> 这条规则已实际抓到过一处数据标注错误（`addition-ethene-bromine`）。

## 5. 前端结构

| 文件 | 职责 |
| --- | --- |
| `frontend/src/viz/ReactionPlayer.ts` | Three.js 播放器 + 两个**纯函数** `interpolateAtomPositions` / `interpolateBondOpacities` |
| `frontend/src/components/ReactionMechanism.vue` | 界面编排：反应选择、播放控件、教学讲解、错误降级 |

### 为什么播放器不复用 `MoleculeViewer`

`MoleculeViewer` 面向**单个静态分子**：每次 `setMolecule` 都**重建**整个 group。
动画要跨 24+ 帧连续插值，逐帧重建会反复创建几何体、触发 GC 抖动。

故 `ReactionPlayer` **只在 `load()` 时建一次**持久网格，之后每帧只改
`mesh.position` 与 `material.opacity`。场景底色、主题跟随、`ResizeObserver`、
`OrbitControls`、高亮琥珀色、球缓存——全部从 `viewer.ts` 复用，
保证与单分子视图同一套视觉语言与资源释放纪律。

### 为什么插值数学要抽成纯函数

`applyProgress` 里的插值决定动画对不对（淡入淡出是不是反了、中点是不是对齐），
但它在 Three.js 私有方法里，happy-dom 下无法渲染、也就无法单测。
抽出纯函数后可在 vitest 里直接验，真实渲染交给浏览器 E2E
（与 `viz.spec.ts` 同口径）。

## 6. Agent 联动（P4）

机理类问题 → LLM 调 `find_reaction_mechanism` → 关键词匹配反应库 →
`reaction_id` 经 `visualization` 随答复下发 → 前端自动切到动画页。

**复用 `visualization` 字段而非新增字段**：该字段本就是为"随答复附带的可视化数据"
预留的，此前恒为空数组。另开字段等于把同一语义拆成两处契约。

匹配用**加权打分**（命中词按字符长度计分，取最高分）而非首条命中——
否则含"乙醇"的问法会被库里第一条通吃，与排列顺序绑定。

## 7. 降级矩阵

| 失效点 | 表现 | 保障机制 |
| --- | --- | --- |
| 反应未收录 | 不下发提示，留在文本讲解 | 工具返回 `found=false`，`_extract_reaction_hints` 返回 `None` |
| RDKit 不可用（本机 WDAC 拦截） | 动画端点 503，文本照常 | 端点 try/except 转 503 |
| 机理工具随化学工具一起降级 | 机理联动整体失效 | **工具注册在化学降级 try 块之外**，由 AST 结构测试锁定 |
| WebGL 不可用 | 提示三维不可用，可看文字讲解 | 播放器构造时探测 WebGL |
| LLM 不可用 | 动画仍可独立使用（学生自选反应） | 帧生成不依赖模型 |

## 8. 验证现状（2026-10-20）

| 项 | 状态 |
| --- | --- |
| 后端单测（`backend/tests/reaction/`，32 例） | 通过；含 AST 结构断言并已反向验证 |
| `scripts/validate_reactions.py`（真实 RDKit，3 条反应） | 全部通过 |
| 前端 vitest（188 + 6 例联动） | 通过；插值数学与 Agent 联动均有覆盖 |
| `vue-tsc --noEmit` | 零错误 |
| 容器内 E2E / 浏览器渲染 E2E | **待做** |
| 教师审核（`draft → approved`） | **待做** |

## 9. 已知限制

1. **动画不是真实反应轨迹**：坐标是 RDKit 生成的一种可能构象，
   在反应物与产物之间做**风格化插值**。不做 DFT/IRC 真实过渡态
   （`product-scope.md` §5）。
2. **反应库只有 3 条**，均为 `draft`；数量少是刻意的
   （质量优先于数量，呼应 D3/I5）。
3. **前端临时列了一份反应清单**（`ReactionMechanism.vue` 的 `REACTION_LIST`），
   因后端尚无 `/api/v1/reaction/list`；接入 list 端点后应改为请求所得。
4. **未收录时退回文本**，但没有"这个反应还没做动画"的显式提示——
   补这个提示需要 list 端点（见上一条）。

---

配套文档：`reaction-mechanism-viz-plan.md`（设计基线）、
`reaction-mechanism-viz-update-plan.md`（同步规划与阶段进度）、
`interface-contract.md` §3.2（schema 契约）。