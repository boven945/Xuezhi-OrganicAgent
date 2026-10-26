# 配置与部署清单

| 项目 | 内容 |
| --- | --- |
| 文档编号 | XZ-OPS-CFG-001 |
| 版本 | **v1.1**（2026-10-22 基线，2026-10-26 修订） |
| 对应代码状态 | `main@ed6d257` |
| 核对方式 | 逐键对照 `backend/app/**` 读取点与 `.env.example`、`docker-compose.yml`、`frontend/.env.example` |
| 上位文档 | [需求基线](requirements-baseline.md)（`AC-08` 的核对对象）、[部署与运维手册](deployment-operations.md) |

> 本文只登记**代码里真实读取**的配置键。没有读取点的键不写进来——
> 写进来就会变成「文档说配了就能用、实际没人读」的假配置。

---

## 1. 使用方式

| 场景 | 用法 |
| --- | --- |
| 首次部署 | 按 §5 清单逐项打勾 → §6 选形态执行 → §7 验证 |
| 演示前 | 直接跑 §8 的 `scripts/demo-check.sh`，不要只看 `/health` |
| 改配置 | 先查 §2 该键的「必需性 / 合法取值 / 失败语义」，再改 |
| 排障 | §10 故障速查；配不上再看 §11 已知漂移 |

**总键数**：后端运行时 **23** 个（`.env.example` 覆盖 22 个，缺 1 个）、
前端 **4** 个（其中 2 个实际未生效）、仅测试 **2** 个。

---

## 2. 配置清单

### 2.1 模型服务（4）

| 键 | 必需性 | 默认值 | 合法取值 / 校验 | 敏感级 | 读取点 |
| --- | --- | --- | --- | --- | --- |
| `MAAS_API_KEY` | **必需**（缺失不阻断启动） | 空 | 非空白字符串；空白视为缺失 | **密钥** | `llm/config.py::ENV_API_KEY` |
| `MAAS_MODEL` | 可选 | `openpangu-2.0-flash` | 非空；建议取 `openpangu-2.0-flash` / `openpangu-2.0-pro`。非白名单值**不拒绝**，仅 `official_maas_model=false` | 非敏感 | `llm/config.py::ENV_MODEL` |
| `MAAS_BASE_URL` | 可选 | `https://api.modelarts-maas.com/openai/v1` | 须以 `http://` 或 `https://` 开头，否则构造即抛 `LLMConfigError` | 非敏感（含租户信息时不入日志） | `llm/config.py::ENV_BASE_URL` |
| `XUEZHI_PERSONA_ENABLED` | 可选 | `0` | 真值集 `{1,true,yes,on}` | 非敏感 | `llm/config.py::ENV_PERSONA` |

> `MAAS_API_KEY` 缺失时的行为：**服务照常启动**，`/ready` 返回 503，
> `/api/v1/*` 返回 503 `llm_not_configured`（附「请设置 MAAS_API_KEY 后重试」）。
> 这是刻意设计——先跑通链路再去申请密钥。

### 2.2 API 服务（5）

| 键 | 必需性 | 默认值 | 合法取值 / 校验 | 敏感级 | 读取点 |
| --- | --- | --- | --- | --- | --- |
| `XUEZHI_STRICT_STARTUP` | 可选 | `0` | `1` = 缺模型配置时**进程启动失败**（生产建议开） | 非敏感 | `api/app.py` lifespan |
| `XUEZHI_CORS_ORIGINS` | 条件必需 | 空 | 逗号分隔的**绝对 origin**；**空 = 完全不注册 CORS** | 非敏感 | `api/deps.py::from_env` |
| `XUEZHI_RATE_LIMIT_RPS` | 可选 | `1.0`（`.env.example` 写 2） | `> 0`，否则**启动失败** | 非敏感 | `api/deps.py` |
| `XUEZHI_RATE_LIMIT_BURST` | 可选 | `8`（`.env.example` 写 10） | `≥ 1`，否则**启动失败** | 非敏感 | `api/deps.py` |
| `XUEZHI_REQUEST_TIMEOUT` | 可选 | `120` | `> 0`，否则**启动失败** | 非敏感 | `api/deps.py` |

