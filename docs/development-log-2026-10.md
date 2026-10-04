# 开发留痕：首批代码模块实施记录

日期：2026-10-04
范围：从文档基线到五个后端模块落地
分支：`feat/knowledge-organic-chemistry-corpus`（合并前状态）

## 1. 为什么有这份文档

本项目的工程纪律要求「一切修改都写文档、工作留痕」。本文记录
**首批代码实施过程**中那些不在代码注释里能体现的内容：为什么这样做、
哪些判断被实测推翻、哪些坑踩过、以及**过程中的失误**。

代码与提交信息说明"做了什么"，本文说明"为什么"和"哪里出了问题"。

## 2. 提交清单（14 个，按时间顺序）

| # | 提交 | 内容 |
| --- | --- | --- |
| 1 | `d28fceb` | 实现化学引擎，记录验证受阻原因 |
| 2 | `7d216eb` | 登记容器化测试环境排查结论（H14） |
| 3 | `aefbd23` | 记录三条绕行路径的实测结果（H12/H14） |
| 4 | `e397f96` | 在 Docker 容器中完成验证并修正四处判断错误 |
| 5 | `d184850` | 实现模型适配层，91 项测试 |
| 6 | `16495b8` | 记录 MaaS 真实连通性实测结果（B4） |
| 7 | `d3451f6` | 完成 MaaS 三项实测并记录四项协议发现 |
| 8 | `4baff92` | 实现工具调度层并完成真实端到端验证 |
| 9 | `a87058c` | 实现知识检索层，发现中文 embedding 阻塞项 |
| 10 | `5c05687` | 接入中文嵌入模型 BAAI/bge-small-zh |
| 11 | `cd0299f` | 完成中文检索质量实测，解除 H17 阻塞 |
| 12 | `2abb73c` | 接入知识检索工具并修复工具选择混淆 |
| 13 | `72c60e3` | 实现教材切分、语料准入与检索质量评估 |
| 14 | `a48b453` | 以自编讲义替代教材原文，产出 41 条有机化学语料 |

变更规模：59 个文件（55 新增 / 4 修改），+11572 行。

## 3. 实施过程中被实测推翻的判断

这一节是本文的核心。**以下每一条我都曾"以为"是对的，
实测证明是错的。**记录下来是为了避免重复。

### 3.1 RDKit API：`Mol.GetSmarts()` 不存在

写化学引擎时我"记得"RDKit 有这个方法，用于还原 SMARTS 文本。
真实调用报 `AttributeError`，`dir()` 查无此法——**是我凭印象编的**。

修正：改为保留 `_FUNCTIONAL_GROUPS` 表中的原始 SMARTS 文本，
编译结果改为 `(名, 原文, Mol)` 三元组。

首轮测试 28 项失败，4 项同源于此类凭记忆假设。

### 3.2 RDKit 行为：三个"常识"其实错了

| 我的假设 | 实测事实 |
|---|---|
| 单个 `"C"` 是非法 SMILES | **合法**——是甲基自由基，formula=CH₄ |
| `CC#N` 可用于测碳碳三键 | **是氰基**（C≡N），不含碳碳三键；须用乙炔 `C#C` |
| 乙醇 `GetNumAtoms()` 是 9 | **是 3**——该方法返回显式原子数，不含隐式氢 |

第三条已在 `models.py` 的字段注释中写明语义，避免使用者误解。

### 3.3 LangChain：与 OpenAI 原始 SDK 结构完全不同

这是影响最大的一次。按 OpenAI 规范写了 dispatcher，
真实调用**全部失败**：

| | OpenAI 原始 SDK | **LangChain `bind_tools`（实测）** |
| --- | --- | --- |
| 返回值 | 响应对象含 `choices[0].message` | **`AIMessage`，没有 `choices`** |
| tool_call 元素 | 对象 `call.function.name` | **dict `{"name","args","id","type"}`** |
| `args` 类型 | **JSON 字符串** | **已解析的 dict** |
| assistant 回填 | 需手工构造 dict | **`AIMessage` 可原样回传** |

