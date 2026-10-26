# 需求基线文档

| 项目 | 内容 |
| --- | --- |
| 文档编号 | XZ-REQ-BL-001 |
| 基线版本 | **v1.1** |
| 冻结日期 | 2026-10-22（v1.0） |
| 最近修订 | 2026-10-26（本地合入 hyf 分支 + 补 FR-14 反应机理可视化 / FR-15 习题模块 / AC-09；更正 TC-03 RDKit 本机可用性例外；修正 uvicorn 版本出处） |
| 对应代码状态 | `main@ed6d257`（2026-10-10，Merge `feat/probe-verification`） |
| 适用对象 | 项目负责人、开发、测试、部署与演示人员、评审专家 |
| 上位文档 | [产品范围与验收标准](product-scope.md)、[系统架构](architecture.md)、[接口契约](interface-contract.md)、[决策登记表](decision-register.md) |
| 配套文档 | [配置与部署清单](config-deployment-checklist.md) |

## 1. 基线声明

### 1.1 本文解决什么问题

`product-scope.md` 定义了**产品意图**，`architecture.md` 定义了**组件边界**，
但两者都没有给出「一条一条可打勾的验收条目」，也没有把**技术栈选型**对需求的
约束讲清楚。结果是：同一个能力，开发认为已交付、评审认为未验收、演示材料写成已上线。

本文把三者合并成一份**可验收、可追溯、可变更**的需求基线：

- 每条需求有唯一编号（`FR-` / `NFR-` / `TC-` / `NR-`），后续评审、Issue、提交信息直接引用编号；
- 每条需求绑定**可执行或可观察的验收判据**，不写「体验良好」这类无法判定的措辞；
- 每条需求标注**真实状态**，区分「已实测」「待验收」「未交付」；
- 技术栈选型从「实现细节」上升为**约束性需求**（`TC-` 组），因为改技术栈会直接推翻已验收的需求。

### 1.2 状态取值（全文档统一）

| 状态 | 含义 | 对外表述允许度 |
| --- | --- | --- |
| **已交付·已实测** | 代码存在，且有可复现的实跑记录（测试或真实调用） | 可对外宣称具备该能力 |
| **已交付·待验收** | 代码存在并跑通，但缺少领域审核（化学教师/教学评审） | 只能说「已实现，内容待审核」 |
| **部分交付** | 主路径可用，存在已登记的限制（如单 worker 限流） | 必须同时说明限制 |
| **未交付** | 无实现，或实现不可运行 | **不得**写入演示材料或答辩 PPT |

### 1.3 本文不替代什么

- 字段级 JSON 语义以 `interface-contract.md` 与 `docs/api/openapi.json` 为准；
- 未确认事项以 `decision-register.md` 为准，本文引用其编号（A/B/C/E/G/H/I 系列）；
- 产品定位与非目标以 `product-scope.md` 为准。

## 2. 干系人与使用场景

| 角色 | 关注点 | 在本基线中的对应条目 |
| --- | --- | --- |
| 高中学生（终端用户） | 答案是否有依据、结构能否看见、语音能否听懂 | FR-01～FR-05、NFR-01、NFR-03 |
| 化学教师 / 领域审核者 | 内容是否符合课标、是否会误导 | FR-11、AC-05 |
| 演示者（答辩现场） | 断网/弱网下能否稳定演示 | NFR-07、AC-06 |
| 部署与运维 | 一条命令起得来、故障看得懂 | FR-08、NFR-06、配置清单 |
| 评审专家 | 宣称的能力是否有实测支撑 | §1.2 状态列、§7 验收门禁 |

## 3. 技术栈基线

### 3.1 技术栈清单（版本均已落地在仓库文件中）

