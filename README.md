# 学智有机 | Xuezhi-OrganicAgent

### ✨ 面向高中有机化学的MCP智能体教学系统

> **文档口径说明（2026-10-05 修正）**
>
> 本文区分三类内容，请勿混淆：
>
> | 标记 | 含义 |
> | --- | --- |
> | ✅ **已交付并实测** | 有源码、有测试、有实跑记录（详见各验证文档） |
> | 📋 **目标设计** | 尚未实现，描述的是计划中的形态 |
> | 🚫 **本机不可用** | 受环境限制，本机跑不了（已实测确认） |
>
> **本仓库已包含前后端完整源码**（`backend/` 43 个 Python 模块、
> `frontend/` 20 个 TS/Vue 模块）、测试与 Docker 配置。
> 早期版本曾表述为"仅有文档与依赖清单"，该表述已过时。

## 文档导航

- [文档总览](docs/README.md)
- [开发流程与分支规范](docs/development-workflow.md)
- [产品范围与验收标准](docs/product-scope.md)
- [系统架构与组件职责](docs/architecture.md)
- [部署与运维手册](docs/deployment-operations.md)
- [**本机运行指南**](docs/local-run-guide.md) ← 跑起来先看这份
- [知识库内容治理](docs/knowledge-base.md)
- [安全与隐私基线](docs/security-privacy.md)
- [逻辑接口基线](docs/interface-contract.md)
- [依赖选型实测记录](docs/dependency-notes.md)
- [Python 版本升级评估](docs/python-version-evaluation.md)
- [H1/H3 核查结论](docs/h1-h3-verification.md)
- [3.12 环境安装验证报告](docs/py312-install-verification.md)
- [化学引擎实现与验证状态](docs/chem-engine-verification.md)
- [模型适配层实现与验证状态](docs/llm-adapter-verification.md)
- [Agent 编排层实现与验证状态](docs/agent-dispatcher-verification.md)
- [知识检索层实现与验证状态](docs/rag-verification.md)
- [中文嵌入模型验证报告](docs/embedding-model-verification.md)
- [知识检索工具接入验证](docs/agent-knowledge-tool-verification.md)
- [知识数据层实现与验证](docs/knowledge-base-implementation.md)
- [有机化学自编讲义验证](docs/organic-corpus-verification.md)
- [开发留痕 2026-10](docs/development-log-2026-10.md)
- [接口契约实现与实测](docs/interface-contract-verification.md)
- [Agent 流式与来源验证](docs/agent-streaming-verification.md)
- [官能团 SMARTS 精度修正](docs/chem-smarts-verification.md)
- [前端实现与实测](docs/frontend-verification.md)
- [3D 分子可视化](docs/frontend-viz-verification.md)
- [torch 移除方案评估](docs/h8-torch-removal-evaluation.md)
- [决策登记表](docs/decision-register.md)

> 参与开发前请先阅读[开发流程与分支规范](docs/development-workflow.md)：每个模块的开发与更新在独立分支上进行。

> 华为创新赛 · 教育赛道参赛项目

## 📖 项目简介

**学智有机（Xuezhi-OrganicAgent）** 面向高中有机化学教学，
构建基于 MCP 的智能体调度系统，使用华为 openPangu盘古大模型。