三个假设全错：按规范写的 `response.choices[0].message`、
`call.function.name`、`json.loads(arguments)`。

已记入决策登记表 A1-note，并确立规则：**写 LangChain 代码前先
真实调用一次并 dump 结构**。

### 3.4 sentence-transformers：API 改名且不报错

`get_sentence_embedding_dimension` 已改名 `get_embedding_dimension`。
旧名**仍然可用，只发 `FutureWarning`**——测试显示"全部通过"。

只有开 `-W error::FutureWarning` 才暴露。**若只看测试通过就放过，
这个过时 API 会潜伏到未来版本移除才炸。**

已改为新名 + `getattr` 兼容旧版，并确立规则：
**跑依赖库测试时加 `-W error::FutureWarning`**。

### 3.5 chromadb：内置 embedding 是英文模型，中文排序完全颠倒

查证 chromadb 1.5.9 内置 `DefaultEmbeddingFunction`，
实测 cosine 空间查「酯化反应」：

| 文档 | 距离 | 实际名次 |
| --- | --- | --- |
| 酯化反应是酸和醇生成酯和水 | **0.8358** | 第 3 ❌（应该第 1） |
| 今天天气很好 | **0.4943** | 第 1 ❌（应该第 3） |

**排序完全颠倒。** 换英文 query 后排序正确，确认为模型语言问题。
这一条直接催生了 H17 阻塞项，并促成改用 `BAAI/bge-small-zh`。

同批查证还发现：默认距离度量是 `l2` 非余弦；内置 EF 首次调用需
联网下载 79.3MB ONNX 模型（断网演示会失败）。

### 3.6 openPangu 协议：tool_choice 不支持指定具体函数

传 OpenAI 规范的 `{"type":"function","function":{"name":...}}` 报
`ModelArts.81001: The Pangu model supports only "none", "auto", and "required"`。

**这是与 OpenAI 规范的差异**，无法强制指定某函数。
工具收敛只能靠白名单 + 提示词引导——反而让
`architecture.md` §5 的白名单机制显得更必要。

另一发现：模型**默认开启深度思考**，返回 message 含
`reasoning_content`。设 `max_completion_tokens=200` 会导致
正文完全为空（token 被思考占用）——这曾让我一度误判为 API 故障。

### 3.7 切分策略：语义切分实测更差

联网核实（详见 `knowledge-base-implementation.md` §3）：
FloTorch 2026 基准（50 篇论文、90 万 token）端到端准确率——
递归字符切分 512 token = **69%**，固定大小 = 67%，
**语义切分仅 54%**（产出 43 token 碎片，检索中但答不出）。

**我原本倾向语义切分**（听起来更先进），实测数据推翻了这一倾向。

### 3.8 教材版权：把他人作品用于 AI 知识库不构成合理使用

联网核实到的明确立场：台湾高校图书馆与经济部知识产权局均指出
未经授权将电子书用于知识库建置超出合理使用范围。

**这直接改变了方案**：从"引入教材"改为"自编讲义"。
详见 `organic-corpus-verification.md` §2。

## 4. 本机环境的两个硬约束

### 4.1 Smart App Control 拦截未签名二进制

Windows Smart App Control 已启用，按**发布者签名**拦截二进制：

| 扩展 | 签名 | 加载 |
| --- | --- | --- |
| numpy / scipy / Pillow | 有 Microsoft 签名 | ✅ |
| **rdkit / grpcio** | **NotSigned** | ❌ 被拦 |

事件日志 3077 给出确切原因：RDKitSubstructMatch-*.dll
不满足 Enterprise signing level。

**关键结论：能否加载原生扩展取决于 Python 的**发行来源**，不是版本。**
同为 3.12.14：uv 发行版可用，conda-forge 环境连标准库 `_ctypes` 都被拦。