| 层 | 技术 | 版本约束（实测/锁定值） | 依据文件 | 可替换性 |
| --- | --- | --- | --- | --- |
| 运行时 | Python | **3.12**（基线决策 H1） | `requirements-lock.txt`、`Dockerfile.test` | 不可随意降级（numpy/pandas 下限要求 3.12） |
| Web 框架 | FastAPI | `0.142.2` | `requirements-lock.txt` | 可替换但需重写 7 个端点与错误处理 |
| ASGI | uvicorn[standard] | `>=0.24.0`（仅 `requirements.txt` 下限，**未在 `requirements-lock.txt` 锁定具体版本**） | `requirements.txt` | 可替换（gunicorn 等） |
| 数据校验 | Pydantic | `2.13.5` | 同上 | 与 FastAPI 强绑定 |
| Agent 编排 | LangChain | `1.4.3` | 同上 | 可替换，但 `AIMessage` 契约已写入调度层 |
| 模型适配 | langchain-openai | `1.6.7` | 同上 | 可用原生 SDK 替代（需重测 tool_calls 结构） |
| 向量库 | ChromaDB | `1.5.9`（cp39-abi3 wheel，免编译） | 同上 | 可替换，但需重建索引 |
| 嵌入模型 | sentence-transformers + `BAAI/bge-small-zh` | `6.1.0` / 512 维 / 91.4 MiB | `docs/embedding-model-verification.md` | **更换即需重建全部索引**（维度与语义均不兼容） |
| 化学计算 | RDKit | `2026.3.6` | `Dockerfile.test`、`requirements.txt`（注释） | **不可替换**（12 条 SMARTS、性质、ETKDG 坐标均依赖） |
| 语音合成 | edge-tts | `7.2.8`（纯 Python wheel，LGPLv3） | `requirements-lock.txt` | 可替换，但**必须仍能产出 MP3**（前端按 `audio/mpeg` 播放） |
| 数字人 | Fay | 上游未锁版本；容器内锁 `langgraph==0.3.5`、`langgraph-sdk==0.1.74` | `infra/fay/Dockerfile` | 可选增强，`GPL-3.0`，**仅 HTTP 调用不分发** |
| 前端框架 | Vue | `3.5.43` | `frontend/package.json` | 可替换但代价极高 |
| 语言 | TypeScript | `6.0.3`（`vue-tsc 3.3.12`） | 同上 | 不可降级（`vue-tsc --build` 依赖新语法） |
| 构建 | Vite | `8.3.2` | 同上 | 可替换 |
| 3D 渲染 | Three.js | `0.186.1` | 同上 | 可替换（Molstar 方案已评估未采用） |
| 状态管理 | Pinia | `4.0.3` | 同上 | 可替换 |
| Node | Node.js | `^20.19.0 \|\| >=22.12.0`；容器 `node:22-alpine` | `frontend/package.json`、`docker-compose.yml` | 受 Vite 8 约束 |
| 模型服务 | 华为 ModelArts MaaS（OpenAI 兼容） | `openpangu-2.0-flash`（默认）/ `openpangu-2.0-pro` | `backend/app/llm/config.py` 的 `MAAS_MODELS` | **白名单内的官方标识**，不自造 |
| 编排 | Docker Compose v2（Desktop / WSL2 后端，实测 daemon 29.8.1） | — | `docker-compose.yml` | 仅演示/开发配置 |

### 3.2 技术栈硬约束（约束性需求 `TC`）

这些不是「选型偏好」，而是**一旦违反就必须重做验收**的约束。

| 编号 | 约束 | 违反后果 | 依据 |
| --- | --- | --- | --- |
| **TC-01** | 模型调用必须走 OpenAI 兼容协议，模型标识取自 `MAAS_MODELS` 白名单 | 非白名单标识不被拒绝，但 `official_maas_model=false`，答辩时无法声称使用官方 openPangu | `llm/config.py:27`；B4 实测：`DeepSeek-*` 404、`Qwen3-*` 403 |
| **TC-02** | 嵌入模型固定为 `BAAI/bge-small-zh`（512 维），与已建索引一致 | 换模型 = 维度不匹配 → 检索全挂；必须 `scripts/build_index.py` 重建 | H17、`rag/embeddings.py` |
| **TC-03** | 化学能力依赖 RDKit 原生扩展，Windows 应用控制策略（SAC/WDAC）下**可能**不可用——取决于安装所在盘符/策略作用域，非 Python 版本或发行来源问题 | 默认被拦时本机无法解析结构、3D 视图降级；但**项目专用 F: 盘 venv `.venv-xuezhi312`（Python 3.12，rdkit 2026.3.6）2026-10-26 实证可本机运行**，容器仅作兜底 | `local-run-guide.md` §2 + 2026-10-26 实测 |
| **TC-04** | 前端必须用**同源相对路径**（`VITE_API_BASE_URL` 留空）或显式配 CORS | 跨域直连时后端 CORS 白名单默认为空，浏览器预检被拒（实测 405） | `frontend/.env.example`、`api/app.py` |
| **TC-05** | 语音合成依赖微软在线服务，**不构成离线能力** | 断网演示只能降级为纯文本，不得宣称「离线语音」 | B5、`speech-module-verification.md` |
| **TC-06** | `docker-compose.yml` 是**演示编排**（源码挂载 + `npm run dev`），不是生产部署 | 直接用它上线 = 开发服务器暴露 + 无 TLS + 无鉴权 | `README.md` §当前范围、A7 未决 |