> `XUEZHI_CORS_ORIGINS` **必须同时写 `localhost` 与 `127.0.0.1` 两个来源**：
> 浏览器把它们当不同源，少一个就被 CORS 挡掉（实测踩过）。演示环境示例：
> `http://localhost:5173,http://127.0.0.1:5173`

### 2.3 知识库（6）

| 键 | 必需性 | 默认值 | 合法取值 / 校验 | 敏感级 | 读取点 |
| --- | --- | --- | --- | --- | --- |
| `XUEZHI_CHROMA_PATH` | 可选 | `data/chroma` | 目录路径。**不得由用户输入决定** | 非敏感 | `api/deps.py` |
| `XUEZHI_COLLECTION` | 可选 | `xuezhi_organic` | `[a-zA-Z0-9._-]`，长度 3–512（chromadb 硬约束），越界**启动失败** | 非敏感 | `api/deps.py` |
| `XUEZHI_INDEX_VERSION` | 可选 | `unversioned` | 与评估报告对应；变更须同步 `docs/` | 非敏感 | `api/deps.py` |
| `XUEZHI_EMBEDDING_PATH` | 条件必需（断网时） | 空 = 在线下载 | **权重所在目录**，不是 HF cache 根；传错会构造成功但检索必失败 | 非敏感 | `rag/embeddings.py::resolve_local_model_path` |
| `XUEZHI_RETRIEVAL_THRESHOLD` | 可选 | 空 = 不设阈值 | `0.0–2.0`（cosine **距离**）；越界/非数字 → 忽略并告警 | 非敏感 | `api/deps.py` |
| `XUEZHI_RETRIEVAL_TOP_K` | 可选 | `4` | `≥ 1`，否则**启动失败** | 非敏感 | `api/deps.py` |

> **阈值默认留空的理由**：41 条语料下 `None/0.4/0.5/0.6/0.7` 五个阈值的 top-3 命中率
> **完全相同**（96.97%）。设一个没有实测依据的阈值只会制造「已调优」的假象（I1）。
>
> **`build_index` 不读 `XUEZHI_CHROMA_PATH`**：它用 `REPO_ROOT/data/chroma`。
> 若自定义了该键，建索引时必须显式加 `--persist <同一目录>`，否则服务读的是空集合。

### 2.4 语音与数字人（8）

| 键 | 必需性 | 默认值 | 合法取值 / 校验 | 敏感级 | 读取点 |
| --- | --- | --- | --- | --- | --- |
| `XUEZHI_TTS_ENABLED` | 可选 | `1` | 关掉后 `/health` 报 `tts=未启用`，不尝试合成 | 非敏感 | `speech/config.py` |
| `XUEZHI_TTS_VOICE` | 可选 | `zh-CN-XiaoxiaoNeural` | 非空；纯空白回落默认 | 非敏感 | `speech/config.py` |
| `XUEZHI_TTS_RATE` | 可选 | `0` | `-50 ~ 100`（百分比）；越界**夹取**不报错 | 非敏感 | `speech/config.py` |
| `XUEZHI_TTS_MAX_CHARS` | 可选 | `400` | `1 ~ 100000`；越界夹取 | 非敏感 | `speech/config.py` |
| `XUEZHI_FAY_ENABLED` | 可选 | `0` | 真值集同前 | 非敏感 | `speech/config.py` |
| `XUEZHI_FAY_URL` | **条件必需** | 应用默认空；Compose 默认 `http://host.docker.internal:5000` | `FAY_ENABLED=1` 时必填，否则见 §2.8 | 非敏感（含内网地址） | `speech/config.py` |
| `XUEZHI_FAY_TIMEOUT` | 可选 | `10` | `> 0` | 非敏感 | `speech/config.py` |
| `XUEZHI_AUDIO_DIR` | 可选 | 系统临时目录下 `xuezhi-audio` | 目录；**多 worker 必须共享**，否则间歇性 404（H23） | 非敏感 | `api/app.py` |

