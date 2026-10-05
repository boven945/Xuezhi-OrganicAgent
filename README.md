# 学智有机 | Xuezhi-OrganicAgent

<div align="center">
<br>
<h3>面向高中有机化学的 AI 助教</h3>
<p>华为 openPangu 盘古大模型 · 分层 RAG 知识库 · 3D 分子可视化 · Fay 数字人讲解</p>
</div>

---

## 这是什么

**学生问一道高中有机化学的难题，系统给出的答案里，每一个结论都标得出教材出处。**

有机化学是高中最难的科目之一：反应条件记不住、相似物质分不清、
方程式写不对。学生的问题往往很具体——「乙醇能不能和碳酸钠反应
放出二氧化碳」——而通用 AI 容易编出看似合理的错误答案，
**学生没有能力判断真假**。

学智有机做的事是：**让答案可核查**。

- 回答里的关键结论会附**教材引用**，学生能自己翻书核对
- 结构相关的输入会真的用 RDKit **算一遍**，而不是猜
- 检索范围**限定在高中课标**，不混入大学超纲内容

### 能做什么

| 能力 | 说明 |
| --- | --- |
| 📖**问答带引用** | 回答附出处，5 条来源可点开核对 |
| 🧪 **结构解析** | 输入 SMILES 得 3D 结构、官能团识别、性质计算 |
| 🔍 **官能团识别** | 12 条 SMARTS 规则，区分醇羟基与酚羟基等同易混官能团 |
| 🎤 **数字人讲解** | Fay 数字人形象 + 语音朗读（注意：口型为近似，非真实音素同步） |
| 🔄 **3D 可交互** | 鼠标拖拽旋转、滚轮缩放，可从任意角度观察结构 |
| 📊 **知识检索** | 41 条自编讲义 / 21 个主题，限定高中课标 |

### 快速体验

```bash
cp .env.example .env      # 填入 MAAS_API_KEY（华为云MaaS 申请）
docker compose up -d     # 启动
```

打开 http://127.0.0.1:5173 ，试试这三个问题：

- 「苯酚为什么比碳酸弱酸？」—— 看引用溯源与结构解析
- 「乙醇能不能和碳酸钠反应放二氧化碳？」—— 看 RDKit 校验
- 「苯使溴水褪色和苯酚使溴水褪色有什么本质区别？」—— 看易错点辨析

> **为什么强调「用Docker」**：RDKit 的 C++ 扩展会被 Windows
> 应用控制策略（Smart App Control / WDAC）拦截，
> `WinError 4551`，**换任何安装方式都无效**（已实测三条路）。
> 容器内的二进制不受主机策略管辖，这是本机跑通化学能力的唯一途径。

### 它不是什么

诚实地说明边界，避免误判：

- **不是图像识别**——只支持文字提问
- **不处理离子反应**——目前只做有机化学反应
- **不是真人数字人**——形象是 3 张静态图按状态切换，
  唇形是本地近似的动画，不是音素同步
- **知识库是自编讲义**（41 条），不是教材原文，避免版权问题；
  内容仍待化学教师审核

---

## 📊 实测数据

| 指标 | 实测值 |
| --- | --- |
| 问答响应 | 12–21 秒（含首次冷启动） |
| 检索命中率 | top-1 81.8%（27/33）、top-3 96.97%（32/33） |
| 引用来源 | 每次 5 条 |
| 前端测试 | 166 passed |
| 后端测试 | 752 passed / 40 skipped / 0 failed |

> 检索评测的探针标注由项目组整理，**未经化学教师审核**，
> 不构成质量结论。详见 `docs/decision-register.md` H18。

## 🏗️ 技术架构

```
学生提问 → Vue 前端 → FastAPI 后端
                    ↓
          MCP Agent（openPangu 盘古，规划 + 工具调用）
                    ↓
     ┌──────────┬──────────┬──────────┬──────────┐
     ▼          ▼          ▼          ▼
  RAG 知识库  RDKit 引擎  3D 分子生成  Fay 数字人
 （限定课标） （真算结构） （Three.js） （表情+语音）
     └──────────┴──────────┴──────────┴──────────┘
                    ↓
        文字讲解 + 引用出处 + 3D 结构 + 语音朗读
```

**技术选型**：Vue 3 + Three.js（前端）／FastAPI + RDKit + ChromaDB +
openPangu 盘古（后端）／Docker（绕开系统策略的唯一途径）

> ⚠️ Markdown 渲染自带**白名单消毒层**（不使用 DOMPurify，
> 规则可审计），实测挡住 `<script>` / `onerror` / `javascript:` 注入。

---

## 📚 给开发者

<details>
<summary><b>技术栈（实测核对，2026-10-05）</b></summary>

**前端**（`frontend/package.json` 实测）

| 库 | 版本 | 用途 |
| --- | --- | --- |
| Vue 3 | 3.5.43 | 框架 |
| Vite | 8.3.2 | 构建 |
| Pinia | 4.0.3 | 状态管理 |
| Three.js | ^0.186.1 | 3D 分子渲染 |
| marked | ^18.0.14 | Markdown 渲染（**自带消毒层**） |
| TypeScript / vue-tsc | 6.0.3 / 3.3.12 | 类型 |
| Vitest + playwright | 5.0.3 / 1.63.0 | 单测 / 浏览器走查 |

**未使用**：TailwindCSS、Axios、Molstar——样式是手写 CSS +
CSS 变量主题，HTTP 走原生 `fetch`，3D 由 Three.js 直接实现。