### 3.3 算力平台与昇腾适配现状（必须显式声明）

本项目参加昇腾创新实践/华为创新赛，**算力平台的合规性属于技术栈要求的一部分**，
因此单独列示，避免答辩时被问住：

| 项 | 现状 | 状态 |
| --- | --- | --- |
| 云端模型调用 | 通过 `https://api.modelarts-maas.com/openai/v1` 调用 openPangu，**已真实调用通过**（含 Function Call 与流式：47 块 / 首块 0.86 s） | 已交付·已实测 |
| 本地 openPangu-7B-Instruct（AWQ 4-bit） | 无可用推理适配器；`autoawq` 纯 sdist 需编译且停更，RTX 5070 的 CUDA 组合未实测 | **未交付**（A5、B1–B3、G3–G4） |
| 昇腾 NPU（CANN/Ascend）本地推理 | 仓库内无任何 CANN、`torch_npu`、MindIE 相关依赖或适配代码；无实测记录 | **未交付，且无实测依据** |
| ModelArts / AgentArts 平台化部署 | 仅有本地 Compose 验证；云上部署未做 | **未交付**（A7） |

> **口径要求**：当前可宣称的是「使用华为云 ModelArts MaaS 的 openPangu 模型服务」；
> **不可**宣称「已在昇腾 NPU 上完成本地推理部署」。若赛题强制要求昇腾本地算力，
> 该项应作为最高优先级缺口立项，而不是在文档里模糊表述。

### 3.4 技术栈如何决定需求边界

| 技术事实 | 直接导致的需求边界 |
| --- | --- |
| RDKit 只做结构解析与性质计算，不做反应机理推演 | FR-04 的验收只覆盖「解析/性质/官能团」，**不含机理正确性**（NR-03） |
| 嵌入模型是通用中文模型，非化学领域模型 | FR-03 只承诺「来源可追溯」，**不承诺首位命中率**；C4/C5 阈值未批准 |
| edge-tts 单次合成有长度风险，实现取 400 字符上限 | FR-06 必须返回 `truncated` 标志，不允许静默截断 |
| SSE 响应头一旦发出不能改状态码 | FR-02 必须保留同步接口作为兜底（A3），错误经事件流传递 |
| 令牌桶是进程内存态 | FR-10 在单 worker 下成立；多 worker 时实际配额 = workers × rate（I4） |
| 语音/数字人属可选增强 | NFR-03 要求任何多媒体失败都不得吞掉文本答复 |

## 4. 功能需求基线（FR）

### 4.1 基线表

