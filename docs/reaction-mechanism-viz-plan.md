# 3D 分子断键/成键机理可视化 —— 实现计划

> 本文是**实现前评审基线**，描述在「学智有机」现有架构上新增
> **有机反应断键/成键 3D 机理动画**模块的设计、技术决策与算法。
> 当前仓库尚无此功能源码；本文所有结论须经实测/教师审核回填，
> 不使用猜测值（依据 `docs/development-workflow.md` §1.5 与 `decision-register.md` 规则）。
>
> 配套文档：`docs/reaction-mechanism-viz-update-plan.md`（更新规划，说明既有文档/流程如何同步）。
> 来源输入：用户附带的 `新建 Microsoft Word 文档.docx`（3D 断键成键可视化调研与改造方案）。

---

## 1. 背景与目标

`product-scope.md` §4 已把"对具备可靠反应模板/数据时生成结构变化和动画所需参数"
列为范围内能力；`architecture.md` §5 强调动画只消费**已验证且前端认识的结构化分子数据**。

本模块目标：当学生问及有机反应机理（如酯化、消去、加成、取代）时，
系统能呈现**反应物 → 过渡态 → 产物**的 3D 动画，直观展示**哪根键断裂、哪根键生成**，
并高亮反应位点。定位是问答/Agent 的**增强展示**，不是独立推理引擎。

设计原则（继承自现有架构）：
- 高中课程边界优先；动画呈现**工具校验过**的结构，不替代化学验证。
- 语音/数字人/动画均不阻断文本答复（可降级）。
- 复用现有 `chem/engine.py` 的构象能力，不重复造轮子。

---

## 2. 调研核验结论（联网核实）

用户文档中的关键断言已逐项核验，结果如下。

### 2.1 竞品与开源生态

| 产品/项目 | 核验结论 | 依据 |
| --- | --- | --- |
| **ChemTube3D**（利物浦大学） | ✅ 真实存在。JSmol 驱动，内置数百套有机机理动画，原生支持**断键淡出、成键生成、弯箭头、过渡态**；静态反应库，**无 AI 对话、无法接入 Agent**，二次集成困难 | chemtube3d.com、liverpool.ac.uk 官方页 |
| **MolView** | ✅ 真实存在。Web 2D→3D  sketcher/viewer，**仅有静态分子视图，无反应动态断键成键动画** | molview.com 官方页 |
| **3Dmol.js** | ✅ 真实存在。**BSD-3-Clause** 许可；WebGL；支持 xyz/pdb/sdf；`StickStyleSpec` 原生支持 `hidden` 与 `opacity(0–1)`；`addModelsAsFrames`+`animate` 原生帧动画 | 3dmol.org 文档、bio.tools、Rego & Koes 2015 (PMID 25505090) |
| **ChemViz3D** | ⚠️ **本次未独立核实**。用户文档称其为"面向教学的浏览器端 3D 反应动画、带关键帧编辑器、有教育机构授权限制不能打包进参赛项目"。该授权限制断言需后续由用户/法务核实，不可直接采信 | 仅用户文档提及 |
| **OrgoSolver** | ⚠️ **本次未独立核实**。用户文档称其为"2D 机理求解器，3D 断键成键动画薄弱"。同理待核实 | 仅用户文档提及 |

**核验要点**：几乎不存在"大模型教学 Agent + 动态 3D 断键成键可视化"的成熟开源结合体，
这正是本项目差异化的空间（与用户文档判断一致，已核实支撑事实）。

### 2.2 渲染引擎可行性

- **3Dmol.js 路线（用户文档 Route B）**：技术可行。`StickStyleSpec.opacity`/`hidden`
  直接控制单键显隐与透明度；多模型帧 + `animate({loop:"backAndForth", interval})`
  可原生播放反应物→产物过渡。**但会引入第二个 WebGL 渲染引擎。**
- **Three.js 复用路线（本项目现状）**：`frontend/src/viz/viewer.ts` 已基于 Three.js
  实现球棍/空间填充/线框三模式，`material.opacity`/`visible` 天然支持逐键透明度与显隐，
  且后端 `molecule-structure/v2` schema 已是它直接消费的形状。新增"帧序列 + 键状态调度"即可，
  **不引入新依赖、不新增渲染栈**。