依次尝试了三条绕行路径（conda 环境、Docker、关闭 SAC），
前两条失败（详见 `chem-engine-verification.md` §2.3），
**最终由用户完成 Docker Desktop 的 WSL2 后端初始化解决**——
容器内二进制不受主机策略管辖。

**处置原则：没有关闭 Smart App Control，也没有为绕过策略在产品代码
里加任何 hack。** 由项目负责人指定测试环境是正确的做法。

### 4.2 网络：直连 30 kB/s，必须走代理

同一下载目标实测：直连 28.7 kB/s，代理 `127.0.0.1:7897` 达 1.02 MB/s，
**相差 35 倍**。直连条件下完整安装 30 分钟未完成，走代理 4 分钟完成。

依赖安装须显式加 `--proxy http://127.0.0.1:7897`。
HuggingFace 直连不通（HTTP 000），bge 权重须代理下载。

## 5. 过程中的失误（留痕以避免重复）

| 失误 | 影响 | 已确立的规则 |
| --- | --- | --- |
| 凭记忆写 RDKit / LangChain API | 首轮 28 项测试失败 | 写任何依赖库代码前先 `dir()` / 真实调用一次 |
| 批量改 Markdown 表格时连带删掉相邻行 | 决策登记表 G1 行被误删 | 改表格后回读整段核对 |
| 按条件批量替换导致 D3 行重复 | 登记表出现两条相同行 | 改完查编号重复 |
| 写批处理脚本批量 dedent | `test_evaluation.py` 缩进全错、语法崩溃 | 改代码用 Edit 按内容匹配，不写脚本批量改缩进 |
| 测试类名不含 pytestmark 名 | `-k requires_model` 选中 0 个 | pytestmark 名字要出现在类名里 |
| 嵌套类在测试方法内用 `self._Chunk` | `AttributeError` | 提到模块级 |
| 容器内写文件未挂载卷 | 评估结果随容器销毁丢失 | 输出路径支持环境变量覆盖 + 挂载卷 |
| gitignore 未覆盖带后缀的 venv 名 | `.venv-xuezhi312/` 会被误提交 | 改用通配 `.venv*/`、`venv*/` |
| Dockerfile 注释写了但 `RUN` 段漏装 | 镜像内无该包却报"依赖缺失" | 改依赖后 `docker build --no-cache` 确认 |

## 6. 一个工程纪律上的问题（需要正视）

**分支纪律没有真正发挥作用。**

本项目规范要求「一个模块一个分支」，但实测结果是：
8 个功能分支最终形成了**单一线性链**，14 个提交全在最后一个分支上。
验证数据：

```
feat/backend-chem-rdkit-engine           +4
feat/backend-llm-model-adapter          +7   （在其上继续）
feat/backend-agent-tool-dispatcher      +8   （在其上继续）
...
feat/knowledge-organic-chemistry-corpus +14  （最终分支）
```

**原因分析**：每个模块都在前一个模块的分支上继续开发，
`git checkout -b` 时新分支包含了旧分支的全部历史。
这在实践中是**低效但不有害**的——它保证了每个提交都是可运行的，
但也意味着：

- 无法单独检出某个模块的分支
- 无法对单个模块做 code review 或 cherry-pick
- 分支名暗示的"模块隔离"与实际内容不符

**改进方向**（待定）：后续若并行开发多个模块，
应从 `main` 切分支而非从当前分支切，合并时用
`--no-ff` 保留分支拓扑，让模块边界在历史上可见。

本文记录这一事实而非掩盖，因为**留痕的价值包括记录做错的地方**。

## 7. 当前状态与未决项

### 7.1 已完成（有实测支撑）

五个模块，352 项测试通过 / 36 项按设计跳过：

| 模块 | 测试 | 实测验证 |
| --- | --- | --- |
| `backend-chem` | 42 | RDKit 真实解析、11 条 SMARTS 实跑 |
| `backend-llm` | 49 | MaaS 三项实测全通过 |
| `backend-rag` | 60 | bge-small-zh 中文检索实测 |
| `backend-agent` | 72 | 模型自主调用工具的完整闭环 |
| `knowledge-data` | 129 | 切分、准入、评估 |

