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

---

## F5：可视化数据的机器可读 schema（2026-10-04晚）

### 发现的缺口

`interface-contract.md` §5 要求「为可视化数据指定 schema 版本」，
但实测发现**OpenAPI 覆盖不到 `viz_data`**：

```json
"VisualizationHint": {
  "properties": {
    "data": {"type": "object", "additionalProperties": true}
  }
}
```

即**对内部 12 个字段零约束**。后果：后端删掉 `coords`、
或改了 `atoms[].index` 的含义，OpenAPI 契约检测**全绿**，
前端却在运行时才炸。

### 为什么放在后端

权威来源是 `app/chem/engine.py`（它生产数据）。
把 schema 放在生产者旁边，才能在**同一个提交**里同时改
"产出什么"和"声明什么"——否则两份定义必然漂移。

### 双向验证（关键）

用**真实** `jsonschema.Draft202012Validator`（非手工查字段）：

```text
正确输出通过校验: True
版本不符     拒绝=True  定位=['schema']
缺必填       拒绝=True  定位=[]
未知状态     拒绝=True  定位=['conformer']
多余字段     拒绝=True  定位=[]
坐标2元组    拒绝=True  定位=['coords', 0]
radius字符串 拒绝=True  定位=['atoms', 0, 'radius']
负index      拒绝=True  定位=['atoms', 0, 'index']
全部坏数据都被拒绝: True
```

> **为什么必须用真实校验器**：手工写 `assert 'coords' in required`
> 只能验证"我想到的约束"，验证不了 schema 本身写得对不对。
> 实测踩过：手工检查全过，但 schema 里**少写一条约束**时无从发现。

### 三条 schema 检不出的风险

机械检查完备会给人错觉。这些是**语义漂移**，
schema 完全匹配而行为已变，故显式登记在 `VIZ_SCHEMA_NOTES`：

1. `radius` 单位是 Å——改成 nm 则球体大小全错。
2. `coords` 与 `render_atoms` 按索引一一对应——顺序不一致则原子会飘。
3. `order` 为 1.5 表示芳香键——改成整数枚举则苯环画不出交替单双键。

### 写测试时自己犯的一个错

断言 `assert "True" not in repr(schema)`，理由是「JSON 里应写 true」。
但 `additionalProperties: False` 里的 `False`
**本来就是合法 JSON Schema 关键字值**（"不允许额外字段"），
用 Python 的 `False` 才是正确写法。按字面搜会误报。

改为查"能否无损 round-trip"+"是否含 Python 的 `None`"——
真正该查的是Python 独有类型，不是 bool 字面量。

### 与前端的一致性

后端 schema 与前端 `src/viz/types.ts` 是**同一契约的两个投影**。
测试比对三件事：`VizData` 字段覆盖 schema 全部属性、
`ConformerStatus` 枚举值一致、两边声明同一版本号。

### 顺带确认：芳香环表示正确

实测苯环的 `order` 含 **1.5**，即**离域表示**。
这与用户此前确认的判断一致——**离域是对的**，
不应改成交替单双键。若哪天改成整数枚举，
苯环就画不出交替单双键，而那是高中必考的结构特征。
已加测试锁定这一点。

**测试：717 passed, 36 skipped**（schema 测试 33 项，零回归）


---

## 数字人形象与答复正文渲染（2026-10-05 凌晨）

### 起因：用户提出数字人联动构想

用户给出四类形态（基础形象/ 交互联动 / 特殊形式 / 轻量降级），
并明确指示：**去掉「指向分子」**、图片由本项目生成、
**Fay 未启用时前端默认待机**、启用后**多用 Fay 已有的 Action**。

### 先核实 Fay 的真实能力（未凭印象）

查得官方一手契约（飞书《数字人驱动接口(10002)》+ 社区实测）：

```json
{"Topic": "human", "Data": {
  "Key": "audio",
  "Lips": [{"Lip": "sil", "Time": 180}, {"Lip": "FF", "Time": 144}],
  "Sentiment": 0.7,
  "Action": {"behavior": "invite", "affect": "warm", "intensity": 0.74}}}
```

**三个与原构想不符的事实**：

