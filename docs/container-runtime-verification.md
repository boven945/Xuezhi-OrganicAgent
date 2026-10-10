# 容器化运行 RDKit 与错误文案补齐

本文记录两件事：一是**用容器运行完整后端**以绕开本机应用控制策略对 RDKit 的拦截（决策项 H13），二是该次回归**顺带发现并修掉的错误文案缺陷**。

日期：2026-10-04。所有结论均为本机实测，不含推测值。

---

## 1. 背景：本机 RDKit 为何不可用

本机启用了 Smart App Control（`VerifiedAndReputablePolicyState=1`）。按二进制签名拦截未签名的第三方原生扩展，RDKit 的 C++ 扩展被逐文件拦截：

```text
import rdkit           → 成功（仅 Python 层）
from rdkit import Chem → WinError 4551（ERROR_BLOCKED_BY_POLICY）
```

深度实测结论（详见 `chem-engine-verification.md` §2）：与路径、Python 版本、发行来源均无关；`rdchem.pyd` 依赖的 7 个 RDKit DLL 中有 3 个被拦（`RDKitGraphMol.dll`、`RDKitSmilesParse.dll`、`RDKitSubstructMatch.dll`）。

---

## 2. 为什么选「容器跑完整后端」而不是「只把 RDKit 拆成服务」

曾评估过一个方案：把 RDKit 单独做成 HTTP 服务（容器暴露 `localhost:8001`），主机跑FastAPI 与前端，主机通过 HTTP 调容器。**该方案在本项目不采纳**，理由如下。

### 2.1 拆分化学能力会显著扩大失败面

本项目的化学能力不是「一个生成三维坐标的函数」。`backend/app/chem/engine.py`（456 行）承担：

- 12 条官能团 SMARTS 匹配（其中醇羟基/酚羟基的拆分是决策项 I5，实测乙酸不误报、苯酚能命中）
- 分子式、分子量、环数、杂原子数等性质计算
- 原子表与键表输出（含共价半径、外层电子数）
- ETKDG 嵌入 + MMFF 优化的三维坐标（固定随机种子以保证可复现）
- 输入长度与重原子数上限（`security-privacy.md` §4 的资源防护）
- 受控错误码（`chem_invalid_structure` 等，见 §5）

改为跨进程 HTTP 调用意味着新增：序列化协议、网络失败模式（超时/连接拒绝/容器重启）、`ChemError` → HTTP 状态码 → `ChemError` 的往返映射，以及这些路径的契约测试。

**为了绕开 DLL 拦截，反而引入了更复杂的失败面**——这与`architecture.md` §6「将文本回答设为主交付，避免为可选能力牺牲整体可用性」相悖。

### 2.2 容器跑完整后端已实测可行

把整个后端放进容器后，RDKit 在容器内正常加载，`chem/engine.py` **一行都不用改**：

```text
GET /health
{"status":"not_ready","ready":false,"components":[
  {"name":"chem","ready":true,"detail":"RDKit 就绪"},
  {"name":"llm","ready":false,"detail":"LLMConfigError"},
  {"name":"rag","ready":true,"detail":"已加载"}],"schema_version":"1.0"}
```

`chem: ready=true` 即本机 RDKit 拦截问题已解决。`llm` 为 false 仅因本次探测未注入 `MAAS_API_KEY`，属预期配置状态。

### 2.3 对前端的影响为零

`frontend/vite.config.ts` 已支持 `VITE_BACKEND_URL` 环境变量指向后端；前端 `API_BASE` 默认同源相对路径。启动容器时把该变量指向 `http://127.0.0.1:8000` 即可，**前端代码无需修改**。

---

## 3. 外部方案中三处与本项目冲突之处

评估外部提供的 Docker 方案时，实测发现三处冲突，**不应直接照抄**。

### 3.1 `libboost-all-dev` 不需要

该方案在 Dockerfile 中装了 `libboost-all-dev`。实测不需要：PyPI 的 `rdkit` wheel 自带全部 `.so`/`.dll`，官方说明与 wheel 维护者仓库均明确「wheel bundles everything，无 Boost、无编译器」。

本项目既有的 `backend/tests/Dockerfile.test` 未装任何 boost，容器内 RDKit 实测正常（`Chem.MolToSmiles(Chem.MolFromSmiles('CCO'))` → `CCO`，分子量 46.07）。装它只会白增数百 MB 与构建时间。

### 3.2 `python:3.11-slim` 会造成基线倒退

本项目 Python 基线是 **3.12**（决策登记表 H1），选它的理由是解除 numpy（→2.5.3）与 pandas（→3.0.6）的版本压制，且决策时无源码迁移成本。回退到 3.11 会重新引入这些约束。

### 3.3 版本须锁定而非 `>=`

该方案用 `pip install rdkit>=2024.03.2`。本项目要求锁定版本（`requirements-lock.txt` / E4），既有测试镜像锁 `rdkit==2026.3.6`，容器内实测该版本可用。

### 3.4 另需注意：Git Bash 会破坏容器路径

