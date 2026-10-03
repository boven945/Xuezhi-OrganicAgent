# 学智有机 | Xuezhi-OrganicAgent

### ✨ 面向高中有机化学的MCP智能体教学系统

> 本仓库当前提供项目文档与 Python 依赖清单。文中系统能力和目录结构描述目标设计；对应模块实现并验收前，不应视为已交付能力。

## 文档导航

- [文档总览](docs/README.md)
- [开发流程与分支规范](docs/development-workflow.md)
- [产品范围与验收标准](docs/product-scope.md)
- [系统架构与组件职责](docs/architecture.md)
- [部署与运维手册](docs/deployment-operations.md)
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
- [torch 移除方案评估](docs/h8-torch-removal-evaluation.md)
- [决策登记表](docs/decision-register.md)

> 参与开发前请先阅读[开发流程与分支规范](docs/development-workflow.md)：每个模块的开发与更新在独立分支上进行。

> 华为创新赛 · 教育赛道参赛项目

## 📖 项目简介

**学智有机（Xuezhi-OrganicAgent）** 面向高中有机化学教学，目标是构建基于 MCP 的智能体调度系统，并使用华为 openPangu 盘古大模型。

目标交互形式为学生通过**文本提问**与系统交互；目标能力包括知识点检索、化学结构解析、适用的化学校验、**动态3D有机反应分子动画**和 Fay 数字人语音讲解。各项能力须以实现和验收状态为准。

知识库设计目标是限定在高中课程范围，区分课标内容与拓展理论，减少不同学段知识混用。

> ⚠️ 本项目**仅支持文本输入，无图像识别模块**


## ⭐ 目标能力

1. **华为openPangu驱动MCP化学智能体**
基于盘古大模型实现Agent自主规划任务，按需调用各类工具，并非简单问答，原生支持Function Call工具调用，适配华为云昇腾生态。
2. **分层RAG知识库**
知识库以高中化学教材、课标、高考习题为数据源；做知识过滤，剔除大学超纲内容，保证输出符合高中生认知水平。
3. **文本驱动3D分子动态仿真**
用户输入自然语言描述有机反应，后端自动生成SMILES分子表达式；前端渲染球棍/填充模型，展示断键、成键动态动画。
4. **Fay数字人助教**
将生成的教学脚本送入数字人模块，自动合成语音，同步驱动面部表情与唇形，实现沉浸式拟人授课。
5. **云端+离线双部署模式**

- 开发调试：华为云ModelArts MaaS，调用openPangu-2.0-Flash
- 现场离线Demo：openPangu-7B-Instruct（AWQ 4bit量化），消费级显卡本地推理

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

### Frontend 前端

- Vue 3 + Vite，Node.js 18+
- TailwindCSS
- Three.js + Molstar：3D分子可视化渲染
- Fay数字人前端SDK
- Axios：前后端接口通信

### Backend 后端（Python）

- FastAPI + Uvicorn：异步Web服务
- Pydantic：请求数据校验
- Loguru：日志管理
- python-dotenv：环境配置
- requests：HTTP请求
- NumPy、Pandas：数据处理
- LangChain：Agent编排、提示词管理
- 自定义MCP调度层：智能体工具调用
- RDKit：分子解析、反应合法性校验
- ChromaDB：向量数据库
- Sentence-Transformers：文本Embedding

### LLM 大模型（华为openPangu盘古）

- 云侧开发：openPangu-2.0-Flash，512K上下文，原生Function Call
- 本地离线Demo：openPangu-7B-Instruct，AWQ 4bit量化

### 数字人模块

- Edge-TTS：轻量级语音合成
- Fay数字人服务进程：表情、唇动驱动

### 工程工具

- Python 3.12
- Git
- Conda / venv
- Docker（可选，容器一键打包）

## 📂 项目目录结构

```
Xuezhi-OrganicAgent/
├── README.md
├── requirements.txt
├── requirements_cloud.txt
├── requirements-lock.txt
├── .gitignore
└── docs/
  ├── README.md
  ├── development-workflow.md
  ├── decision-register.md
  ├── dependency-notes.md
  ├── python-version-evaluation.md
  ├── h1-h3-verification.md
  ├── py312-install-verification.md
  ├── chem-engine-verification.md
  ├── llm-adapter-verification.md
  ├── agent-dispatcher-verification.md
  ├── rag-verification.md
  ├── h8-torch-removal-evaluation.md
  ├── product-scope.md
  ├── architecture.md
  ├── deployment-operations.md
  ├── knowledge-base.md
  ├── security-privacy.md
  └── interface-contract.md
```

以上为当前文档与依赖文件布局。前后端源码、知识库数据、Docker 配置及 Fay 服务接入尚未出现在当前仓库中；后续实现时应按架构文档补齐并更新目录说明。

## 🚀 快速启动 Quick Start

### 1. 创建Python虚拟环境并安装依赖

```
conda create -n xuezhi python=3.12
conda activate xuezhi
pip install -r requirements.txt
conda install -c conda-forge rdkit
```

默认 `requirements.txt` 包含本地 openPangu-7B AWQ 推理依赖。如果只使用华为云 MaaS API 开发调试，可改为安装精简依赖：

```
pip install -r requirements_cloud.txt
conda install -c conda-forge rdkit
```

安装说明：

- **Python 基线为 3.12**（由 `docs/python-version-evaluation.md` 实测评估确定）。
  3.10 亦可兼容，但会压制 numpy 至 2.2.6、pandas 至 2.3.3，故统一使用 3.12。
- RDKit 强烈建议通过 Conda 安装；直接使用 pip 安装可能遇到兼容性问题。
  conda-forge rdkit 2026.03.6 已提供 py312 构建（覆盖 6 个平台）。
- `torch` 请根据 CUDA 版本从 PyTorch 官网选择对应安装命令；依赖清单中的版本仅供参考。
- **云端精简版实际不含** `autoawq`、`transformers`、`accelerate`，但**仍会安装
  `torch`**——因为 `sentence-transformers` 硬依赖 `torch>=2.2`（实测 2026-10-03：
  Python 3.12.14 下 dry-run 解析出 118 个包，含 torch 及其传递依赖）。
  "不在本机跑大模型"指的是不加载本地权重，不等于不安装 torch。
  详见[验证报告](docs/py312-install-verification.md) §3.1。
- 版本上界约束见 `requirements-lock.txt`，实测依据见[依赖选型实测记录](docs/dependency-notes.md)。
  该文件目前**不是可复现锁定**：torch 与 autoawq 因平台/编译原因未锁定，
  传递依赖与 hash 待补全，详见决策登记表 G2-G4。

### 2. 启动服务

当前仓库没有 `backend/`、`frontend/` 源码目录或可运行入口，因此不能仅凭依赖清单启动系统。实现交付后，请按[部署与运维手册](docs/deployment-operations.md)补齐实际启动命令、配置项和健康检查地址。

## ⚠️ 项目约束与局限性

1. 当前版本**仅支持有机化学反应，暂不处理离子反应**
2. 输入仅支持自然语言文本，无图像识别能力
3. 知识库限定高中化学课标，不会输出大学阶段超纲化学解释
4. 本地离线推理性能受GPU显存影响，可按需调整量化精度

## 📜 License

本项目仅用于创新竞赛、教育科研演示，非商用。