### 2.3 RDKit 能力（本项目已具备）

- 自 2024.03 起默认 **ETKDGv3**；`Chem.AddHs → AllChem.EmbedMolecule → AllChem.MMFFOptimizeMolecule`
  可生成 3D 坐标；`chem/engine.py` 已固定随机种子 `0xF00D` 保证可复现构象。
- 反应物→产物**原子映射**：`AllChem.ReactionFromSmarts` 带原子映射号时，
  产物原子携带 `old_mapno`/`react_atom_idx`/`react_idx` 属性；亦可 `rdFMCS.FindMCS` 求最大公共子结构映射。
  → 对**预定义的高中反应库**，在反应定义里直接存显式原子映射号是最确定、最省力的方案。

---

## 3. 推荐架构方案（贴合现有架构）

```
学生提问(机理类)
   │
   ▼
Agent / 路由判定 → 命中反应库 reaction_id
   │
   ▼
POST /api/v1/reaction/mechanism  { reaction_id }
   │
   ▼
backend/app/reaction/  (新模块)
   ├─ library.py   读取 data/reactions/*.json (reaction-library/v1)
   ├─ frames.py     用 RDKit 生成 反应物/产物 3D 坐标(复用 chem 构象)
   │                 + 显式原子映射 + 线性/样条插值 → 帧序列
   └─ schemas.py    reaction-mechanism/v1
   │
   ▼
前端 ReactionMechanism.vue + viz/ReactionPlayer.ts
   ├─ 复用 viewer.ts 的 Three.js 渲染能力
   └─ 按帧驱动：原子位置插值、键 visible/opacity 调度、反应位点高亮
```

**为何选 C 而非另起 3Dmol.js**：本项目可视化层已统一为 Three.js（`viewer.ts` 有详细选型理由，
且 `molecule-structure/v2` 已是它的输入）。再引入 3Dmol.js 等于维护两套 WebGL 渲染、
增大包体、分裂动画代码路径。**已决策：选 C 复用 Three.js、舍弃更简便的 3Dmol.js**——
其中记录了"曾评估并放弃"该捷径的完整取舍依据（含已联网核实的滚轮反向/限位标定/配色不一致
等具体代价），详见决策 D-R1（2026-10-20 定稿）。

---

## 4. 关键技术决策（含待确认项）

> 下列 D-R* 为需用户/负责人确认的决策，登记方式见更新规划。

### D-R1 渲染引擎：复用 Three.js（决策：选 C，2026-10-20）

- **最终决策：选 C —— 复用现有 Three.js 渲染栈、扩展帧动画；明确舍弃更简便的 3Dmol.js。**
- **本决策刻意记录了"曾考虑又放弃"的更优捷径**：3Dmol.js 是分子可视化专用库（BSD-3-Clause），
  在"断键/成键帧动画"上确实更省事（原生 `addModelsAsFrames`+`animate`、键 `hidden`/`opacity`
  原生控制）。本项目**评估过**这条更简便的路线，但基于 C 方案的复利优势**未采纳**，理由如下。
- **三方案取舍**（依据代码事实 + 联网核验，非凭印象）：
  - **A 全量替换为 3Dmol.js**：❌ 否决。本项目 `frontend/src/viz/viewer.ts`（646 行）已是基于
    Three.js 调校好的球棍/填充/线框渲染器，其每行注释都是一次实测踩坑（透明背景变黑斑、截图空白、
    `OrbitControls` 漏导入、`lookAt` 与 up 共线翻转、几何缓存共享释放、主题底色两处复刻）。全量替换
    等于丢弃已付清的沉没成本，仅为绿地功能重写已上线模块，代价高、收益窄。
  - **B 双引擎并存**（现有 Three.js + 新模块用 3Dmol.js）：⚠️ 可行但未被选。3Dmol 在"帧动画"上
    人体工学更优，**但已联网核实四项具体代价**：
    ① 滚轮缩放方向与本项目 `OrbitControls` **相反且不可配置项翻转**（多个中文开发者独立确认）；
    ② 缩放限位按相机距离算，而分子大小不一（乙醇 ~4Å vs 大甾体），需逐分子标定；
    ③ 包体为全家桶（自带解析器+渲染器），须动态 `import()` 懒加载以免膨胀；
    ④ 两引擎着色/键半径不同，须抽共享配色常量。四项均可遏制，但"长期养两个引擎"的成本易被低估。
  - **C 复用 Three.js 手写帧插值**：✅ 选定。零新依赖、零视觉不一致、复用全部调试经验；
    反应是"逐帧坐标 + 键状态（`visible`/`opacity`）调度"，本质是在现有 `viewer.ts` 的 rAF 循环上
    加一层时间线，**不重写分子解析**（后端 RDKit 仍生成反应物/过渡态/产物坐标，前端只做帧间 tween）。