| 编号 | 需求 | 优先级 | 验收判据 | 实现位置 | 状态 |
| --- | --- | --- | --- | --- | --- |
| **FR-01** | 同步问答：提交中文问题，返回有机化学解释 | P0 | `POST /api/v1/ask` 返回 200 且 `status ∈ {completed, partial}`；`explanation` 非空 | `api/routes.py::ask` | 已交付·已实测 |
| **FR-02** | 流式问答：SSE 逐 token 输出，前端可即时渲染 | P0 | 事件序列含 `meta`→`stage`→`delta`→`result`；失败时发 `error` 事件而非无提示断流 | `api/routes.py::ask_stream`、`agent/dispatcher.py` | 已交付·已实测 |
| **FR-03** | 来源可追溯：答复附带知识库条目元数据 | P0 | 检索成功时 `sources` 含 `source_id`/`title`/`locator`；检索失败时 `sources=[]` 且**不得伪造** | `agent/knowledge_tools.py`、`knowledge/corpus.py` | 已交付·已实测 |
| **FR-04** | 分子解析：SMILES → 性质 + 结构 + 官能团 | P0 | `POST /api/v1/molecule` 返回 `properties`/`structure`/`functional_groups`；非法 SMILES 返回 200 + `ok=false` + `chem_invalid_structure` | `chem/engine.py` | 已交付·已实测 |
| **FR-05** | 3D 渲染数据：提供原子/键/坐标供 Three.js 渲染 | P1 | 数据符合 `molecule-structure/v2` schema；`conformer=failed` 时仍能渲染二维结构 | `chem/viz_schema.py`、`frontend/src/viz/` | 已交付·已实测 |
| **FR-06** | 语音合成（两阶段交付） | P1 | `POST /api/v1/speak` 返回 `audio_id` + `audio_url`；`GET /api/v1/speak/{id}` 返回 `audio/mpeg` > 1 KB；接口**永不返回 5xx**（除配置错误） | `api/routes.py::synthesize_speech`、`speech/service.py` | 已交付·已实测 |
| **FR-07** | 数字人推送（可选） | P2 | `XUEZHI_FAY_ENABLED=1` 时向 `{FAY_URL}/transparent-pass` 推送；失败仅置 `digital_human_delivered=false` | `speech/fay.py` | 已交付·已实测 |
| **FR-08** | 健康检查与就绪探针 | P0 | `/health` **恒返回 200** 且给出 4 个组件明细；`/ready` 就绪 200 / 未就绪 503 | `api/routes.py`、`api/deps.py::probe` | 已交付·已实测 |
| **FR-09** | 统一错误语义：稳定错误码 + 中文文案 + 不泄露内部信息 | P0 | 错误体含 `code`/`message`/`retryable`/`request_id`；缺密钥返回 503 `llm_not_configured`；校验失败不回显原始 `input` | `api/errors.py`、`api/app.py` | 已交付·已实测 |
| **FR-10** | 限流：防滥用，不误伤探针 | P1 | 仅 `/api/*` 受限；超限返回 429 + `Retry-After`；`/health`、`/docs` 不限流 | `api/ratelimit.py` | 部分交付（多 worker 不共享，I4） |
| **FR-11** | 课程边界与诚实降级：超纲/条件不足/工具不支持时明说 | P0 | 超纲问题给出边界说明；RDKit 不可用时 `/health` 报 `chem.ready=false` 且问答降级为仅检索 | `api/deps.py::get_agent_loop`、系统提示词 | 已交付·**待验收**（需教师评审，C1） |
| **FR-12** | 知识库构建：语料 → 向量索引 | P0 | `python -m scripts.build_index` 成功；`--check-only` 可查条数；41 条 / 21 主题 | `scripts/build_index.py`、`knowledge/builder.py` | 已交付·已实测 |
| **FR-13** | 本地离线模型模式（openPangu-7B AWQ） | P2 | 断网条件下加载权重并完成一次完整问答 | 无（仅云端适配器） | **未交付** |
| **FR-14** | 反应机理可视化：断键/成键 3D 帧动画 + Agent 路由到反应工具 | P1 | `POST /api/v1/reaction/mechanism` 返回 `reaction-mechanism/v1` 帧序列；未收录反应返回 `ok=false` + `reaction_not_found`（200 信封，不报 5xx）；断键/成键标注与帧内 `opacity` 可见性一致；前端 `ReactionMechanism.vue` 可播放 | `app/reaction/{schemas,frames,library,routes}.py`、`app/agent/reaction_tools.py`、`frontend/src/viz/ReactionPlayer.ts`、`frontend/src/components/ReactionMechanism.vue` | 已交付·**待验收**（反应库 3 例均 `draft`，需化学教师评审） |
| **FR-15** | 习题模块：题目获取 / 作答评分 / 答案解析 | P1 | `GET /api/v1/quiz/questions` 返回题目；`POST /api/v1/quiz/submit` 返回评分；`POST /api/v1/quiz/explain` 返回解析；纯逻辑测试 32 例通过 | `app/quiz/{models,store,grade,explain,routes}.py` | 已交付·已实测 |