在Windows Git Bash 下执行 `docker run -e PYTHONPATH=/work/backend ...` 时，Git Bash 会把 `/work/backend` 当作 Windows 路径展开，导致容器内报 `ModuleNotFoundError: No module named 'app'`：

```text
#实际传进容器的值（路径被展平，语义完全错误）
PYTHONPATH=C:/Users/Lenovo/.workbuddy/binaries/PortableGit/versions/1.2.0/work/backend
```

**解法**：命令前加 `MSYS_NO_PATHCONV=1`。这与 RDKit 无关，是本机 shell 环境陷阱，容易误判为镜像缺依赖。

---

## 4. 决策与操作方式

**决策（H13）**：本机演示与开发统一用容器运行完整后端；不修改Smart App Control，不为绕过策略污染产品代码。

理由（依据已联网核实）：

- WDAC / AppLocker 管辖的是 **Windows PE 可执行文件**，对 WSL2 内的 **Linux ELF 二进制不生效**——官方与企业实践文档均确认这一点。Docker Desktop 的 WSL2 后端因此天然不受该策略管辖。
- **不解除策略限制**：Smart App Control 一旦关闭，**不重置或重装 Windows 就无法再开启**（微软官方明确说明）。为跑通一个 Python 扩展而永久关闭整机安全防护，代价与收益完全不成比例。

**启动命令**：

```bash
MSYS_NO_PATHCONV=1 docker run --rm -p 8000:8000 \
  -e PYTHONPATH=/app -e XUEZHI_CHROMA_PATH=/data/chroma \
  xuezhi-chem-test \
  python -m uvicorn app.api.app:app --host 0.0.0.0 --port 8000
```

配套环境变量（`.env` 或 `docker run -e`）：

| 键 | 作用 |
| --- | --- |
| `XUEZHI_CHROMA_PATH` | 向量库目录，须指向挂载卷中的路径 |
| `XUEZHI_EMBEDDING_PATH` | 断网演示须指定，指向本地权重目录（实测 91.4 MiB） |
| `MAAS_API_KEY` | 缺则 `/ready` 返回 503，但服务可启动 |

**演示前检查**：容器需提前启动；嵌入模型在容器内首次加载需数秒。

**内存**：WSL2 默认最多可用主机内存的 50%（本机 16GB → 上限 8GB），并可开启 Resource Saver 在空闲时回收。本项目的实际需求远低于该上限。

---

## 5. 本次回归发现的缺陷：错误文案全部落入通用兜底

容器内跑通后，逐个接口验证时发现**一个此前未暴露的真实缺陷**。

### 5.1 现象

学生输入非法结构式（实测 `C1CC`，少一个右括号）时，返回：

```json
{"error":{"code":"chem_invalid_structure","message":"服务暂时不可用，请稍后重试。","retryable":false}}
```

**误导性文案比报错更糟**：服务一切正常，错的只是学生少写了一个括号。「请稍后重试」暗示重试就能好，学生会反复重试而不去检查自己的输入。

### 5.2 根因

`app/api/errors.py` 的 `DOMAIN_MESSAGES` 表**完全没有 chem 段**。`public_message` 的文案优先级是：API 层码 → 下层码（查 `DOMAIN_MESSAGES`）→ 通用兜底。chem 的 4 个码都不在表中，于是全部落到「服务暂时不可用」。

### 5.3 修复范围比预期大

补齐 chem 段后，新增了**反向一致性检查**，它立刻又查出7 个缺失：

- `tool_error`、`tool_not_found`、`tool_argument_invalid`、`tool_execution_failed`
- `agent_internal_error`、`llm_internal_error`、`rag_internal_error`

以及 `DOMAIN_CODE_SPECS`（错误码 → HTTP 状态码）中缺失的 `rag_invalid_document` 等。

### 5.4 状态码的一处判断

`rag_invalid_document` 定为 **500 而非 4xx**：它是入库前拦截的**服务端数据问题**（无来源/授权不明/超纲），客户端改输入也修不好，重试只会再撞同一份坏数据。归 4xx 会误导前端以为是用户的请求有问题。

而 `tool_argument_invalid` 定为 400：它确实是调用方请求不合法，与 chem 的输入问题同类。

### 5.5 为什么原测试没抓到

`TestDomainMessageConsistency` 原本**只查一个方向**：「文案表里的码是否都真实存在」（孤儿码），**不查缺失**。于是 chem 整段缺失时，几百个测试全绿。

孤儿码是有害但冗余（写了没用的文案），缺失码让学生拿到误导提示（有害）。**两个方向都得查**，而后者恰好是原测试的盲区。

---

## 6. 补齐的错误文案