### 2.5 前端（4）

| 键 | 必需性 | 默认值 | 作用 | 实际生效 |
| --- | --- | --- | --- | --- |
| `VITE_API_BASE_URL` | 可选 | 空（**同源相对路径**） | 客户端请求基址，**构建时注入** | ✅ `src/api/client.ts` |
| `VITE_BACKEND_URL` | 可选 | `http://127.0.0.1:8000` | 仅 dev server 代理 `/api`、`/health`、`/ready` 的 target | ✅ `vite.config.ts` |
| `VITE_HOST` | — | `127.0.0.1` | `.env.example` 声称控制监听地址 | ❌ **未读取**（`server.port` 硬编码 5173，host 未设） |
| `VITE_PORT` | — | `5173` | `.env.example` 声称控制端口 | ❌ **未读取** |

> **绝对禁止**把任何密钥写进 `VITE_*`：只有 `VITE_` 前缀会注入浏览器 bundle。
> 密钥只属于仓库根 `.env`（后端侧）。
>
> 前端 2 个键的漂移已登记为 §11 的 D-02，属于文档与实现不一致，不是使用者的配置错误。

### 2.6 仅测试（2）

| 键 | 值 | 作用 |
| --- | --- | --- |
| `XUEZHI_RUN_MODEL_TESTS` | `1` | 开启依赖真实 embedding 权重的测试（默认 skip） |
| `XUEZHI_RUN_E2E_TESTS` | `1` | 开启依赖真实模型的端到端测试（默认 skip） |

### 2.7 配置加载链路（最容易踩的一步）

```
.env                    ── scripts/run-local.sh（shell source）──▶ 进程环境变量
.env                    ── docker compose（仅替换 compose 中显式引用的键）──▶ 容器内环境变量
applications 代码        ── ✗ 不读 .env
```

**应用本身没有调用 `load_dotenv()`**（`python-dotenv` 在依赖里但无引用）。
因此：

- 用 `scripts/run-local.sh` 启动 → `.env` 中的后端配置进入进程环境；
- 用 `docker compose` 启动 → **只有 `docker-compose.yml` 的 `environment` 中显式列出的键生效**；当前仅透传 MaaS、人设和 Fay 等部分键，其他键要通过 Compose override / `docker compose run -e` 注入，见 §11 的 D-07；
- 直接 `uvicorn app.api.app:app` → **`.env` 完全不生效**，表现为「我明明填了密钥却说没配置」。

优先级：**显式传参 > 环境变量 > 代码默认值**（`LLMConfig.from_env` / `ServiceSettings.from_env` 一致）。

### 2.8 配置错误的三种失败语义

改配置前先看这张表，避免「改错一个字，排查两小时」：

| 类型 | 触发键 | 实际行为 |
| --- | --- | --- |
| **A. 启动即失败**（进程起不来） | `RATE_LIMIT_RPS ≤ 0`、`RATE_LIMIT_BURST < 1`、`REQUEST_TIMEOUT ≤ 0`、`RETRIEVAL_TOP_K < 1`、`COLLECTION` 含非法字符 | `ServiceSettings.__post_init__` 抛 `ValueError` → `create_app()` 在**导入期**失败 → uvicorn 起不来 |
| **B. 回落默认值** | API 数值键填了非数字、`RETRIEVAL_THRESHOLD` 越界、`TTS_VOICE` 纯空白、`TTS_RATE`/`MAX_CHARS` 越界 | 回落默认值；API 数值与检索阈值会记 warning，语音配置当前不记 warning；服务正常 |
| **C. 运行时不匹配** | `FAY_ENABLED=1` 但 `FAY_URL` 为空 | `SpeechSettings.from_env()` 抛 `ValueError` → `/api/v1/speak` 首次调用返回 **500**，`/health` 的 `speech.ready=false`。**不是启动失败** |