**后端**（Python 3.12，逐一 grep 源码核实）

| 库 | 导入方式 |
| --- | --- |
| FastAPI + Uvicorn + Pydantic | 顶层 |
| RDKit | 顶层 |
| ChromaDB | 延迟导入（缺失时降级） |
| sentence-transformers（`BAAI/bge-small-zh`） | **函数内延迟导入** |
| langchain-openai（`ChatOpenAI`） | **函数内延迟导入** |

> ⚠️ **判断某库是否使用不能只看顶层 import**——本项目大量使用
> 函数内延迟导入（可选重依赖缺失时降级而非崩溃）。

**未使用**：Loguru、python-dotenv、Pandas（日志用标准库 `logging`，
HTTP 用 `urllib`，配置读 `os.environ`）。

</details>

<details>
<summary><b>运行方式（三选一）</b></summary>

**Docker（推荐，化学能力完整）**

```bash
cp .env.example .env      # 填 MAAS_API_KEY
docker compose up -d
curl http://127.0.0.1:8000/health# 应返回 status:"ok"、ready:true
```

| 地址 | 说明 |
| --- | --- |
| http://127.0.0.1:5173 | 前端界面 |
| http://127.0.0.1:8000/health | 健康检查 |
| http://127.0.0.1:8000/docs | Swagger 文档 |

`docker-compose.yml` 三服务：`api`（后端）、`web`（前端）、
`fay`（数字人，仅容器内可达）。

**脚本（本机开发）**

```bash
./scripts/run-local.sh --check    # 只自检，报告哪些组件可用
./scripts/run-local.sh            # 启动
./scripts/run-local.sh --port 9000 --reload
```

> 🚫 本机 RDKit 被拦截，故脚本下**化学结构解析不可用**
> （`/health` 会如实报 `chem: ready=false`）。

**手动（仅后端）**

```bash
conda create -n xuezhi python=3.12 && conda activate xuezhi
pip install -r requirements_cloud.txt
conda install -c conda-forge rdkit=2026.3.6
uvicorn app.api.app:app --app-dir backend --port 8000
```

</details>

<details>
<summary><b>项目结构</b></summary>

```
Xuezhi-OrganicAgent/
├── backend/
│   ├── app/
│   │   ├── api/          路由、依赖注入、错误码
│   │   ├── agent/        Agent 编排与工具调度
│   │   ├── chem/         RDKit 引擎与官能团 SMARTS
│   │   ├── knowledge/    知识库构建
│   │   ├── llm/          模型适配与配置
│   │   ├── rag/          检索与嵌入
│   │   └── speech/       TTS 与 Fay 数字人通信
│   ├── data/             知识库数据（讲义、索引）
│   └── tests/            后端测试（容器内执行）
├── frontend/
│   ├── src/
│   │   ├── api/          HTTP 客户端 + Markdown 渲染（含消毒）
│   │   ├── components/   问答主视图、数字人、3D 分子
│   │   ├── stores/       Pinia 状态
│   │   ├── styles/       全局样式与主题变量
│   │   ├── types/        接口类型定义
│   │   └── viz/          Three.js 3D 渲染与形象状态机
│   ├── e2e/              浏览器走查脚本
│   └── tests/            前端单测
├── scripts/
│   ├── run-local.sh      本机一键启动
│   ├── build_index.py    知识库索引构建
│   └── export_openapi.py 导出 OpenAPI 契约
├── docs/                 文档体系（32 份，含 api/openapi.json）
├── tests/Dockerfile.test 测试执行环境
├── docker-compose.yml
└── .env.example
```

</details>

<details>
<summary><b>技术限制（均实测确认）</b></summary>

| 项 | 原因 |
| --- | --- |
| 🚫 本机 RDKit 不可用 | Smart App Control / WDAC 拦截（`WinError 4551`），三条路均实测无效 |
| 🚫 无真实音素口型 | Fay 的 `Lips` 仅 Windows 生成，容器内恒空 |
| ⚠️ 人设开关默认关闭 | 实测对人设文本**无任何影响**，默认开启属虚假承诺 |
| ⚠️ LaTeX 偶发不渲染 | 3 次采样 0 复现，属偶发；引渲染库收益不匹配成本，已决策不做 |
| ⚠️ 离线推理未验证 | openPangu-7B AWQ 未实测 |
| ⚠️ 依赖清单非可复现锁定 | torch/autoawq 因平台未锁，hash 待补（G2–G4） |

</details>

---

## 📜 文档导航

> 参与开发前请先阅读[开发流程与分支规范](docs/development-workflow.md)：
> 每个模块的开发与更新在独立分支上进行。

> **文档口径（2026-10-05 统一）**
>
> 本仓库**已包含前后端完整源码**（`backend/` 43 个Python 模块、
> `frontend/` 20 个 TS/Vue 模块）、测试与 Docker 编排。
> 各验证文档记录的都是**实跑结论**，不是计划。
> 仍标「目标设计」「未实现」「本机不可用」的条目，
> 是**确实尚未交付或受环境限制**的，请勿当作已交付能力对外描述。

### 核心文档

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

## 📜 License

本项目仅用于创新竞赛、教育科研演示，非商用。

> ⚠️ 依赖 **Fay**（数字人）。Fay 是 **GPL-3.0** 许可。
> 本项目只通过 HTTP 调用其 `/transparent-pass` 接口，
> **不分发其代码**，故不触发 copyleft
> （业界通行理解，**非法律意见**）。