**优先级定义**：P0 = 演示/答辩缺一不可；P1 = 显著影响体验但可降级；P2 = 增强项，缺失不影响主流程。

### 4.2 P0 需求详述

#### FR-01 同步问答

| 项 | 约定 |
| --- | --- |
| 输入 | `{"question": string}`，1–4096 字符，UTF-8，纯空白拒绝 |
| 输出 | `request_id`、`status`、`explanation`、`sources[]`、`tool_invocations[]`、`steps`（1–32）、`elapsed_seconds` |
| 状态迁移 | `processing → completed / partial / failed`（当前单轮，只有一个终态） |
| 部分成功 | 检索失败但文本成功 → **200 + `partial`**（不得返回 5xx，否则前端误判整体失败） |
| 已知限制 | 4096 字符是**工程取值**，非性能实测结论（C6 未批准） |

#### FR-02 流式问答

| 项 | 约定 |
| --- | --- |
| 事件 | `meta`（携 `request_id`，立即发）→ `stage` → `delta`（逐 token）→ `tool` / `source` → `result` / `error` |
| 硬约束 | 必须声明 `response_class=EventSourceResponse` 且 `response_model=None`，否则装饰器执行期抛错 |
| 编码 | 中文在 SSE 线格式中被转义为 `\uXXXX`，前端必须 `JSON.parse` 还原 |
| 兜底 | 流式失败自动降级为同步；同步接口不得删除 |

#### FR-03 来源可追溯

| 项 | 约定 |
| --- | --- |
| 必填字段 | `source_id`（≤128）、`title`（≤512）、`locator`（≤256）、`locator_kind` |
| 缺失处理 | 字段缺失给空串，不报错——来源是展示信息，不该让问答失败 |
| 审核状态 | Agent 层不返回审核结论时标 `unknown`，**不得写成 `approved`** |
| 禁止 | 检索失败时不得由模型「回忆」出教材原文并标注为来源 |

#### FR-04 分子解析

| 项 | 约定 |
| --- | --- |
| 输入 | `{"smiles": string}`，1–512 字符 |
| 成功 | `ok=true`，`properties`（分子式/分子量/原子数/键数/环数）、`structure.viz_data`、`functional_groups`（**仅 `matched=true`**） |
| 失败 | HTTP 200 + `ok=false` + `chem_invalid_structure`（用户输错不是服务故障） |
| 组件缺失 | RDKit 不可用 → 503 `api_component_unavailable`，`/health` 如实报告 |
| 口径 | 能解析 ≠ 机理正确；`verification` 字段须区分语法/合法性/性质/基团四层 |

#### FR-08 健康与就绪

| 探针 | 语义 | 用途 |
| --- | --- | --- |
| `GET /health` | **恒 200**，`status ∈ {ok, degraded, not_ready}` + 4 组件（`chem`/`llm`/`rag`/`speech`） | 给人排障；不用于 liveness（否则故障时会反复重启） |
| `GET /ready` | 就绪 200 / 未就绪 503 | 给编排决定是否接流量 |
| `caps` 约定 | `tts`/`fay`/`rag` = **配置意图**；`tts_verified`/`fay_verified`/`rag_verified` = **实测可用** | 前端关闭降级路径**必须**看 `*_verified` |

> 该字段是踩坑产物：曾连续四次「`/health` 说就绪、实际不可用」（tts 未安装、rag 嵌入模型加载失败、
> fay 端口无监听、embedding 路径传错）。因此**验收时只信 `*_verified`，并配合 `scripts/demo-check.sh` 真跑一遍**。

#### FR-09 错误语义

