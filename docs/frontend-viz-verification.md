# 3D 分子可视化：实现与实测记录

> 模块：`frontend-viz` ｜ 分支：`feat/frontend-viz-3d-molecule`
> 基线 commit：`3448ea6`
> 实测日期：2026-10-04

本文件记录 3D 分子视图的实现依据、**实测数据**，
以及一个必须记录在案的判断：**本模块依赖后端补齐数据才成为可能**。

---

## 1. 起因：实测发现后端数据缺口

按 `development-workflow.md` 的模块划分，`frontend-viz` 应在 `frontend/src/viz/`。
动手前先实测后端返回什么，得到：

```json
{
  "schema": "molecule-structure/v1",
  "smiles": "CCO",
  "atom_count": 3,
  "bond_count": 2,
  "conformer": "none",
  "conformer_note": "尚未生成三维坐标，前端需自行构象生成。"
}
```

**只有计数，没有原子与键的任何数据。** 不知道原子在哪、不知道键连的是谁。

原注释说「前端需自行构象生成」，但**让前端从 SMILES 反推结构等于
重新实现一遍化学信息学**（SMILES 解析、环感知、立体化学、构象搜索）。

**这不是设计取舍，是数据没给。** 故先做后端前置改动
（`feat/chem-viz-atom-bond-data`，已合入 main），schema v1 → v2。

### 为什么坐标生成放在后端

实测构象生成耗时（容器内，含H + ETKDG + MMFF 优化）：

| 分子 | 原子数 | 嵌入耗时 |
| --- | --- | --- |
| 乙醇 CCO | 9 | 17 ms |
| 苯酚 | 13 | 2 ms |
| 乙酸 | 8 | 1 ms |
| 乙酸乙酯 × 10 次 | — | 共 11 ms |

**平均 1–17 ms，同步返回完全可行**，不需要异步任务或前端算法。

### 三个刻意的设计决定

**① 加显式氢。** 高中教学必须看到 C 上的 H，否则学生数不出 CH₃ 的四个键。

**② 固定随机种子**（`0xF00D`）。ETKDG 本身含随机性，不固定的话
学生看到「同一个乙醇变成两个样子」，也无法对照反应前后的构象变化。
测试 `test_coordinates_are_reproducible` 锁定。

**③ 原子半径取 RDKit 的 `GetRcovalent`**，不硬编码。
那份表来自实验数据；硬编码等于用自己的近似替换权威数据。
测试断言具体数值（C=0.76 Å、O=0.66 Å）。

---

## 2. Three.js 而非 Molstar

架构文档两者都列了。实测考量：

| | Molstar | Three.js（选用） |
| --- | --- | --- |
| 生态定位 | 结构生物学（PDB 蛋白结构） | 通用 3D |
| 输入假定 | PDB/mmCIF | 任意几何数据 |
| 本场景契合度 | 用挖掘机挖花盆 | 球 + 圆柱两类几何体即可 |

我们的输入是**单个有机小分子的 SMILES**，Molstar 的优势（实验数据密度图、
大分子装配）完全用不上。

**若将来要展示蛋白质或晶体结构，应重新评估** —— 那是 Molstar 的主场。
已记入 §7 的复评条件。

版本 `three@0.186.1`（`npm view` 实测）。r180+ 移除了大量废弃 API，
`outputEncoding` 已被 `outputColorSpace` 取代 —— 代码用后者。

---

## 3. CPK 配色：取 Jmol 权威表，不自选

配色取自 **Jmol 官方配色表的 CPKnew 列**（`biomodel.uah.es/Jmol/colors`）。

**不用原版 CPK 的纯黑/纯白**：那个配色在网页上几乎不可读 ——
碳是纯黑 `#000000`，键也是深色，学生根本看不清键连的是谁。
Jmol 把 C 改成中灰 `#909090`，是社区实践验证过的可读版本。

| 元素 | 颜色 | 元素 | 颜色 |
| --- | --- | --- | --- |
| H 氢 | `#FFFFFF` | O 氧 | `#FF0D0D` |
| C 碳 | `#909090` | S 硫 | `#FFFF30` |
| N 氮 | `#3050F8` | P 磷 | `#FF8000` |
| Cl 氯 | `#1FF01F` | Br 溴 | `#A62929` |

未登记元素回落**粉色**（`#FF69B4`）而非 `undefined`——
`undefined` 传给材质会得到黑色，看起来像「这个原子不存在」。

---

## 4. 包体积：实测数据与代码分割

静态引入 Three.js 的后果（实测）：

```
主包  92 KB (gzip 36 KB)  →  634 KB (gzip 173 KB)
```

**+542 KB**，而多数会话只查官能团、不看 3D。让所有人先下载 540 KB
只为一个可能不看的功能不合理。

改为 `defineAsyncComponent` 按需加载后：

```
index.js                95.65 kB │ gzip: 37.60 kB   ← 主包（+3 KB）
MoleculeViewer.js      542.00 kB │ gzip: 136.42 kB  ← 懒加载
```

代价是首次打开有加载延迟。用组件内的提示如实告知，不让界面卡住不给反馈。

---

## 5. 四处实测踩坑（凭直觉写一定错）

### ① `preserveDrawingBuffer` 必须开

默认配置下 canvas 内容在每帧结束后被清空，`toDataURL()` 只能拿到空白图。
**页面里明明画出了分子，截图却是全空。**

