# 本机运行指南

本文回答一个问题：**在当前这台Windows 机器上，怎么把项目跑起来、跑测试。**

2026-10-04 实测记录。所有结论都来自实跑，非推断。

## 1. 快速开始

```bash
# 环境自检（不启动服务，只报告哪些组件可用）
./scripts/run-local.sh --check

# 启动服务
./scripts/run-local.sh# http://127.0.0.1:8000

# 换个端口 / 开发模式
./scripts/run-local.sh --port 9000
./scripts/run-local.sh --reload
```

首次使用先复制配置模板：

```bash
cp .env.example .env      # 然后填入 MAAS_API_KEY
```

**没有 `MAAS_API_KEY` 也能启动** —— 服务会起来、`/health` 与
`/docs` 可用，只是问答接口返回 `llm_not_configured`。
这样可以先把链路跑通再去申请密钥。

## 2. 本机实测能跑什么

| 组件 | 状态 | 说明 |
| --- | --- | --- |
| fastapi / uvicorn / pydantic | ✅ | |
| **chromadb** | ✅ | **读写查询实测正常**（曾以为不可用） |
| sentence-transformers / torch | ✅ | |
| **langchain / langchain-openai** | ✅ | **包括流式 `.stream()`** |
| **rdkit.Chem** | ❌ **被拦** | Windows 应用控制策略按签名拦截 C++ 扩展 |

### 关于 RDKit 被拦

`rdchem.pyd` 被 Smart App Control / WDAC 拒绝加载，报错：

```
ImportError: DLL load failed while importing rdchem:
应用程序控制策略已阻止此文件。
```

**这不是缺依赖，重装无效。** 三种处置：

| 方案 | 适用 |
| --- | --- |
| 容器内跑（`docker build ... && docker run`） | 推荐，容器不受主机策略管辖 |
| 关闭 Smart App Control | **不建议**——牺牲系统安全策略换开发便利 |
| 本机降级运行 | 服务正常，只是没有化学结构解析（见 §4） |

> **测"能否 import"要测到子模块。** 本项目第一轮自检只查了
> `import rdkit`（通过），但 `from rdkit import Chem` 失败——
> 前者只加载 Python 层，不触碰 C++ 扩展。
> 同理，`import chromadb` 通过不代表能用，须真跑一次
> `PersistentClient` + `add` + `query`。

## 3. 跑测试

```bash
# 本机（不用 Docker）
export PYTHONPATH=backend
.venv-xuezhi312/Scripts/python.exe -m pytest backend/tests/ -q

# 容器（完整环境）
docker build -t xuezhi-chem-test -f backend/tests/Dockerfile.test .
docker run --rm xuezhi-chem-test
```

### 两边的实测结果

| 环境 | 通过 | 跳过 | 失败 |
| --- | --- | --- | --- |
| **本机**（RDKit 被拦） | **443** | 51 | **0** |
| **容器**（RDKit 可用） | **579** | 36 | **0** |

差136 项是化学相关测试——本机跳过，容器正常执行。
**两边都是零失败**，这是关键：跳过不是掩盖问题。

### 跳过机制

`backend/tests/_env.py` 探测 RDKit 可用性，
`backend/tests/conftest.py` 按需跳过：

| 文件 | 机制 | 原因 |
| --- | --- | --- |
| `chem/test_engine.py`等 4 个 | `pytest_ignore_collect` | 文件**顶层**就 import RDKit，收集阶段即失败，`skipif` 来不及生效 |
| `agent/test_dispatcher.py::TestChemTools` 等 2 类 | `@pytest.mark.skipif` | 文件能收集，只是部分用例不适用 |

跳过原因在 `pytest -rs` 下可见，且会区分两种情况
（**被策略拦截** vs **未安装**）——处置完全不同。

> 刻意区分这两者：若笼统报"RDKit 不可用"，人会以为是缺依赖
> 去反复 `pip install`，而真正的原因是装也没用。

## 4. RDKit 不可用时的行为

服务**照常启动**，不做静默假装正常：

```json
GET /health →
{
  "status": "degraded",
  "ready": true,
  "components": [
    {"name": "chem", "ready": false, "detail": "ImportError"},
    {"name": "llm",  "ready": true,  "detail": "已配置"},
    {"name": "rag",  "ready": true,  "detail": "已加载"}
  ]
}
```