> C 类与 `.env.example` 的注释（「启动即报错」）**不一致**，属已知缺陷 D-03。
> 演示前请用 `scripts/demo-check.sh` 实测，不要凭注释判断。

---

## 3. 端口、卷与目录

### 3.1 端口

| 端口 | 服务 | 暴露方式 | 对谁开放 |
| --- | --- | --- | --- |
| `8000` | FastAPI（`/health`、`/ready`、`/docs`、`/api/v1/*`） | `run-local.sh` 默认 `127.0.0.1`；compose 映射 `8000:8000` | 本机 / 局域网演示 |
| `5173` | Vite 开发服务器 | compose 映射 `5173:5173` | 同上 |
| `5000` | Fay Flask HTTP（`/transparent-pass`） | 仅 `--profile fay` 时映射 | 仅后端容器 |
| `10002` | Fay WebSocket 渲染端 | 仅 `--profile fay` 时映射 | 前端数字人 |
| `10003` | Fay UI | **镜像 EXPOSE 但 compose 未映射** | 不对外开放 |

### 3.2 卷与持久化目录

| 卷 / 挂载 | 容器路径 | 内容 | 说明 |
| --- | --- | --- | --- |
| `./backend` | `/work/backend` | 源码 | 改代码需重启容器（compose 未开 `--reload`） |
| `./data` | `/work/data` | `chroma/`、`audio/`、`knowledge/` | **索引与音频都在这里**，删了这个目录等于清空知识库 |
| `frontend_node_modules`（命名卷） | `/app/node_modules` | 前端依赖 | 必须隔离：跨平台二进制会互相覆盖（实测踩过） |
| `fay_data`（命名卷） | `/app/data` | Fay 运行数据 | 仅 fay profile |

音频文件 TTL **600 秒**，清扫周期 **300 秒**（周期须明显小于 TTL）。
演示长时间挂机时磁盘不会无限增长，但**答辩前重放旧音频会 404**（属正常语义，统一 404 不区分过期与不存在）。

### 3.3 关键文件

| 路径 | 作用 | 是否入库 |
| --- | --- | --- |
| `.env` | 本地真实配置（含密钥） | ❌ 已被 `.gitignore` 忽略 |
| `.env.example` | 配置模板 | ✅ 可提交，**不得含真实密钥** |
| `frontend/.env.local` | 前端本地覆盖 | ❌ |
| `data/chroma/` | 向量索引（派生产物） | ❌ |
| `data/audio/` | 语音临时产物 | ❌ |
| `docs/api/openapi.json` | 接口契约快照 | ✅（契约测试比对基准） |

---

## 4. 部署形态与选择

| 形态 | 适用 | 化学能力 | 语音 | 生产就绪 | 状态 |
| --- | --- | --- | --- | --- | --- |
| **A. 本机 venv**（`run-local.sh`） | 改后端逻辑、跑非化学测试 | **化学能力：项目 F: 盘 venv `.venv-xuezhi312`（rdkit 2026.3.6）2026-10-26 实证可本机运行**；仅当安装在被 SAC/WDAC 按盘符拦截的目录（如 C: 默认）时才降级为仅检索 | 安装 `edge-tts` 且联网时可用 | 否 | 可用 |
| **B. Docker Compose**（推荐演示） | 答辩现场、端到端联调 | ✅ 容器内 RDKit 正常 | ✅（需外网） | **否**（开发服务器 + 无 TLS + 无鉴权） | 可用 |
| **C. 云端 ModelArts / AgentArts** | 长期在线服务 | 取决于平台镜像 | 取决于网络策略 | 未验证 | **未适配**（A7） |

