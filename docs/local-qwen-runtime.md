# 本地 qwen 推理运行时（默认模型底座）

> 2026-11-23 起，项目**不再默认使用华为盘古（openPangu MaaS）**，改用本机
> qwen2.5-7B-Instruct。本文档记录权重位置、运行时选择、启动与验证步骤。
> 云端盘古仍可通过环境变量显式切回（见文末），代码零改动。

## 1. 为什么换

- 协作者机器上没有华为云 MaaS 账号/密钥，也没有盘古权重；
- 本地权重来源项目（`<本地权重所在项目>`）已有可在本地跑的
  qwen2.5-7B-Instruct GGUF 权重，复用可避免重复下载 4.4GB；
- 本项目 LLM 适配层本来就是通用 OpenAI 兼容客户端，切换只需改配置默认值。

## 2. 权重事实（已实测）

| 项 | 值 |
|---|---|
| 目录 | `<本地 qwen 权重目录>` |
| 首分片 | `qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf` |
| 分片数 | 2（约 3.99GB + 0.69GB，合计约 4.4GB） |
| 格式 | GGUF V3，`general.architecture=qwen2`，量化 `Q4_K_M` |
| 上下文 | 训练长度 131072；启动建议设 4096 |
| 工具模板 | `tokenizer.chat_template` 内含 `{%- if tools %}` 分支（Qwen2.5 原生支持） |

**分片加载规则**：`-m` / `--model` 只写第一个分片，llama.cpp 会从元数据
（`split.no` / `split.count` / `split.tensors.count`）自动发现同目录后续分片。
**不要改名或只拷一个分片**，否则加载失败。

## 3. 运行时选择（关键：工具调用必须能解析）

本项目 Agent 依赖 OpenAI 标准 `tool_calls`（`app/llm/client.py::invoke_with_tools`）。
实测发现运行时版本直接决定能否用：

| 运行时配置 | 普通问答 | 标准 tool_calls | 结论 |
|---|---|---|---|
| `llama-cpp-python 0.3.30`，自动使用 GGUF 模板 | ✅ 正常 | ❌ `<tool_call>` 原文落在 `content` | 不可用（Agent 链路断） |
| **同上 + `--chat_format chatml-function-calling`** | ✅ 正常 | ✅ **5/5 实测通过** | **当前推荐（已落地）** |
| 官方 `llama-server`（b12648+）+ `--jinja` | 未实测 | 文档称可 | 备选；GitHub 下载受阻 |

> 根因与修复：0.3.30 自动选到的 Qwen 模板不会把 `<tool_call>` 解析成结构化字段；
> 显式指定 `chatml-function-calling` 格式即可。**另有两处必须配套**：
> ① 客户端须显式传 `tool_choice="auto"`（缺省时老版服务端不启用工具解析，
> 2026-11-23 实测：同样参数缺省只在 content 里说「我将使用工具」）；
> ② 该格式在 `stream=true` + tools 时返回空流，故本地端点默认关闭流式。

## 4. 启动（已实测方案）

本机 conda base 已装 `llama-cpp-python 0.3.30`，只需补一个小依赖：

```bash
/d/anaconda3/python.exe -m pip install starlette_context
```

然后：

```bash
bash scripts/run-local-qwen.sh
```

脚本已带 `--chat_format chatml-function-calling`、
`--model_alias qwen2.5-7b-local`、绑定 `127.0.0.1:8080`。
换解释器/权重/端口用 `QWEN_PYTHON` / `QWEN_MODEL_PATH` / `QWEN_PORT` 覆盖。

**备选（未实测）**：官方 `llama-server` + `--jinja`，需从 `ggml-org/llama.cpp`
nightly release 下载 Windows 二进制（本机 GitHub 直连约 50KB/s，下载中断）。
若已备好二进制，可用 `--jinja --alias qwen2.5-7b-local` 启动同端口服务，
本项目代码无需改动。

`.runtime/` 已被 `.gitignore` 忽略，本地二进制不进版本库。

## 5. 验证（两项都要过）

```bash
# A. 普通问答
curl -s --noproxy '*' http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen2.5-7b-local","messages":[{"role":"user","content":"用一句话回答：乙烯与溴加成的产物是什么？"}],"max_tokens":100,"temperature":0.1}'
# 期望：choices[0].message.content 含「1,2-二溴乙烷」

# B. 工具调用（Agent 链路依赖）
curl -s --noproxy '*' http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen2.5-7b-local","messages":[{"role":"user","content":"请查询乙醇的分子量。"}],"tools":[{"type":"function","function":{"name":"calc_molecular_weight","description":"根据 SMILES 计算分子量","parameters":{"type":"object","properties":{"smiles":{"type":"string"}},"required":["smiles"]}}}],"tool_choice":"auto","max_tokens":300,"temperature":0.1}'
# 期望：message.tool_calls 非空，function.name=calc_molecular_weight；
# 若只有 content 里的 <tool_call> 文本，说明运行时版本不合格，Agent 会降级。
```

本机代理可能劫持 `127.0.0.1`，所以 curl 必须带 `--noproxy '*'`。

## 6. 项目配置

`.env`（复制自 `.env.example`）：

```bash
MAAS_BASE_URL=http://127.0.0.1:8080/v1
MAAS_MODEL=qwen2.5-7b-local
MAAS_API_KEY=          # 本地模式留空即可，代码自动填占位值 local
XUEZHI_LLM_STREAMING=  # 留空=本地自动关流式；云端自动开。可设 1 强制开启
```

嵌入模型仍是本地 `bge-small-zh`（`XUEZHI_EMBEDDING_PATH`），与 LLM 切换无关。
本地关流式后 Agent 按既有降级路径走同步调用，问答链完整可用，只是没有逐 token 效果。

## 7. 已知限制

- **无鉴权**：本地服务只绑 `127.0.0.1`，不要改 `0.0.0.0` 暴露到公网。
- **CPU 推理慢**：conda base 的 llama-cpp-python wheel 无 CUDA，7B Q4 全部在 CPU；
  RTX 5070 Laptop 8GB 足以装下 4.4GB 模型，但需要 CUDA 版 llama.cpp 才能加速。
- **跨项目路径依赖**：权重在 本地权重来源项目里，若该项目被移动/删除，需设
  `QWEN_MODEL_PATH` 指向新位置，否则脚本报「找不到模型权重」。
- **流式与工具不可兼得**：`chatml-function-calling` 下 `stream=true` + tools
  返回空流。本项目本地端点默认关流式（`XUEZHI_LLM_STREAMING`），Agent 走同步调用。
- **7B 化学映射能力有限**：工具调用协议稳定（5/5 返回标准 `tool_calls`），
  但 5 次都把「乙醇」映射成 `COC`（二甲醚）而非 `CCO`——这是模型能力问题，
  不是协议缺陷。涉及具体分子判读时须由工具/RDKit 兜底，不能只信模型给出的 SMILES。
- **不点名具体函数**：只传 `tool_choice="auto"`，不传 object 形式指定某个函数
  （openPangu 与本地运行时均不支持）。工具收敛靠白名单。

## 8. 切回云端（可选项，非默认）

```bash
MAAS_BASE_URL=https://api.modelarts-maas.com/openai/v1
MAAS_MODEL=openpangu-2.0-flash
MAAS_API_KEY=<华为云 MaaS 密钥>
```

`MAAS_MODELS` 白名单仍保留两个盘古标识，仅用于兼容性提示，不拦截自定义模型。