| 场景 | 错误码 | HTTP |
| --- | --- | --- |
| 输入为空/超长/格式错 | `api_invalid_input` | 400 |
| 缺模型密钥 | `llm_not_configured` | 503 |
| 组件加载失败 | `api_component_unavailable` | 503 |
| 限流 | `api_rate_limited` | 429 |
| 结构非法 | `chem_invalid_structure` | 200（`ok=false`） |
| 音频过期/不存在 | 统一 404（**不区分**，避免泄露「曾经存在过」） | 404 |
| 未分类异常 | `api_internal_error` | 500（不返回类名/消息/堆栈） |

## 5. 非功能需求基线（NFR）

| 编号 | 需求 | 判据 / 实测值 | 状态 |
| --- | --- | --- | --- |
| **NFR-01** | 响应延迟可接受 | MaaS 短请求中位 **7.10 s**、最长数十秒；SSE 首块 0.86 s；本机目标值**未批准**（C2） | 已实测，目标待定 |
| **NFR-02** | 可承载课堂级并发且不滥用上游 | 默认 1 rps / burst 8；5 并发实测未触发 MaaS 限流 | 已实测（单 worker） |
| **NFR-03** | **降级优先于失败** | chem / rag / tts / fay 任一失败，文本答复仍完整交付 | 已交付·已实测 |
| **NFR-04** | 安全与隐私 | 密钥不入 Git/日志/前端 bundle；CORS 白名单；输入上限 4096；错误不回显输入 | 已交付·已实测 |
| **NFR-05** | 构建可复现 | `requirements-lock.txt` 仅锁定直接依赖，**无 hash、无完整传递依赖锁定**；RDKit 只在测试镜像固定版本，autoawq/torch 的本地推理组合未形成可复现基线 | **部分达标**（G2、E4） |
| **NFR-06** | 可观测性 | 每次请求有 `request_id`、`elapsed_seconds`、`tool_invocations`；组件探测有缓存 | 已交付；监控/告警未接入（A7） |
| **NFR-07** | 离线可用性 | 断网时仅本地知识检索（需预置嵌入权重）与分子解析可用；完整问答依赖 MaaS，语音依赖 Edge-TTS，二者均不可用 | **不达标**（FR-13、B5） |
| **NFR-08** | 版本基线一致性 | Python 3.12 / Node ^20.19 或 ≥22.12 / RDKit 2026.3.6 | 已锁定 |
| **NFR-09** | 文档与代码同步 | 接口/配置/行为变更须同分支更新文档（`development-workflow.md` §5） | 已有规则，**执行有缺口**（见 §10） |

## 6. 明确非目标（NR）

以下条目**不属于**本基线承诺，答辩与宣传材料中不得暗示已具备：

| 编号 | 非目标 |
| --- | --- |
| **NR-01** | 图片、手写公式、试卷照片、视频输入与识别 |
| **NR-02** | 大学有机化学、无机化学、生物化学、离子反应为主的教学范围 |
| **NR-03** | 严格反应机理证明；RDKit 通过不代表机理正确 |
| **NR-04** | 真实实验执行、实验设备控制、危险化学品制备指导 |
| **NR-05** | 多轮会话记忆（服务端无状态，历史由调用方自行保留，A2 未决） |
| **NR-06** | 用户认证与权限体系（当前无鉴权，A2 未决） |
| **NR-07** | 音素级口型同步（Fay 的 `Lips` 仅在 Windows 生成，Linux 容器内恒为空，A4） |
| **NR-08** | 生产级 SLA / 可用性承诺（C3 未批准） |
| **NR-09** | 昇腾 NPU 本地推理（见 §3.3） |

## 7. 验收基线（AC）

### 7.1 质量门禁