> 形态 B 是当前唯一能同时演示「问答 + 来源 + 3D + 语音」的路径。
> 它用 `node:22-alpine` 跑 `npm run dev`，**不是生产构建**——上线前必须换成
> `vite build` 产物 + Nginx/Caddy 反向代理 + TLS，并接入鉴权。

---

## 5. 部署前检查清单

### 5.1 通用

- [ ] 已确认目标形态（A / B / C），并知晓该形态的能力边界（§4）
- [ ] `.env` 从 `.env.example` 复制而来，且**未被 Git 跟踪**（`git status` 无 `.env`）
- [ ] `MAAS_API_KEY` 已填入 `.env`，且**未出现在任何将提交的文件中**
- [ ] `MAAS_MODEL` 取值在官方文档确认范围内（默认 `openpangu-2.0-flash`）
- [ ] `XUEZHI_CORS_ORIGINS` 同时包含 `localhost` 与 `127.0.0.1` 两个来源
- [ ] 生产环境已设 `XUEZHI_STRICT_STARTUP=1`，且所用编排确实把该键注入容器（当前演示 Compose 未透传，见 D-07）
- [ ] 已确认限流参数（演示可放宽：`RPS=2`、`BURST=10`）
- [ ] 目标机的 Python 为 **3.12**、Node 为 **^20.19 或 ≥22.12**

### 5.2 知识库（最容易漏的一步）

- [ ] 已执行建索引：`python -m scripts.build_index`（**必须用 `-m`**，直接执行脚本会缺 `sys.path`）
- [ ] 已用 `python -m scripts.build_index --check-only` 确认条数 **41**
- [ ] 若自定义了 `XUEZHI_CHROMA_PATH`，建索引时已加 `--persist` 指向同一目录
- [ ] 断网演示前已预置嵌入权重（**91.4 MiB**）并设 `XUEZHI_EMBEDDING_PATH` 指向**权重目录**

### 5.3 容器形态

- [ ] Docker Desktop 已启动，后端为 **WSL2 / Linux**（`docker info` 可查）
- [ ] 已构建后端镜像：`docker build -f backend/tests/Dockerfile.test -t xuezhi-chem-test .`（首次约 20 分钟）
- [ ] 依赖变更后已用 `docker build --no-cache` 重建（增量缓存会留下旧依赖）
- [ ] 演示机 Ubuntu/Debian 的 `apt` 源可访问（仅 Fay 镜像需要）

### 5.4 前端

- [ ] `VITE_API_BASE_URL` **留空**（同源相对路径）
- [ ] 若改为跨域直连，后端已同步配置 `XUEZHI_CORS_ORIGINS`
- [ ] 已跑 `npm run typecheck` 与 `npm test`（期望 166 项通过）

### 5.5 网络

- [ ] 目标机能访问 `api.modelarts-maas.com`（云端模型）
- [ ] 目标机能访问微软 TTS 服务（否则语音降级；**属正常降级，不是故障**）
- [ ] 若设了 `HTTP_PROXY`，访问本机/内网服务时加 `--noproxy '*'`（否则 502）
- [ ] 安装依赖时如遇 PyPI 慢，显式加 `--proxy`（实测直连 29 kB/s vs 代理 1 MB/s）

---

## 6. 部署步骤

### 6.1 形态 A：本机 venv

```bash
# 1. 建环境（Python 3.12）
python -m venv .venv-xuezhi312
.venv-xuezhi312/Scripts/activate          # Windows
pip install -r requirements-lock.txt       # 已验证的直接依赖版本（含 edge-tts）

# 2. RDKit（项目实测：F: 盘 .venv-xuezhi312 中 pip install 可真实 import 并生成帧；
#    若装在被 SAC/WDAC 按盘符拦截的目录则跳过，服务降级）
pip install rdkit==2026.3.6                # 容器内用 pip 轮子；文档基线建议 conda-forge

# 3. 配置
cp .env.example .env                       # 填入 MAAS_API_KEY

# 4. 建索引（首次必须）
python -m scripts.build_index

# 5. 自检 + 启动
./scripts/run-local.sh --check
./scripts/run-local.sh                     # http://127.0.0.1:8000
```