- **C 被选中而非 B 的真实硬优势**（经代码核对，非转述）：
  - 叠加物**直接挂进现有 `moleculeGroup`**，自动继承缩放/相机/轨道控制；键渐变、箭头运动、暂停节点
    **共用同一个 rAF 时间线**——这是 3Dmol 的 `animate` 范式难以低成本实现的。
  - 主题/释放/截图/交互基础设施全复用；**不引入第二个 WebGL 引擎** → 无包体膨胀、无滚轮反向、无配色不一致。
  - 面向**交互式教学智能体**的长期复利：电子转移箭头、轨道示意、过渡态自动暂停弹知识点、势能曲线/振动/
    碰撞仿真等"跨范式教学仿真"正是 Three.js 擅长、传统分子软件薄弱的方向。
- **两处必须纠正的转述偏差**（已用代码 + 联网核实）：
  - 材料称"现有 viewer 已写好原子拾取，C 直接复用"——**言过其实**：`viewer.ts`/`MoleculeViewer.vue`
    无 `raycaster`、无点击→原子映射，"拾取"只是带 `userData={element,index}` 与 `highlight(index)`
    的**未接线脚手架**。C 仍要补 raycaster（增量 ~30–50 行），但能直接挂进现有渲染循环。
  - 材料称"3Dmol 难低成本叠加自定义三维元素"——**言过其实**：3Dmol 实测有 `setClickable`/
    `setHoverable`（原子点击一行搞定）+ `addArrow`/`addLabel`/`addCylinder`。**静态/简单叠加做得到**；
    只有"随原子运动、深度正确、与键透明度同步"的复杂动画叠加才更别扭。别把"难"说成"不能"。
- **电子转移箭头典型案例（B vs C）**：静态指一下 → B 的 `addArrow` 一行完胜；
  但本项目目标是**动画版**（先出现→沿曲线游走→与新键同步淡入、随分子旋转保持深度正确），
  此时 C 的"同场景图 + 同时间线"优势成立，B 反而要绕过自身帧范式、自写 rAF 循环——故材料对该
  案例的倾向在"动画版"前提下成立。箭头若设为 `QuadraticBezierCurve3`+`TubeGeometry`+锥头，是
  Three.js 原生曲线，复用现有 `animate()` 时钟即可与键透明度天然同步。
- **决策影响**：前端反应可视化零新依赖、与现有分子查看器 100% 视觉/交互一致；后端 `reaction-mechanism/v1`
  契约与 `molecule-structure/v2` 对齐的设计不变（三方案共用，与"是否换引擎"无关）。

### D-R2 反应来源：预定义高中反应库 vs 自动预测断键位置
- **推荐**：**预定义**。`data/reactions/` 下每个高中重点反应存
  `reactant_smiles / product_smiles / 显式原子映射 / breaking_bonds / forming_bonds / 过渡态说明 / 知识点`。
  与 `decision-register.md` 中"不做任意分子自动预测断键"的避坑原则一致；
  也契合用户文档"高中反应池有限，预定义断键索引大幅降难度"。
- **不推荐**：通用化学反应预测（属于化学 AI 难题，超出创新赛范围）。

### D-R3 过渡态表示
- **推荐**：**风格化过渡帧**（断裂键用虚线/半透明、生成键半透明），不做 DFT/IRC 量子化学计算。
  与用户文档"避坑：勿对标 webORA 做 DFT"一致，且高中教学无需真实势能面。