| 接口 | RDKit 不可用时|
| --- | --- |
| `GET /health` | ✅ 如实报告 `chem: ready=false` |
| `GET /docs` | ✅ 正常 |
| `GET /openapi.json` | ✅ 正常 |
| `POST /api/v1/ask` | ✅可用（Agent 降级为**仅知识检索**） |
| `POST /api/v1/ask/stream` | ✅ 可用 |
| `POST /api/v1/molecule` | ❌ 结构解析不可用 |

降级设计见 `app/api/deps.py::get_agent_loop`：
化学工具构造失败时**只挂知识检索工具**继续服务。
依据是 `architecture.md` §6「将文本回答设为主交付」。

## 5. 为此做的两处代码改动

### 5.1 `app/chem/__init__.py` 延迟导出 `engine`

**问题**：原来 `from .engine import ChemEngine, get_engine` 在顶层。
而 `app/api/errors.py` 需要 `app.chem.errors` 来透传 `chem_*` 错误码——
于是**任何** `import app.api.app` 都会连带触发 RDKit 加载，
RDKit 不可用时**整个 API 网关起不来**，连 `/health` 都无法
回答"化学组件不可用"这件事。

**改法**：用 :pep:`562` 的模块级 `__getattr__` 延迟导入。
`from app.chem import ChemEngine` 的用法**完全不变**，
但只有真正访问这两个名字时才导入 engine。

实测效果：
- 修复前：`import app.api.app` → ImportError
- 修复后：`/health` 正常返回 `chem: ready=False`

### 5.2 `app/api/deps.py` 化学工具降级

`get_agent_loop` 原来无条件调`build_chem_tools()`，
RDKit 不可用时抛 `ImportError` 导致 500 且无清晰提示。
现在捕获并降级，同时记日志说明实际启用了哪些工具。

## 6. 网络注意事项

本机**直连极慢（实测 30 kB/s），走代理约 1 MB/s，差 35 倍**：

```bash
export https_proxy=http://127.0.0.1:7897 http_proxy=http://127.0.0.1:7897
```

访问**本机**服务时要绕过代理，否则 curl 会返回 502：

```bash
curl --noproxy '*' http://127.0.0.1:8000/health
```

嵌入模型权重（约 400MB）默认在线下载，
**HuggingFace 直连在本机不通（实测 HTTP 000），须走代理**。
断网演示要预先下载并设`XUEZHI_EMBEDDING_PATH`。

## 7. 常见问题

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| 启动报`应用程序控制策略已阻止此文件` | RDKit 被WDAC 拦 | 见 §2，容器内跑或接受降级 |
| `/ready` 返回 503 | 缺 `MAAS_API_KEY` 或组件未就绪 | 看 `/health` 的组件明细 |
| 问答报 `llm_not_configured` | 未设密钥 | 填 `.env` 后重启 |
| 问答报 `llm_auth_failed` | 密钥无效 | 核对密钥 |
| 首次启动很慢 | 正在下载嵌入模型 | 设 `XUEZHI_EMBEDDING_PATH` 用本地权重 |
| curl 返回 502 | 走了代理 | 加 `--noproxy '*'` |

## 8. 目录约定

| 路径 | 用途 |
| --- | --- |
| `.venv-xuezhi312/` | 本机 venv（Python 3.12.14，uv 装的） |
| `.env` | 本地配置（**已被 .gitignore 忽略**） |
| `.env.example` | 配置模板（可提交，**不得含真实密钥**） |
| `scripts/run-local.sh` | 启动脚本 |
| `backend/tests/_env.py` | 环境能力探测（RDKit 可用性） |
| `backend/tests/conftest.py` | pytest 钩子（按能力跳过） |

## 9. 关于外部方案的两点澄清

曾参考过"chromadb 分离部署"与"弃用 langchain_openai"两个方案，
理由都是 `uuid_utils.dll` 被拦。**实测该文件现已可导入**，
`langchain_openai` 也能正常构造 `ChatOpenAI`（含`streaming=True`），
两个方案**都不需要执行**。

若将来 `uuid_utils` 真的再次被拦，注意它是
`langchain-core` 与 `langsmith` 的**共同依赖**——
届时整个 LangChain 都会不可用，"只弃用 langchain_openai"不成立。
届时的正确做法是自己封装 OpenAI 兼容协议调用 MaaS。