### 6.2 形态 B：Docker Compose（推荐）

```bash
# 1. 构建后端镜像（首次约 20 分钟，含 torch 554MB）
docker build -f backend/tests/Dockerfile.test -t xuezhi-chem-test .

# 2. 配置
cp .env.example .env                       # 填入 MAAS_API_KEY 与 CORS

# 3. 建索引（容器内执行，落盘到宿主的 ./data/chroma）
docker compose run --rm api python -m scripts.build_index
docker compose run --rm api python -m scripts.build_index --check-only

# 4. 启动
docker compose up -d
docker compose logs -f api

# 5. 可选：数字人（默认不启动，属可选增强）
docker compose --profile fay up -d fay
```

> 步骤 3 的命令按镜像布局（`WORKDIR /work`、`COPY scripts /work/scripts`）推导，
> **首次执行请以 `--check-only` 的输出为准**；若报 `No module named 'scripts'`，
> 说明镜像未重新构建（`Dockerfile.test` 已 `COPY scripts`）。

### 6.3 形态 C：云端（未适配，仅列待办）

- [ ] 确认目标平台（ModelArts / AgentArts / ECS + Compose）与镜像构建方式
- [ ] 密钥改由平台密钥管理注入，**移除 `.env` 依赖**
- [ ] 前端改 `vite build` 静态产物 + 反向代理，关闭 5173 开发服务器
- [ ] 配置 TLS、访问控制、日志脱敏与备份
- [ ] 以上均无实测记录，**不得据本文宣称已支持云上部署**

---

## 7. 部署后验证清单

按顺序执行，任一项不通过则**不要进入演示**。

- [ ] `curl --noproxy '*' http://127.0.0.1:8000/health` → 200，`status` 为 `ok` 或 `degraded`（附原因）
- [ ] `/health` 中 `chem.ready=true`（容器形态必须；本机 F: 盘 venv 也可达 true；为 false 说明 RDKit 不可用，走容器形态）
- [ ] `/ready` → 200（503 表示缺 `MAAS_API_KEY` 或组件未就绪）
- [ ] `POST /api/v1/molecule {"smiles":"Oc1ccccc1"}` → `ok=true`，`functional_groups` 含**酚羟基**且**不含**醇羟基
- [ ] `POST /api/v1/ask {"question":"苯酚为什么具有弱酸性？"}` → 200，`sources` 非空（**空数组说明索引没建**）
- [ ] `POST /api/v1/ask/stream` → 收到 `meta` 事件后持续收到 `delta`
- [ ] `POST /api/v1/speak` + `GET /api/v1/speak/{id}` → 音频 > 1 KB
- [ ] `python -m scripts.export_openapi --check` → 无漂移（7 端点 / 15 schema）
- [ ] `docker run --rm xuezhi-chem-test` → 0 failed（期望 752 passed / 40 skipped）
- [ ] `git status` → 无 `.env`、无 `data/chroma/`、无密钥

---

## 8. 演示前检查（唯一可信的门禁）

```bash
./scripts/demo-check.sh
# 或指定地址
BACKEND=http://127.0.0.1:8000 ./scripts/demo-check.sh
```

脚本**不看 `/health` 的 `ready` 字段**（那正是曾经会说谎的字段），而是真发请求：

| 检查项 | 判据 |
| --- | --- |
| 服务可达 | `/health` 有响应 |
| 语音合成 | `caps.tts=true` 时要求 `tts_verified=true` |
| 数字人 | `caps.fay=true` 时要求 `fay_verified=true` |
| 知识检索 | 答复正文**不得**出现「检索服务不可用」类措辞；且必须带 `source_id` |
| 语音产物 | 拿到 `audio_id` 后**真取一次**，> 1000 字节 |
| 分子解析 | 返回含 `atoms`/`bonds` 数组 |