| 编号 | 门禁 | 执行方式 | 当前结果 |
| --- | --- | --- | --- |
| **AC-01** | 后端测试全通过 | `docker run --rm xuezhi-chem-test` | 历史基线：752 passed / 40 skipped / 0 failed；目标机发布前须复跑 |
| **AC-02** | 前端测试与类型检查 | `cd frontend && npm ci && npm run typecheck && npm test` | 历史基线：166 passed、`vue-tsc` 无错误；目标机发布前须复跑 |
| **AC-03** | 接口契约无漂移 | `python -m scripts.export_openapi --check` | 一致：7 端点 / 15 schema / OpenAPI 3.1.0 |
| **AC-04** | 检索质量不低于基线 | 33 条探针集 | top-1 27/33（81.8%）、top-3 32/33（96.97%） |
| **AC-05** | 化学内容经教师审核 | 人工评审 | **未完成**（H13、H18、C1） |
| **AC-06** | 演示环境端到端可用 | `./scripts/demo-check.sh` | 脚本就绪，需在目标机复跑 |
| **AC-07** | 无密钥入库 | `git log -p \| grep -i maas_api_key` 人工复核 | 需在发版前复跑 |
| **AC-08** | 配置键与代码一致 | 对照[配置与部署清单](config-deployment-checklist.md) §2 | 已核对；已知漂移与限制见该文 §11 |
| **AC-09** | 反应机理可视化端到端可用 | 后端 `backend/tests/reaction/test_e2e.py`（4 例，真实 RDKit 帧）+ 前端 `frontend/e2e/verify-reaction.mjs`（Playwright 真实渲染 8 例） | 后端 4 passed；前端 8/8 通过 |

### 7.2 不得对外宣称的指标

以下指标**尚无实测依据或未经批准**，任何文档、PPT、答辩口述都不得给出具体数值：

- 答案准确率、教学有效性（C1）；
- 本地推理延迟与吞吐（C2，且本地模式未交付）；
- 系统可用性 / SLA（C3）；
- 检索阈值「已调优」（实测 5 个阈值结果完全相同，设阈值只会制造已调优的假象，I1）；
- 并发承载能力上限（仅测过 5 并发）。

## 8. 需求追溯矩阵

| 需求 | 代码 | 验证证据 | 相关决策 |
| --- | --- | --- | --- |
| FR-01 | `app/api/routes.py`、`app/agent/dispatcher.py` | `backend/tests/api/test_app.py`、`test_e2e_http.py` | A2、A3 |
| FR-02 | `app/api/streaming.py`、`app/agent/dispatcher.py` | `backend/tests/api/test_streaming.py`、`agent-streaming-verification.md` | A3、I3 |
| FR-03 | `app/agent/knowledge_tools.py`、`app/knowledge/corpus.py` | `backend/tests/agent/test_streaming_sources.py` | I2、D3 |
| FR-04 | `app/chem/engine.py`、`app/api/routes.py` | `backend/tests/chem/*` | I5、H13 |
| FR-05 | `app/chem/viz_schema.py`、`frontend/src/viz/` | `backend/tests/chem/test_viz_schema.py`、`frontend-viz-verification.md` | F5、I7 |
| FR-06 | `app/speech/*`、`app/api/routes.py` | `backend/tests/speech/test_speech.py`、`test_speech_endpoint.py` | H21、H31 |
| FR-07 | `app/speech/fay.py` | `speech-module-verification.md` | A4、H22 |
| FR-08 | `app/api/deps.py::probe` | `backend/tests/api/test_probe_honesty.py` | — |
| FR-09 | `app/api/errors.py`、`app/api/app.py` | `test_errors_ratelimit.py` | I6、H20-corr |
| FR-10 | `app/api/ratelimit.py` | `test_errors_ratelimit.py`（10 项） | I4 |
| FR-11 | `app/api/deps.py::get_agent_loop` | 缺 RDKit 场景降级测试 | H20 |
| FR-12 | `scripts/build_index.py`、`app/knowledge/builder.py` | `backend/tests/knowledge/test_builder.py` | H24 |
| FR-13 | — | — | A5、G3、G4 |
| FR-14 | `app/reaction/*`、`app/agent/reaction_tools.py`、`frontend/src/viz/ReactionPlayer.ts`、`frontend/src/components/ReactionMechanism.vue` | `backend/tests/reaction/test_e2e.py`、`test_reaction.py`、`test_agent_link.py`、`frontend/e2e/verify-reaction.mjs` | H20-corr（RDKit 本机可用性 2026-10-26） |
| FR-15 | `app/quiz/*` | `backend/tests/quiz/test_quiz.py`（32 例） | — |