代价是轻微的性能损失（浏览器无法丢弃缓冲）。已开启并在注释里说明。

### ② `lookAt` 在视线与 up 平行时失效

相机对准分子中心时，若位置与目标点的连线恰好与 `up` 向量平行，
`lookAt` 会**完全失效**（画面随机翻转）。

这是 Three.js 的经典坑。解法：x 方向偏移一个极小量（0.001）。

### ③ `Object3D` 基类没有 `geometry` / `material`

释放资源时写 `child.geometry.dispose()` 类型报错 ——
`geometry` 是 `Mesh` / `Line` 的属性，不是基类的。
必须先 `instanceof` 判断。

**漏掉判定的后果不是编译错误，而是运行时 `undefined.dispose()` 崩溃
或显存泄漏**（表现为「切几次分子后画面卡住」）。

### ④ 分支基线落后于 main

第一版 `feat/frontend-viz-3d-molecule` 是在 I6 修复**之前**创建的，
导致容器里跑的是 v1 数据（返回 `conformer: "none"`）。

**我一度以为是 Docker 缓存问题，重建容器仍不变。**
真正原因是分支基线 —— 这与之前记录的「改了仓库文件必须重建镜像」
同源：**验证环境与代码不同步时，先查基线，别急着怀疑工具**。

---

## 6. 测试

### 单元测试（25 项，`npm test`）

`tests/viz.spec.ts` 覆盖三类：

| 类别 | 项数 | 覆盖 |
| --- | --- | --- |
| CPK 配色 | 6 | 权威值、高中元素齐备、未登记回落 |
| 球棍几何 | 2 + 6 | 球/键比例、键数（单/双/三/芳香/异常值） |
| 几何计算 | 4 | 中心、跨度、空输入、单原子（除零） |
| 数据校验 | 7 | conformer 失败、索引错位、NaN/Infinity、缺字段 |

**不测「渲染出的像素是否正确」** —— 那需要 WebGL 与截图比对，
脆弱且难维护。真实渲染由 E2E 覆盖。

### 端到端验证（11 项，`node e2e/verify-viz.mjs`）

用**真实 Chromium**（带 `--use-gl=swiftshader` 软件渲染，
因为无 GPU 的环境也能跑）。**实测 11/11 通过**。

覆盖：WebGL canvas 创建、**canvas 实际绘制了内容**（非空白）、
原子数与后端一致、三种显示模式切换、关闭氢、自动旋转产生帧差、
多次切换分子无泄漏症状、控制台无错误。

**关键断言是「canvas 实际绘制了内容」**：
canvas 存在不等于画出了东西 —— 空场景也有 canvas。
该断言通过读 `toDataURL` 的像素统计非透明比例。

---

## 7. 已知缺口与复评条件

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 芳香环交替单双键 | **部分** | 后端给 1.5 键（离域），前端画一根细圆柱 + 内侧标记。未画标准的交替双键 |
| 分子旋转交互 | **未实现** | 目前只能自动旋转，**不能鼠标拖拽**。缺 `OrbitControls` |
| 原子点选 | **未实现** | `userData` 已挂元素信息，但未接拾取射线 |
| 截图导出 | 已实现未验证 | `snapshot()` 存在，未接 UI 按钮 |
| 移动端 | 未测 | 只在 1280×1000 验证 |
| **若展示蛋白质/晶体结构** | **须重新评估** | 那是 Molstar 的主场（见 §2） |

### 复评 Molstar 的触发条件

出现以下任一情况时，应重新评估技术选型：
1. 需要展示蛋白质、核酸等**大分子**；
2. 需要读取 **PDB/mmCIF** 等实验数据格式；
3. 需要 **实验数据密度**（分辨率楔形）表达。

单个有机小分子的球棍模型，Three.js 是更合适的工具。

---

## 8. 复现命令

```bash
# 1. 后端（**必须用容器**——本机 RDKit 被应用控制策略拦截，无 3D 数据）
cd F:/Xuezhi-OrganicAgent
docker run -d --name xuezhi-viz-backend -p 8133:8000 \
  -e MAAS_API_KEY=sk-fake -e XUEZHI_RATE_LIMIT_RPS=1000 \
  -e XUEZHI_RATE_LIMIT_BURST=1000 \
  -v "F:/Xuezhi-OrganicAgent/backend:/work/backend" \
  -v "F:/Xuezhi-OrganicAgent/data:/work/data" \
  --workdir /work xuezhi-chem-test \
  python -m uvicorn app.api.app:app --host 0.0.0.0 --port 8000 --app-dir backend

# 2. 前端
cd frontend
npm install
unset https_proxy http_proxy HTTPS_PROXY HTTP_PROXY   # 必须
export no_proxy="127.0.0.1,localhost" NO_PROXY="127.0.0.1,localhost"
VITE_BACKEND_URL=http://127.0.0.1:8133 npm run dev     # http://127.0.0.1:5180

# 3. 验证
node e2e/verify-viz.mjs
```

> **本机无法验证 3D**：RDKit 的 C++ 扩展被应用控制策略拦截，
> `/api/v1/molecule` 返回 503。故 E2E 必须在容器后端下跑。
> 这也意味着**演示机的环境必须先确认**（见决策登记表 I7）。