退出码 0 = 可以演示；1 = 有失败项，**先修再上台**。

> 历史教训：曾连续四次「`/health` 说就绪、实际不可用」（tts 未安装、嵌入模型加载失败、
> Fay 端口无监听、embedding 路径传错）。**只看健康检查等于没检查。**

---

## 9. 停止、回滚与重建

| 操作 | 命令 | 说明 |
| --- | --- | --- |
| 停止服务 | `docker compose down` | 保留卷与数据 |
| 停止并清空 | `docker compose down -v` | **会删 `frontend_node_modules`、`fay_data`**，索引若在命名卷内也会丢 |
| 重启后端 | `docker compose restart api` | 配置改动后必须重启（探针结果在进程内缓存） |
| 回滚代码 | `git checkout <上一个 tag/commit>` | 回滚后**必须重建镜像并重跑 §7** |
| 重建索引 | `python -m scripts.build_index` | 语料变更后执行；16 秒 / 41 条 |
| 清空索引 | 删除 `data/chroma/` 后重建 | **不要**只删集合不重建，否则检索恒 `no_match` |
| 轮换密钥 | 更新 `.env` → `docker compose up -d api` | 旧密钥须在云控制台**撤销**，不能只改本地 |

**回滚判定**：只要 `/health` 出现 `chem.ready=false` 或 `rag_verified=false`，
就应回到上一个已验证镜像，而不是在现场改配置。

---

## 10. 故障速查

| 现象 | 最可能原因 | 处置 |
| --- | --- | --- |
| 起服务就崩，日志是 `ValueError` | §2.8 A 类：限流/条数/集合名非法 | 按校验规则改回合法值 |
| 问答 503「请设置 MAAS_API_KEY 后重试」 | 直接跑 uvicorn，`.env` 未被加载 | 用 `run-local.sh` 或 compose 启动 |
| `/ready` 503，`/health` 里 `llm.ready=false` | 密钥缺失或无效 | 核对 `MAAS_API_KEY`；`llm_auth_failed` 说明密钥无效 |
| 答复里说「检索不可用」但 `/health` 说 rag 就绪 | 索引为空 / 嵌入模型加载失败 | `--check-only` 查条数；重建索引 |
| `sources` 恒为空数组 | 集合条数为 0（H24） | `python -m scripts.build_index` |
| 检索一直 `no_match`，索引明明建过 | `XUEZHI_CHROMA_PATH` 与 `--persist` 不一致 | 二者指向同一目录 |
| 首次提问超时 | 嵌入模型冷加载（实测 12.5 s，工具阈值 15 s） | 等预热线程完成，或启动后先手动问一次 |
| `/api/v1/molecule` 503 | RDKit 被 Windows 应用控制策略拦截（仅当装在被拦目录时） | 项目 F: 盘 venv `.venv-xuezhi312` 实测可本机跑，无需容器；仅当装在被拦目录时才改用容器（H20）；**不要关 SAC**（不可逆） |
| 启动报 `No module named 'app'`（Git Bash + docker run） | Git Bash 把 `/work/backend` 展平成 Windows 路径 | 前置 `MSYS_NO_PATHCONV=1` |
| 浏览器预检 405 / CORS 报错 | `XUEZHI_CORS_ORIGINS` 只写了一个 origin | 补齐 `localhost` 与 `127.0.0.1` |
| 语音返回 `stage=unavailable` | edge-tts 需外网，断网必失败 | 属正常降级；文本答复不受影响 |
| `/api/v1/speak` 返回 500「服务内部错误」 | `FAY_ENABLED=1` 但 `FAY_URL` 为空（§2.8 C 类） | 补 `FAY_URL` 或关掉 `FAY_ENABLED` |
| 拿到 `audio_id` 但取音频 404 | 音频超 TTL（600 s）或多 worker 目录不共享 | 重新合成；多 worker 统一 `XUEZHI_AUDIO_DIR` |
| 明明配了 Fay 却报不可用 | 本机 HTTP 代理劫持了 127.0.0.1 请求 | 代码已用空 `ProxyHandler`；自检加 `--noproxy '*'` |
| 断网启动卡在下载模型 | 未预置 `XUEZHI_EMBEDDING_PATH` | 预置权重目录（91.4 MiB） |