交互形式为学生**文本提问**；能力包括知识点检索与引用溯源、
化学结构解析、化学校验、3D 分子可视化与 Fay 数字人语音讲解
（各项交付状态见上方[能力与交付状态](#-能力与交付状态)）。

知识库限定在高中课程范围，区分课标内容与拓展理论，
减少不同学段知识混用。

> ⚠️ 本项目**仅支持文本输入，无图像识别模块**


## ⭐ 能力与交付状态

> 每条都标注**实测结论**，不写"应该可以"这类话。

| # | 能力 | 状态 | 实测结论 |
| --- | --- | --- | --- |
| 1 | **openPangu 驱动 Agent 工具调用** | ✅ 已交付 | 实测工具调用 2.72 秒、流式 47 块（首块 0.86 秒）。Agent 编排为自写，非 LangChain 链 |
| 2 | **分层 RAG 知识库** | ✅ 已交付 | 中文嵌入 `BAAI/bge-small-zh`（512 维，已归一化），三个中文查询首位全对。**须显式设 cosine 空间**（默认 l2 会导致排序颠倒） |
| 3 | **3D 分子可视化** | ✅ 已交付 | RDKit 生成坐标 → Three.js 球棍模型，原子高亮可用。🚫 **不支持鼠标拖拽旋转**（缺 OrbitControls，仅自动旋转） |
| 4 | **Fay 数字人讲解** | ⚠️ 部分交付 | 形象三态切换、语音合成可用。🚫 **口型是本地近似**——Fay 在 Linux 不产音素（`Lips` 空） |
| 5 | **云端 + 离线双部署** | 📋 目标设计 | 云端 MaaS 路径已实测；离线 openPangu-7B AWQ **未实测** |
| 6 | **3D 反应动画**（断键/成键） | 📋 目标设计 | 当前只渲染单分子结构，**无反应过程动画** |

### 问答链路实测数据（2026-10-05）

| 指标 | 实测值 |
| --- | --- |
| 首次问答 | 12–21 秒（含冷启动） |
| 检索超时 | 已修复（原 15 秒阈值被冷启动 12.7 秒踩线） |
| 引用溯源 | ✅ 每次 5 条来源，含教材原文 |
| 前端测试 | **166 passed**（vitest） |
| 后端测试 | **753 passed / 39 skipped**（pytest，容器内） |

> **测试须在有、无 `MAAS_API_KEY` 两种环境下都能通过。**
> 此前有两项用例隐含假设"环境里没有 key"（注释写着
> 「不注入，让它因缺密钥抛错」），而容器为了联调配了真实 key，
> 于是**假失败 2 项**（`DID NOT RAISE`）。
> 已改用 `monkeypatch.delenv` 显式固定前提——
> **测试不该寄希望于环境恰好如何**（决策登记 H33）。
> 修复后实测：有 key 752 passed / 无 key 753 passed，**均 0 failed**。

### 已知限制（实测确认，非推测）

| 项 | 原因 |
| --- | --- |
| 🚫 本机 RDKit 不可用 | Smart App Control / WDAC 拦截（`WinError 4551`），换任何安装方式均无效 |
| 🚫 无真实音素口型 | Fay 的 `Lips` 仅 Windows 生成，容器内恒空 |
| ⚠️ 人设开关默认关闭 | 实测对人设文本**无任何影响**（口语标记两版都是 0），默认开启属虚假承诺 |
| ⚠️ LaTeX 偶发不渲染 | 实测 3 次采样 0 复现，属偶发；引渲染库收益不匹配成本，**已决策不做** |
| ⚠️ 离线推理未验证 | openPangu-7B AWQ 未实测 |

## 🧱 系统架构

```
用户文本输入 → Vue前端 → FastAPI后端
                        ↓
              MCP Agent（openPangu盘古大模型）
                        ↓
        ┌──────────┬──────────┬──────────┬──────────┐
        ▼          ▼          ▼          ▼
    RAG知识库   RDKit化学引擎 3D分子生成 Fay数字人服务
        ↓          ↓          ↓          ↓
        └──────────┴──────────┴──────────┘
                        ↓
             汇总结果返回前端
    【文字机理 + 3D分子动画 + 数字人语音讲解】
```

## 🛠️ 技术栈 Tech Stack

>以下区分**代码里实际在用**与**依赖清单里列了但尚未 import**。
> 后者不是错误（清单先于实现），但**不应让人以为已经用上了**。

### Frontend 前端（实测依赖，2026-10-05 核对 `package.json`）

| 库 | 版本 | 用途 |
| --- | --- | --- |
| Vue 3 | 3.5.43 | 框架 |
| Vite | 8.3.2 | 构建 |
| Pinia | 4.0.3 | 状态管理 |
| Three.js | ^0.186.1 | 3D 分子渲染 |
| marked | ^18.0.14 | Markdown 渲染（**含自写消毒层**，见下） |
| TypeScript / vue-tsc | 6.0.3 / 3.3.12 | 类型 |
| Vitest + playwright | 5.0.3 / 1.63.0 | 单测 / 运行时走查 |

**实现要点**

- **未用 TailwindCSS**，样式是手写 CSS（`<style scoped>` + CSS 变量主题）。
- **未用 Axios**，走原生 `fetch`（含 SSE 流式读取）。
- **未用 Molstar**，3D 由Three.js 直接实现（RDKit 生成坐标 → 球棍模型）。
- **Markdown 渲染自带白名单消毒**（`frontend/src/api/markdown.ts`）——
  不引入 DOMPurify，规则可审计；实测挡住 `<script>` / `onerror` /
  `javascript:` / `style` 注入。

### Backend 后端（Python 3.12）

**代码里实际使用的第三方库**（实测逐一 grep 源码得出）：

| 库 | 用途 | 导入方式 |
| --- | --- | --- |
| FastAPI + Uvicorn | 异步 Web 服务 | 顶层 |
| Pydantic | 请求数据校验 | 顶层 |
| RDKit | 分子解析、官能团 SMARTS | 顶层 |
| ChromaDB | 向量库（`rag/`） | 延迟导入，缺失时降级 |
| sentence-transformers | 中文嵌入（`BAAI/bge-small-zh`） | **函数内延迟导入** |
| langchain-openai | 模型适配（`ChatOpenAI`） | **函数内延迟导入** |

> **为何多处用函数内延迟导入**：这些库是可选重依赖，
> 缺失时抛受控错误码并降级，而不是让整个服务起不来。
> 故仅 grep 顶层 import 会**低估实际使用情况**——
> 我第一版就是这样漏判了 chromadb / langchain，改用全量 grep 复核。

**未使用**：Loguru、python-dotenv、requests、Pandas
（日志用标准库 `logging`，HTTP 用 `urllib`，配置读 `os.environ`）。
依赖清单里列了它们，属清单超前于实现。

### 依赖清单与代码的关系

`requirements*.txt` 是**计划全量清单**（含本地 openPangu-7B AWQ 推理依赖、
LangChain 等）；**实际代码只用了上表那些**。
清单超前于实现是正常的，但安装时按 `requirements_cloud.txt` 即可跑通当前代码
（实测装 118 个包，见[验证报告](docs/py312-install-verification.md)）。

### LLM 大模型（华为 openPangu 盘古）

- 云侧开发：**openPangu-2.0-Flash**，512K 上下文，原生 Function Call
  （实测工具调用 2.72 秒、流式 47 块，见 `decision-register.md` B4）
- 本地离线 Demo：openPangu-7B-Instruct，AWQ 4bit 量化
  📋 **目标设计**，尚未实测

### 数字人模块

- Edge-TTS：轻量级语音合成
- Fay 数字人服务进程：表情、唇动驱动
- 🚫 **本机限制**：Fay 在 Linux 容器**不产生音素**（`Lips` 为空，
  源码 `fay_core.py:2270` 的 `if platform.system() == "Windows"`），
  故口型是**本地近似**，非真实音素同步

### 工程工具

- Python 3.12（基线，见 `python-version-evaluation.md`）
- Node.js 18+（前端构建）
- Git
- Docker（**本机跑 RDKit 的唯一途径**，见 `local-run-guide.md`）

## 📂 项目目录结构

```
Xuezhi-OrganicAgent/
├── backend/                    ✅ 后端源码（43 个 Python 模块）
│   ├── app/
│   │   ├── api/                路由、依赖注入、错误码
│   │   ├── agent/              Agent 编排与工具调度
│   │   ├── chem/               RDKit 引擎与官能团 SMARTS
│   │   ├── knowledge/          知识库构建
│   │   ├── llm/                模型适配与配置
│   │   ├── rag/                检索与嵌入
│   │   └── speech/             TTS 与 Fay 数字人通信
│   ├── data/                   知识库数据（讲义、索引）
│   └── tests/                  后端测试（容器内执行，见下）
├── frontend/                   ✅ 前端源码（20 个 TS/Vue 模块）
│   ├── src/
│   │   ├── api/                HTTP 客户端 + Markdown 渲染（含消毒）
│   │   ├── components/         问答主视图、数字人、3D 分子等
│   │   ├── stores/             Pinia 状态
│   │   ├── styles/             全局样式与主题变量
│   │   ├── types/              接口类型定义
│   │   └── viz/                Three.js 3D 渲染与形象状态机
│   ├── e2e/                    浏览器走查脚本
│   └── tests/                  前端单测（166 passed）
├── scripts/
│   ├── run-local.sh            ✅ 本机一键启动（--check 只做自检）
│   ├── build_index.py          知识库索引构建
│   └── export_openapi.py       导出 OpenAPI 契约
├── docs/                       文档体系（含 api/openapi.json）
├── tests/Dockerfile.test       测试执行环境
├── docker-compose.yml          ✅ 容器编排（api / frontend / fay）
├── .env.example                配置模板
├── requirements*.txt           Python 依赖清单
└── README.md
```

> 🚫 **本机 RDKit 被系统策略拦截**（Smart App Control / WDAC，
> 错误码 `WinError 4551`），**换任何安装方式都无效**（已实测三条路）。
> 故化学能力须在**容器内**运行——这是本机跑通项目的唯一途径，
> 详见[本机运行指南](docs/local-run-guide.md)。

## 🚀 快速启动 Quick Start

> ✅ **本节已可用**：仓库含完整源码与容器编排。
> 下面命令均来自 `scripts/run-local.sh`（实测运行过）。

### 方式一：Docker（推荐，化学能力完整）

RDKit 在本机被系统策略拦截，**只有容器内不受管辖**。

```bash
# 1. 配置（MAAS_API_KEY 必填，否则问答接口返回 llm_not_configured）
cp .env.example .env      # Windows PowerShell: Copy-Item .env.example .env

# 2. 起服务
docker compose up -d

# 3. 验证
curl http://127.0.0.1:8000/health     # 应返回 status:"ok"、ready:true
```

启动后可访问：

| 地址 | 说明 |
| --- | --- |
| http://127.0.0.1:8000/health | 健康检查 |
| http://127.0.0.1:8000/docs | Swagger 交互文档 |
| http://127.0.0.1:5173 | 前端界面 |
| http://127.0.0.1:8000/ | 后端根 |

`docker-compose.yml` 定义三个服务：`api`（后端）、`web`（前端）、
`fay`（数字人，仅容器内可达，不对外暴露 10002/10003）。

### 方式二：脚本（本机开发）

```bash
./scripts/run-local.sh --check    # 只自检，报告哪些组件可用
./scripts/run-local.sh            # 启动服务
./scripts/run-local.sh --port 9000 --reload
```

> ⚠️ 本机 RDKit 被拦，故脚本下**化学结构解析不可用**
> （`/health` 会如实报 `chem: ready=false`）。其余功能正常。

### 方式三：手动安装（仅后端）

```bash
conda create -n xuezhi python=3.12
conda activate xuezhi
pip install -r requirements_cloud.txt
conda install -c conda-forge rdkit=2026.3.6
uvicorn app.api.app:app --app-dir backend --port 8000
```

**Python 基线为 3.12**（由 `docs/python-version-evaluation.md` 实测确定）。

- `requirements_cloud.txt` 不含 `autoawq` / `transformers` / `accelerate`，
  但**仍会装 torch**——`sentence-transformers` 硬依赖 `torch>=2.2`
- 版本上界见 `requirements-lock.txt`，但它**尚不是可复现锁定**
  （torch / autoawq 因平台原因未锁，hash 待补，见 `decision-register.md` G2–G4）

## ⚠️ 项目约束与局限性

**功能范围**

1. 仅支持有机化学反应，暂不处理离子反应
2. 输入仅支持自然语言文本，无图像识别能力
3. 知识库限定高中化学课标，不会输出大学阶段超纲化学解释

**技术限制（均为实测确认）**

4. 🚫 **本机 RDKit 不可用**——被 Smart App Control / WDAC 拦截
   （`WinError 4551`），换路径/ 换 Python 版本 / 换发行来源、
   conda-forge 均**实测无效**。化学能力须在容器内运行
5. 🚫 **无真实音素口型**——Fay 的 `Lips` 仅 Windows 生成，容器内恒空
6. 🚫 **3D 不支持鼠标拖拽**——缺 `OrbitControls`，仅自动旋转
7. ⚠️ **离线推理未验证**——openPangu-7B AWQ 未实测
8. ⚠️ **依赖清单非可复现锁定**——`requirements-lock.txt` 中
   torch / autoawq 因平台原因未锁，hash 待补（`decision-register.md` G2–G4）

**说明能力的边界**

文档中凡标📋 **目标设计** 的条目均**未实现或未验证**，
不应在对外材料中作为已交付能力描述——
本项目的教训是「代码就绪」不等于「效果成立」
（人设实测对人设文本无影响、表格曾被转义成源码，
详见 `docs/frontend-verification.md` H28/H29/H30）。

## 📜 License

本项目仅用于创新竞赛、教育科研演示，非商用。

> ⚠️ 依赖 **Fay**（数字人）。Fay 是 **GPL-3.0** 许可。
> 本项目只通过 HTTP 调用其 `/transparent-pass` 接口，
> **不分发其代码**，故不触发 copyleft
> （业界通行理解，**非法律意见**）。