- `conformer` 字段沿用 `molecule-structure/v2` 的 `ready/failed` 语义：过渡帧坐标为风格化插值，
  **不宣称经过量子化学验证**。

### D-R4 原子映射方式
- **推荐**：反应库内显式存原子映射号（确定性、可审阅、可单测）。
- 不依赖运行时 `rdFMCS` 自动推断（自动推断在断键导致图结构剧变时易错，且不可复现）。

---

## 5. 数据 schema

### 5.1 反应库 `reaction-library/v1`（静态、project-authored）
与 `knowledge-corpus/v1`、`quiz-bank/v1` **同级治理**，置于 `data/reactions/`。
```jsonc
{
  "schema": "reaction-library/v1",
  "index_version": "reaction-2026.10",
  "reactions": [
    {
      "id": "esterification-ethanol-aceticacid",
      "name": "乙酸与乙醇酯化",
      "topic": "酯化反应",
      "reactant_smiles": "CCO.CC(=O)O",
      "product_smiles": "CC(=O)OCC.O",
      "atom_map": { "0": 0, "1": 1, "...": "..." },   // 显式 反应物原子idx → 产物原子idx
      "breaking_bonds": [[7, 8]],                       // 断裂键(反应物原子对)
      "forming_bonds":  [[1, 9]],                       // 生成键(产物原子对)
      "transition_note": "羧基 C–O 键断裂，同时酸脱羟基醇脱氢",
      "related_chunk_ids": ["org-esterification-1"]
    }
  ]
}
```
> `status` 同 quiz 库：`draft`/`approved`；**未经化学教师审核不得标 `approved`**。

### 5.2 帧序列 `reaction-mechanism/v1`（接口输出）
每帧复用 `molecule-structure/v2` 的原子/键/坐标结构，额外加**键状态**与高亮：
```jsonc
{
  "schema": "reaction-mechanism/v1",
  "reaction_id": "esterification-ethanol-aceticacid",
  "frame_count": 24,
  "fps": 12,
  "atom_map": { "0": 0 },
  "frames": [
    {
      "t": 0.0,
      "atoms": [ {"index":0,"element":"C","x":0.1,"y":0.2,"z":0.3}, ... ],
      "bonds": [ {"a":0,"b":1,"order":1,"state":"intact"},
                 {"a":7,"b":8,"order":1,"state":"breaking","opacity":0.9},
                 {"a":1,"b":9,"order":1,"state":"forming","opacity":0.1} ],
      "highlight_atoms": [7, 8]
    }
    // ... 过渡帧 state 渐变、opacity 渐变；末帧 breaking 键 opacity→0、forming 键 opacity→1
  ]
}
```
> `bonds[].state` 取值：`intact` / `breaking` / `forming`；`opacity` 由前端按帧插值驱动。
> 坐标单位 Å，与 `molecule-structure/v2` 一致（实测约束见 `interface-contract.md` §3.1）。

---

## 6. API 端点设计