---

## 11. 已知漂移与待回填（配置层面必须知道）

以下为**文档与实现不一致**处，核对时已确认，处置建议随文给出，未擅自改动代码。

| 编号 | 漂移 | 影响 | 建议处置 |
| --- | --- | --- | --- |
| **D-01** | `XUEZHI_PERSONA_ENABLED` 在 `llm/config.py` 与 `docker-compose.yml` 中使用，但**不在 `.env.example`** | 使用者不知道有这个开关（默认关，影响小） | 补进 `.env.example`，注明「实测对人设输出无影响（H28）」 |
| **D-02** | `VITE_HOST` / `VITE_PORT` 在 `frontend/.env.example` 中声明，但 `vite.config.ts` **未读取** | 改这两个键无效，误以为端口已改 | 二选一：读入配置，或从模板删除 |
| **D-03** | `.env.example` 称 Fay 配置缺失「启动即报错」，实际是**调用 `/api/v1/speak` 时 500** | 排障方向错误（会去查启动日志） | 改为「调用语音接口时返回 500，`/health` 报 speech 未就绪」 |
| **D-04** | `deployment-operations.md` §1 仍写「仓库仅含文档、无源码」，与 README 事实相反 | 评审会质疑文档可信度 | 按本清单 §3–§6 回填；旧文保留但加「已过时」提示 |
| **D-05** | `decision-register.md` F2 记「11 个配置键」，实际后端运行时为 **23 个** | 配置清单口径不一致 | F2 改为引用本文 §2 |
| **D-06** | 前端测试数、后端测试数散落在各验证文档，口径不一（443/579/752） | 对外报数容易报错 | 统一以 `README.md` §验证与质量为历史基线，并在目标机发版前复跑 |
| **D-07** | `docker-compose.yml` 只显式透传部分 `.env` 键；`STRICT_STARTUP`、限流、检索、TTS 等多数覆盖值不会自动进入容器 | 使用者修改 `.env` 后误以为容器已采用新值 | 演示环境使用代码默认值；需要覆盖时增加 Compose override，生产编排必须逐键显式注入 |
| **D-08** | 本文 §4/§6/§7/§10 多处称「Windows 下 RDKit 被 SAC 拦截、只能容器」，但 2026-10-26 实证项目 F: 盘 venv 可本机运行（与需求基线 TC-03 例外一致） | 读者可能误以为本机必失败 | 本机优先用 `.venv-xuezhi312`；容器作兜底；根因疑似策略按盘符作用域，非版本问题 |

---

## 12. 变更记录

| 版本 | 日期 | 内容 |
| --- | --- | --- |
| v1.0 | 2026-10-22 | 首次建立：23 个后端键 + 4 个前端键 + 2 个测试键；配置加载链路、三种失败语义、端口/卷清单、三形态部署步骤、部署前后检查清单、故障速查、7 处已知漂移与限制 |
| v1.1 | 2026-10-26 | 本地合入 hyf 分支；补充 RDKit 本机可用性例外（§4/§6/§7/§10/§11 D-08），与需求基线 TC-03 更正一致 |

---

**相关文档**：[需求基线](requirements-baseline.md) · [部署与运维手册](deployment-operations.md) ·
[本机运行指南](local-run-guide.md) · [安全与隐私基线](security-privacy.md) ·
[决策登记表](decision-register.md)