1. **Fay 给的是音素时间轴，不是逐帧图片**——"说话"态不能靠整张图切换，
   得按 `Lips` 的音素驱动。我们用三态静态图近似，是**有意的降级**。
2. **`Action.behavior` 是人物动作**（挥手/点头/思考），
   **不含三维空间指向**。故"手势指向原子"确实做不了，已按用户指示去掉。
3. **10002 WebSocket 此前明确决定不接**（决策 A4 记载"本项目前端不驱动形象"）。
   现在要驱动形象了，该决策前提已变——已更新 A4。

### 抓到的第一个契约缺陷：靠字符串判断能力

`/health` 的 speech 组件 detail 形如 `"tts=就绪 fay=未启用"`。
前端要判断"数字人是否可用"，只能 `detail.includes('fay=就绪')`
——**把展示文案变成了契约**。文案一改（"就绪"→"可用"）前端静默失效。

修复：`ComponentStatus` 新增结构化 `caps: dict[str, bool]`。

**实测踩到第二层**：加了字段却在 `routes.py` 的 health 路由里**忘了透传**，
被 `default_factory` 静默填成 `{}`——探针层是对的，接口返回是空的。
**这类"加了字段但没接线"的缺陷不报错**，是本项目反复出现的类型
（`DOMAIN_MESSAGES` 缺失、`XUEZHI_AUDIO_DIR` 无人读、配置形同虚设）。

### 数字人降级链（三层，每层都实测）

| 情况 | 行为 |
| --- | --- |
| Fay 未启用 | **不建连接**，恒待机（用户明确要求） |
| 已启用但连不上 | 待机 + 提示"未连接数字人服务"，**只提示一次** |
| 已启用且连上 | 由 Fay 的 `Lips` / `Action.behavior` 驱动三态 |

动作映射**刻意宽松**：只认 `nod`/`invite`/`wave`/`confirm` → 说话，
`think`/`question` → 思考，**其余全部落待机**。
认不出的若猜成"说话"，学生会看到嘴在动但没声音。

**反向验证**：把兜底从 `idle` 改成 `speaking` 后测试立即失败。

### 顺手抓到的更严重缺陷：答复正文显示原始 Markdown

截图验证时发现主交付显示的是 `**羟基**` 与原始换行——
**这不是样式瑕疵，是内容不可读**。

实测扫了三个真实提问，模型产出的格式有四种：
**标题、引用、无序列表、粗体**（不含 HTML 标签）。

接入 `marked@18.0.14`（MIT），**自己写白名单消毒而不用 DOMPurify**：

- DOMPurify 是 20KB 依赖，且需要 `jsdom` 才能在 CI 跑
- 白名单规则**可审计**——能一眼看出允许什么
- 因为是枚举允许项，`<script>` / `onerror` / `javascript:` 全部被转义

**已诚实记录的局限**：正则消毒不处理属性值里的引号逃逸，
严格不如 DOMPurify 可靠。可接受的前提是输入为模型生成的受限Markdown。
**若将来允许用户自定义内容进正文，必须换成 DOMPurify**（已写入注释）。

### 写测试时自己犯的两个错

**① 断言写错了安全行为**：我写`expect(html).not.toContain('onerror')`，
但正确行为是**整个标签转义成文本**——字符串里仍会出现"onerror"这几个字母。
**断言必须是"没有可执行标签"，而非"文本里不含某关键词"。**

**② CSS 规则写了个不存在的元素**：我写 `br + br { display: none }`
试图折叠双换行，但查真实 DOM 发现**模型段落本就渲染成独立 `<p>`，根本没有 `<br>`**。
真正的间距主因是浏览器给 `ul` 的默认大margin。
> 截图看着"不对"时，**先查 DOM 再说**——我这次就是靠截图猜错了方向。

### 实测产出

- 数字人三态图（1024×1024 透明 PNG，用 Pillow 去白底，约 77% 透明）
- 分屏：grid + `grid-template-areas`，窄屏自动塌成单列
- **107 passed**（新增 20 项Markdown 测试 + 14 项数字人测试）
- 端到端截图验证：粗体正确渲染、段落分明、数字人 1024px 加载成功、状态 `idle`