对齐现有 `POST /api/v1/molecule` 的契约风格（单一错误事实源、用户错误返回 200+`ok=false`）：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/v1/reaction/mechanism` | 入参 `{ reaction_id }` → 返回 `reaction-mechanism/v1` 帧序列 |

- 入参 `reaction_id` 不在库中 → `200 + ok=false`（`reaction_not_found`，可重试=否），不抛 5xx。
- RDKit 不可用时 → `503 + api_component_unavailable`（复用 `classify` 体系，与 molecule 端点一致）。
- 不依赖 LLM：**模型不可用时该端点仍可用**（确定性 RDKit 计算），符合降级设计。
- 注册方式：新建 `backend/app/reaction/routes.py`，在 `app/api/app.py` `include_router`。

---

## 7. 帧生成与插值算法（backend/app/reaction/frames.py）

1. 由 `reaction_id` 取反应定义；`MolFromSmiles` 反应物/产物，`AddHs`。
2. 各自 `EmbedMolecule`(固定种子) + `MMFFOptimizeMolecule`（复用 `chem/engine.py` 构象函数）。
3. 按 `atom_map` 对齐两构象原子顺序；对新增原子（如加成进来的 H）在产物帧淡入。
4. 在 `frame_count` 帧间对每原子 xyz **线性/样条插值**；
   断裂键 `opacity` 由 1→0、生成键由 0→1；中间若干帧两键均半透明（过渡态观感）。
5. 高亮集合 = `breaking_bonds ∪ forming_bonds` 端点原子，前端按 `highlight_atoms` 着色。
6. 输出 `reaction-mechanism/v1`。

> **不引入真实过渡态几何**：过渡帧坐标为风格化插值，前端明确标注"示意"，不宣称经量子化学验证。

---

## 8. 前端动画控制器（frontend/src/viz/ReactionPlayer.ts + ReactionMechanism.vue）

- 复用 `viewer.ts` 的球棍渲染、`dispose` 资源管理（避免显存泄漏，见 `viewer.ts` 注释）。
- 新增 `ReactionPlayer`：按 `fps` 推进帧，逐帧更新原子位置 + 键 `material.opacity`/`visible` + 高亮色。
- 播放控制：播放/暂停/单步/慢放（断键瞬间 0.25x，呼应教学要点）、镜头锁定反应位点（局部放大、其余虚化）。
- 与 Fay/Edge-TTS 协同：数字人讲解机理时，页面同步播放该动画（不阻断文本）。

---

## 9. 工作量与难度（参照用户文档评估）

- **推荐路线（Three.js 复用）**：⭐⭐⭐（中等）。RDKit 构象与 Three.js 渲染均已具备，
  主要新增量是"反应库 + 帧生成 + 键状态调度"。
- **3Dmol.js 路线**：开发更省，但引入双渲染栈（见 D-R1）。
- **DFT/IRC 路线**：⭐⭐⭐⭐⭐，算力巨大、远超创新赛范围，**明确不做**。

---

## 10. 验收与测试

- **契约测试**：`reaction-mechanism/v1` 入 OpenAPI，新增契约测试（含坏数据反向验证，
  参照 F4 / F5 模式）；导出 `docs/api/openapi.json --check` 供 CI。
- **纯逻辑单测**（不依赖 RDKit 容器）：schema 校验、原子映射解析、`opacity` 调度曲线单调性。
- **E2E（容器内）**：`/health` + `/api/v1/reaction/mechanism` 实测；
  前端在浏览器层验证抽帧/播放/慢放/高亮（参照 quiz 模块的 E2E 待办写法）。
- **教学审核**：反应库 `approved` 须经化学教师核对**断键/成键位点与高中教材一致**
  （呼应 D3 内容审核要求、I5 官能团口径一致原则）。
- 准确率/延迟/可用性阈值依 `decision-register.md` C1–C6，**由教师/负责人实测后批准，不预设数值**。

---

## 11. 风险与降级

| 风险 | 缓解 |
| --- | --- |
| RDKit 受本机应用控制策略拦截 | 沿用既有方案：**容器内运行完整后端**（决策 H20），动画帧由后端生成 |
| 反应库覆盖不全 | 仅对 `approved` 反应提供动画；未收录反应不强行生成，退回文本讲解 |
| 前端动画卡顿/显存泄漏 | 复用 `viewer.ts` 的 `dispose` 递归释放；限制帧数与原子数 |
| 模型/Agent 不可用 | 动画端点不依赖 LLM，独立可用；Agent 仅作"是否触发"的调度 |

---

## 12. 待核实/待办

- [x] D-R1 渲染引擎最终选型：**已决策选 C（2026-10-20）**——复用现有 Three.js 栈手写帧动画，明确舍弃更简便的 3Dmol.js（取舍依据见 D-R1 正文与 `decision-register.md` D-R1 登记）。
- [ ] ChemViz3D / OrgoSolver 授权与能力独立核实（若后续考虑借鉴设计）。
- [ ] 反应库首批覆盖范围（建议从课标四大模块高频反应起步，经教师核定）。
- [ ] 过渡态风格化呈现是否需与教材示意图对齐（教师审核）。