### 7.2 端到端链路已验证

```
学生提问
  ↓ 模型自主决定调用 search_knowledge（工具选择实测 4/4 正确）
  ↓
bge-small-zh 检索自编讲义 → top3 命中 96.97%
  ↓
回填教材原文（含来源标识/教材名/版本/章节）
  ↓
模型基于片段作答，并主动标注五项来源信息
```

两个边界行为亦已验证：
- 超纲问题 → 如实说"知识库未收录，以下结论不来自教材检索"，不编造出处
- 检索失败与"未找到"在**数据结构上不可混淆**

### 7.3 未完成

| 模块 | 状态 |
| --- | --- |
| `backend-api` | **未开始**——无 HTTP 接口 |
| `frontend-web` | **未开始** |
| `frontend-viz` | **未开始**——3D 分子动画是课标核心卖点 |
| `backend-speech` | **未开始**——Fay 数字人 + Edge-TTS |
| MCP 协议接入 | **未开始**——当前为自研调度层 |

### 7.4 最大未决项

**41 条自编讲义尚未经化学教师审核**（H13/H18）。
知识点讲错比缺内容更严重——`reviewer` 字段全部标为
「待化学教研组审核」，须专业确认准确性与教学口径。

次要未决项：
- 检索阈值：语料 41 条下五个阈值结果完全相同，
  距离分布区分度需数百条样本才显现（C5）
- 重排序：top3 命中 96.97% 但首位仅 81.8%，
  6 个非首位案例中 5 个正确片段在 top2 内（I1）
- 决策登记表 58 条中 40 条待决策，多数为"等做到那一步再定"

## 8. 合并与推送记录

**执行时间**：2026-10-04

### 操作结果

| 项 | 值 |
| --- | --- |
| 合并方式 | `--ff-only`（8 个分支依次快进，无冲突） |
| 合并前远端 | `badcdcb` |
| 合并后远端 | **`617145e`** |
| 推送耗时 | 8 分 45 秒（15 个提交 / 59 个文件） |
| 合并前安全核验 | 敏感文件 0、密钥内容 0、工作区干净 |
| 合并后测试 | **352 通过 / 36 跳过**，与合并前一致 |
| 本地分支清理 | 8 个功能分支已删除，仅留 `main` |

分支合并顺序（依赖顺序）：

```
feat/backend-chem-rdkit-engine              (化学引擎)
  └─ feat/backend-llm-model-adapter         (模型适配)
       └─ feat/backend-agent-tool-dispatcher (Agent 编排)
            └─ feat/backend-rag-knowledge-retrieval  (知识检索)
                 └─ feat/rag-bge-chinese-embedding    (中文嵌入)
                      └─ feat/agent-rag-knowledge-tool (检索接入 Agent)
                           └─ feat/knowledge-data-ingestion   (切分与评估)
                                └─ feat/knowledge-organic-chemistry-corpus (自编讲义)
```

**推送方式**：`git -c http.proxy=http://127.0.0.1:7897 -c https.proxy=... push`。
本机 git 全局代理常处于关闭状态，直连推不动（实测代理快 35 倍）。

### 为何用快进而非 `--no-ff`

本次 8 个分支事实上是**单一线性链**（每个模块在前一个模块之上开发，
见 §6），快进合并不丢历史，也避免制造 8 个无实际内容的合并节点。

分支纪律的改进方向见 §6——若后续并行开发多个模块，
应从 `main` 切分支并用 `--no-ff` 合并，让模块边界在历史上可见。

### 合并后的仓库状态

```
代码：41 个 Python 文件（含测试），8832 行
文档：23 份 Markdown，3619 行
语料：47 条（自编讲义 41 + 开发集 6）
分支：仅 main，与远端 origin/main 完全一致
测试：352 通过 / 36 按设计跳过
```