| 错误码 | HTTP | 文案 |
| --- | --- | --- |
| `chem_invalid_structure` | 400 | 结构式无法识别，请检查括号是否配对、元素符号是否正确。 |
| `chem_unsupported_structure` | 400 | 该结构超出本工具的分析范围，请换用更简单的结构。 |
| `chem_structure_too_large` | 400 | 结构式过于复杂，本工具只处理较小的分子。 |
| `chem_internal_error` | 500 | 化学分析出错了，请稍后重试。 |
| `tool_not_found` | 403 | 当前问题超出了工具能处理的范围，请换种方式提问。 |
| `tool_argument_invalid` | 400 | 工具收到的参数不合法，请检查输入内容。 |
| `tool_execution_failed` | 500 | 分析工具执行失败，答复可能不完整。 |
| `tool_error` | 500 | 工具调用出错，请稍后重试。 |
| `agent_internal_error` | 500 | 推理过程出错了，请稍后重试。 |
| `llm_internal_error` | 500 | 模型服务出错了，请稍后重试。 |
| `rag_internal_error` | 500 | 知识库检索出错了，请稍后重试。 |
| `rag_invalid_document` | 500 | 知识库中存在不合规的文档，请联系维护者。 |

`tool_not_found` 特意不写「暂时不可用」：它不是故障，而是**白名单机制生效的体现**（模型请求了未注册的工具 = 安全边界被触碰，见 `agent/errors.py`），默认不可重试。正确动作是把问题改回知识问答范围。

---

## 7. 新增的机械化检查

`backend/tests/api/test_errors_ratelimit.py` 新增 4 项，全部针对本次暴露的盲区：

| 检查 | 防止什么 |
| --- | --- |
| `test_every_real_error_code_has_a_message` | 文案表**缺失**码（本次 chem 整段的根因） |
| `test_every_real_error_code_has_a_status_spec` | 状态码表缺失，导致落入保守默认 500 |
| `test_chem_invalid_structure_message_is_actionable` | 文案指向「重试」而非「改输入」——结构断言抓不到，只能按场景钉 |
| `test_input_error_codes_are_4xx` | 输入类错误被判为 5xx，前端会误当服务端故障 |

另抽出 `_collect_real_error_codes()` 供两个方向的检查共用。

**反向验证有效性**：临时删掉 `chem_invalid_structure` 文案行后重跑，4 项测试立刻失败——确认这些检查真能捕获问题，而非恒真断言。

同时修正了一个历史测试的前提失效：`test_public_message_hides_domain_message` 原用 `InvalidStructureError` 当「未登记码」的反例，依赖「chem 未登记」这一前提；chem 补齐后该前提失效（本层文案也含「括号」二字），改用 `ToolTimeoutError`。该用例验证的是**机制**（不透传异常自带文案），不该被文案表内容变化牵连。

---

## 8. 实测记录

### 8.1 容器内化学能力

苯酚 `c1ccccc1O`：

| 项 | 结果 |
| --- | --- |
| canonical SMILES | `Oc1ccccc1` |
| 分子式 / 分子量 | `C6H6O` / 94.11 |
| 官能团命中 | 酚羟基、苯环 |
| viz schema | `molecule-structure/v2` |
| conformer | `ready` |
| render_atoms / coords | 13 / 13 |

### 8.2 官能团精度回归（决策项 I5）

| SMILES | 命中 | 判定 |
| --- | --- | --- |
| `CC(=O)O`（乙酸） | 羧基 | 正确，不误报羟基 |
| `CCO`（乙醇） | 醇羟基 | 正确 |
| `c1ccccc1O`（苯酚） | 酚羟基、苯环 | 正确，不误报醇羟基 |

### 8.3 错误语义修复前后对比

| | 修复前 | 修复后 |
| --- | --- | --- |
| HTTP 状态 | 400 | 400 |
| 错误码 | `chem_invalid_structure` | `chem_invalid_structure` |
| 文案 | 服务暂时不可用，请稍后重试。 | 结构式无法识别，请检查括号是否配对、元素符号是否正确。 |

### 8.4 测试统计

```text
620 passed, 36 skipped in 21.81s
```

36 项跳过为需真实外部服务（模型 API、语音）的用例，非回归。

---

## 9. 仍未解决的事项

| 事项 | 状态 | 阻塞原因 |
| --- | --- | --- |
| `MAAS_API_KEY` 未配置 | 未验证 | 需真实密钥；本次 `/ready` 返回 503 属预期 |
| SSE 逐token 流的真实 MaaS 实测 | 未验证 | 同上，容器内未做带真实模型的端到端 |
| 容器镜像与测试镜像共用 | 现状 | 目前用 `Dockerfile.test` 起服务。生产部署需独立 `Dockerfile`，属infra 模块 |
| 断网演示的嵌入模型权重 | 需预置 | 91.4 MiB（实测 95,842,633 字节），须经 `XUEZHI_EMBEDDING_PATH` 指定 |
| 4xx/5xx 语义的前端处理 | 未验证 | 前端是否正确区分「改输入」与「重试」需E2E 确认 |

---

## 10. 关联文档

- `chem-engine-verification.md` §2 —— 本机 RDKit 被拦截的完整实测
- `local-run-guide.md` —— 本机与容器的能力差异
- `interface-contract-verification.md` —— API 契约与框架行为差异
- `decision-register.md` H13 —— 本决策的登记项
- `security-privacy.md` §4 —— 输入限长与资源防护
- `product-scope.md` §5 —— 「结构非法」与「超出支持范围」为何必须分开