## 9. 基线变更控制

| 触发条件 | 处理 |
| --- | --- |
| 新增/删除可对外宣称的能力 | 升版（v1.x → v1.y），更新 §4 与 §7，同步 `docs/README.md` |
| 技术栈替换（TC-01～TC-06 任一） | 升版并**重跑全部 AC 门禁**，在 §3.1 记录新版本与依据 |
| 验收判据变严或变松 | 升版，说明原因与影响范围 |
| 仅补充实测数值（不改判据） | 可原地更新，记入 §11 变更记录 |
| 未决项被批准（C1–C3、A6、A7 等） | 在 `decision-register.md` 标「已确认」，回填本文对应条目 |

**规则**：未确认的取值一律不写入本文（沿用 `development-workflow.md` §1.5「不写猜测值」）。
基线升版必须在同一分支内同步 `product-scope.md` 与 `config-deployment-checklist.md`，避免三份文档各说各话。

## 10. 当前基线的已知缺口与风险

| 编号 | 缺口 | 影响 | 责任方 | 处置建议 |
| --- | --- | --- | --- | --- |
| R-01 | 化学内容未经教师审核（H13/H18/D3） | AC-05 无法关闭，答错风险不可控 | 化学审核者 | 答辩前完成 41 条语料抽审，至少覆盖 5 处高频易错点 |
| R-02 | 无昇腾 NPU 本地推理（§3.3） | 若赛题硬性要求，属合规缺口 | 项目负责人 | 先确认赛题口径，再决定是否立项 |
| R-03 | 依赖未完整锁定（NFR-05） | 换个机器可能装出不同版本 | infra | `pip-compile --generate-hashes` 补全 |
| R-04 | 无限流跨进程共享（I4） | 多 worker 下限流失效 | infra | 演示保持单 worker；生产前换分布式 |
| R-05 | 无鉴权、无会话（NR-05/06） | 不能直接对公网开放 | 架构 | 演示限于内网/本机 |
| R-06 | `deployment-operations.md` §1 仍写「仓库仅含文档」 | 与 README 事实矛盾，评审时会被质疑文档可信度 | docs | 见配置清单 §11 待回填项 |
| R-07 | 检索首选命中率 81.8%，6 例非首位中 5 例正确片段在 top2 | 学生可能看到次要来源 | backend-rag | 先扩语料观察分布，再评估 reranker（I1） |
| ~~R-08~~ | ~~后端缺 `/api/v1/reaction/list` 端点，前端 `ReactionMechanism.vue` 硬编码 3 个反应 id~~ **已解决（2026-10-26）**：后端已补 `GET /api/v1/reaction/list`，前端改为拉取（内置清单仅作降级回退），后端 E2E 5 例 + 前端 Playwright 8/8 通过 | — | backend-reaction | — |

## 11. 变更记录

| 版本 | 日期 | 变更内容 | 依据 |
| --- | --- | --- | --- |
| v1.0 | 2026-10-22 | 首次建立需求基线：技术栈清单、6 条硬约束、13 条功能需求、9 条非功能需求、9 条非目标、8 项验收门禁、追溯矩阵；明确完整问答与语音不具备离线能力 | 代码 `main@ed6d257` + 各模块实测文档 |
| v1.1 | 2026-10-26 | 本地合入 hyf 分支需求基线；补 FR-14 反应机理可视化、FR-15 习题模块与 AC-09；更正 TC-03（RDKit 本机可用性例外）；修正 uvicorn 版本出处 | 代码 `feat/reaction-viz-schema` / `feat/quiz-practice` |
| v1.2 | 2026-10-26 | 关闭 R-08：后端补 `GET /api/v1/reaction/list`，前端 `ReactionMechanism.vue` 改为拉取反应清单（保留内置清单作降级回退）；同步重导出 openapi.json（9 端点 / 19 schema） | 代码 `feat/reaction-viz-schema`（`bf5cd03`） |

---

**配套阅读**：[配置与部署清单](config-deployment-checklist.md)（本文 `AC-08` 的核对对象）、
[部署与运维手册](deployment-operations.md)、[安全与隐私基线](security-privacy.md)。
