# 决策登记表

本文集中登记项目尚未确认的决策项与待回填缺口，作为实现的前置条件。
来源为各文档中标注"待确认""尚未""须在实现后回填"的条目。

规则：
- 未确认前**不使用猜测值**填充代码、配置或文档。
- 决策完成后回填对应文档，并在本文标记状态与日期。
- 每条决策必须记录**依据来源**（实测结果或服务方官方文档），不接受凭经验断言。

状态取值：`待决策` / `决策中` / `已确认` / `已废弃`

---

## A. 架构决策（阻塞实现，优先级最高）

| 编号 | 决策项 | 来源 | 阻塞对象 | 负责角色 | 状态 |
| --- | --- | --- | --- | --- | --- |
| A1 | MCP 调度层的协议版本、传输方式、工具 schema 与权限边界 | **部分完成**：工具白名单、schema 校验、超时隔离、失败结构化、步数上限均已实现（backend-agent）；知识检索工具已接入并完成端到端验证。**仍待确认**：MCP 标准协议版本与传输方式（当前为自研调度层，未接入 MCP 规范）| infra | 部分完成 |
| A2 | API 路由、认证方式、会话存储方式 | **路由与传输已实现**：新增 `/health`（恒 200）、`/ready`（未就绪 503）、`/api/v1/ask`（同步）、`/api/v1/ask/stream`（SSE 阶段事件）、`/api/v1/molecule`（SMILES 解析，无需模型）。**认证方式仍待决策**：演示场景无鉴权，以令牌桶限流 + 输入长度上限防护；**会话存储未做**（多轮history 由调用方传入，服务端无状态）。实测记录见 `interface-contract-verification.md` | backend-api | 架构 | 部分完成 |
| A3 | 同步响应 / 流式输出 / 任务轮询 / 推送通道的取舍 | **同步 + SSE 流式并存**（已实现）。依据：openPangu 短请求中位延迟 7.10 秒，7 秒同步会让前端长时间转圈；但流式响应头发出后无法再改 HTTP 状态码（实测：Agent 抛错时流式仍返回 200），故不能作为唯一方案。**当前 SSE 是阶段事件而非逐 token 流**——`AgentLoop` 为同步迭代不外露中间态，真流式需改造该模块并重跑其 72 项测试。**任务轮询与推送通道未采用**：引入任务态需先定会话存储（A2） | backend-api、frontend-web | 架构 | 部分完成 |
| A4 | Fay SDK/服务版本、通信协议、端口、音视频数据流向 | architecture.md §7 | backend-speech | 架构 | 待决策 |
| A5 | 本地模型服务适配器、权重分发与校验 | 云端侧适配器已实现（backend-llm，OpenAI 兼容协议）。**本地推理适配器待定**：`autoawq` 纯 sdist 需编译（G4），且 RTX 5070 CUDA 组合未实测（G3）| infra | 部分完成 |
| A6 | ChromaDB 部署方式、持久化卷、备份周期、并发访问策略 | architecture.md §7 | backend-rag | 架构 | 待决策 |
| A7 | 服务部署位置、TLS/反向代理、监控与告警系统 | architecture.md §7 | infra | 运维 | 待决策 |

> A3 与 A4 相互依赖：Fay 服务的实际能力决定前端是否需要流式通道。建议 A4 先于 A3 确认。

## B. 实测与硬件基线

| 编号 | 决策项 | 来源 | 验证方式 | 负责角色 | 状态 |
| --- | --- | --- | --- | --- | --- |
| B1 | RTX 5070 显存实测容量与本地推理可行性 | architecture.md §7、deployment-operations.md §2 | 目标机器实跑模型加载并记录峰值显存 | 开发 | 待决策 |
| B2 | NVIDIA 驱动 / CUDA / PyTorch / Transformers / AutoAWQ 兼容组合 | deployment-operations.md §5 | 隔离环境逐项验证并记录版本 | 开发 | 待决策 |
| B3 | openPangu-7B-Instruct AWQ 4-bit 权重来源、授权与校验和 | deployment-operations.md §5 | 官方渠道核验并记录 checksum | 项目负责人 | 待决策 |
| B4 | MaaS 模型 ID、endpoint、认证、配额、区域、服务条款 | **已核实技术参数**：base_url `https://api.modelarts-maas.com/openai/v1`、模型标识 `openpangu-2.0-flash`/`openpangu-2.0-pro`、Bearer Token 认证、仅西南-贵阳一区域（官方文档 2026-09-29 更新）。**仍需确认**：账号授权、配额、计费方式、Function Call 支持情况 | 项目负责人 | 部分确认 |
| B5 | Fay 与 Edge-TTS 是否依赖外网（决定能否称"离线可用"） | deployment-operations.md §5 | 断网环境实测 | 开发 | 待决策 |

## C. 质量指标（须实测后批准，禁止预设）

| 编号 | 指标项 | 来源 | 说明 | 负责角色 | 状态 |
| --- | --- | --- | --- | --- | --- |
| C1 | 准确率阈值 | product-scope.md §7 | 基于已批准测试集与教师评审确定 | 项目负责人 | 待决策 |
| C2 | 延迟目标 | product-scope.md §7 | 按云端/本地模式分别设定 | 项目负责人 | 待决策 |
| C3 | 可用性目标 | product-scope.md §7 | 依实测结果确定 | 项目负责人 | 待决策 |
| C4 | 检索排序与召回阈值 | knowledge-base.md §5 | 用标注问答集实测，不使用固定经验值 | 开发 | 待决策 |
| C5 | 知识库评估数值门槛 | knowledge-base.md §6 | 依基线测量与教学审核确定 | 教师/审核者 | 待决策 |
| C6 | 输入长度与请求体大小上限 | interface-contract.md §4 | 依性能与教学测试确定 | 开发 | 待决策 |

## D. 内容与授权

| 编号 | 决策项 | 来源 | 负责角色 | 状态 |
| --- | --- | --- | --- | --- |
| D1 | 教材版本清单与区域课程差异处理 | knowledge-base.md §1、§4 | 项目负责人 | 待决策 |
| D3 | 教材内容的版权授权 | **已绕开（方案：自编讲义）**：不再依赖教材原文，改为依据《普通高中化学课程标准（2017年版2020年修订）》自行编写讲义。已产出 41 条/ 21 个主题，覆盖课标四大模块，全部标记 project-authored。**仍需化学教师审核内容准确性**（H13/H18）| 项目负责人 | 已解决（内容待审核）|
| D2 | 知识库内容的使用许可与授权范围 | knowledge-base.md §2 | 项目负责人 | 待决策 |

## E. 安全与运营

| 编号 | 决策项 | 来源 | 负责角色 | 状态 |
| --- | --- | --- | --- | --- |
| E1 | 日志、会话、音频与分析数据的保留期限与删除流程 | security-privacy.md §2 | 项目运营方 | 待决策 |
| E2 | 向 MaaS / Fay 发送数据的用户告知文案 | security-privacy.md §2 | 项目负责人 | 待决策 |
| E3 | 安全事件联系人、升级路径、服务暂停权限 | security-privacy.md §6 | 项目运营方 | 待决策 |
| E4 | 生产依赖锁定方案与漏洞审查流程 | security-privacy.md §5 | 开发 | 待决策 |
| E5 | 数据存储位置与访问角色划分 | security-privacy.md §2 | 运维 | 待决策 |

## F. 待回填文档缺口

以下缺口需在对应模块实现后回填，当前**不得填入猜测值**。

| 编号 | 缺口 | 目标文档 | 由哪个模块回填 | 状态 |
| --- | --- | --- | --- | --- |
| F1 | 实际启动命令、监听地址、端口、健康检查地址 | **已确定（2026-10-04）**：`scripts/run-local.sh`（支持 `--check` 自检、`--port` / `--host` / `--reload`）。默认 `http://127.0.0.1:8000`，健康检查 `/health`，接口文档 `/docs`。**无MAAS_API_KEY 也能启动**（便于先跑通链路）。实测本机可用，见 `local-run-guide.md` | backend-api、infra | 已解决 |
| F2 | 配置键清单（键名、用途、必需性、默认行为、密钥级别、覆盖方式） | **已确定（2026-10-04）**：见 `.env.example`（11 个配置键，含用途、默认值、是否影响可复现）。全部经`ServiceSettings.from_env` 实测验证。密钥级别：仅 `MAAS_API_KEY` 为密钥，其余均为非敏感配置。**不提供任何带默认值的密钥**（`deployment-operations.md` §6） | infra | 已解决 |
| F3 | `.env.example` | **已提供（2026-10-04）**：`.env.example`，由 `scripts/run-local.sh` 自动载入。已实测 `git add -n .env.example` 成功（`!.env.example` 规则生效），而 `.env` 被正确忽略 | infra | 已解决 |
| F4 | OpenAPI 文件与契约测试 | **部分完成（2026-10-04）**：OpenAPI 可由 `/openapi.json` 实时生成（实测 5 路径 / 13 schema），但**未导出静态契约文件**，也**无契约变更检测**（改字段不会导致测试失败）。建议后续导出 `openapi.json` 入库并加对比测试 | backend-api | 部分完成 |
| F5 | 可视化数据 schema 版本 | interface-contract.md | frontend-viz | 待回填 |
| F6 | 目录结构实际布局 | README.md | 随各模块实现更新 | 待回填 |
| F7 | 监控接入后的责任人、SLO、值守渠道、恢复目标 | deployment-operations.md §8 | infra | 待回填 |
| F8 | ChromaDB 已发布索引的版本记录与回滚包 | knowledge-base.md §7 | knowledge-data | 待回填 |

## G. 工程基建缺口（本次识别，原文档未列）

| 编号 | 缺口 | 说明 | 负责模块 | 状态 |
| --- | --- | --- | --- | --- |
| G1 | 仓库缺少 `.gitignore` | 存在密钥、`.env`、模型权重、构建产物误提交风险 | infra | 已处理 |
| G2 | 依赖未锁定 | requirements 仅给出下限版本，非可复现安装 | infra | 部分处理 |
| G3 | torch 未锁定（CUDA 变体） | 目标机 RTX 5070 的 CUDA/驱动组合未实测，无法单一版本锁定 | infra | 待决策 |
| G4 | autoawq 无 wheel 且停更 | 纯 sdist 需编译；2025-05-11 后无新版本；与 transformers 5.x 兼容性未验证 | infra | 待决策 |
| H1 | Python 基线选型 | **已决策：3.12**（2026-10-03）。依据见 `docs/python-version-evaluation.md` | 项目负责人 | 已决策 |
| H2 | conda-forge rdkit 是否支持 Python 3.12 | **已确认支持**：2026.03.6 覆盖 py310–py314 × 6 平台 | infra | 已解决 |
| H3 | Fay / Edge-TTS 的 Python 版本约束 | 未知，随 A4 一并确认 | infra | 待决策 |
| H4 | chromadb 1.5.9 源码编译是否可行 | **无需编译**：`cp39-abi3` 稳定 ABI wheel 适用 3.9+，实测 pip 直接选用 | infra | 已关闭 |
| H5 | `requirements-lock.txt` 中 pandas 版本与基线冲突 | **已解除**：基线升至 3.12 后 pandas==3.0.6 可正常安装 | infra | 已解决 |
| H6 | 在 Python 3.12 环境下执行完整安装实测并记录组合 | **已完成**：Python 3.12.14 venv 装入 131 个包（EXIT=0）；import 冒烟测试 15/16 通过，唯一失败为本机 WDAC 拦截 grpcio（非依赖问题） | infra | 已完成 |
| H7 | 云端精简版实际会安装 torch | sentence-transformers 硬依赖 `torch>=2.2`，实测解析出 118 包含 torch；原文档"不含 torch"表述错误 | infra | 已修正表述 |
| H8 | 是否需要彻底避免安装 torch | **建议不移除**：移除 sentence-transformers 将失去本地 embedding，违反知识库固定 embedding 模型要求；推荐改用 CPU 版 torch（见 h8-torch-removal-evaluation.md） | 项目负责人 | 建议：不移除 |
| H11 | 演示机是否存在同类 WDAC 拦截 | 本机实测 grpcio 的 cygrpc.pyd 被应用程序控制策略阻止，需确认 Linux 演示机是否同样受限 | infra | 待确认 |
| H12 | 团队测试执行环境 | **已解决：使用 Docker 容器执行测试**。本机 SAC 拦截未签名二进制（conda 发行版自身也被拦），但容器内二进制不受主机策略管辖。已新增 `backend/tests/Dockerfile.test`，42/42 通过 | infra | 已解决 |
| H13 | 官能团 SMARTS 模式的正确性审核 | 11 条模式已通过 RDKit 实测（容器内 42/42 通过）；**仍需化学教师确认覆盖范围适合高中课程** | 化学审核者 | 待审核 |
| H14 | 容器化测试环境 | **已解决**：Docker daemon 正常（29.8.1，Linux 后端），容器可执行 Python 3.12 + RDKit 测试 | infra | 已解决 |
| H15 | tool_choice 协议限制的规避方案 | 实测 openPangu **不支持** OpenAI 规范的 `{"type":"function","function":{...}}` 形式，只接受 none/auto/required。工具调度层需靠白名单收敛 + 提示词引导，不可依赖精确点名 | infra | 待处理 |
| H16 | 安全上下文阈值的确定 | 实测 342,895 tokens 时中间位置标记漏召回，接近上限召回能力下降。512K 是硬上限，但**安全阈值需项目负责人批准**（关联 C2）| 项目负责人 | 待决策 |
| H17 | 中文 embedding 模型选型 | **已解决**：选定 `BAAI/bge-small-zh` 并完成实测验证。维度 512、已归一化；三个中文查询首位全部正确（cos 0.82-0.93），无关文档稳定排末位。内置 EF 的排序颠倒问题已纠正。详见 `docs/embedding-model-verification.md` | 项目负责人 | 已解决 |
| H18 | 知识库召回质量评估 | **部分完成**：真实检索实测（41 条语料 / 33 探针）首位正确 27/33（81.8%）、top3 命中 32/33（96.97%）。**已定位排序瓶颈**：6 个非首位案例中 5 个正确片段在 top2 内，属重排序问题而非召不出（I1）。**仍待教师用标注问答集做端到端核对**（注意：检索命中不等于答得对，FloTorch 2026 实证过这一点）| 化学审核者 | 待审核 |
| H19 | 工具选择混淆 | **已修复**：工具描述中互相点名各自边界（检索工具注明不解析 SMILES，化学工具注明不检索教材原文）。实测三工具同时注册时 4/4 用例正确分配；改动前"SMILES 是 CCO"被误分配给检索工具 | infra | 已解决（待扩大样本统计）|
| I1 | 检索重排序（瓶颈已量化） | 实测确认：41 条语料下top3 命中率 96.97% 但首位正确仅 81.8%，**6 个非首位案例中 5 个正确片段在 top2 内**。问题不是召不出而是排序不准，正是 reranker 的适用场景。案例：「乙酸的酸性有多强」top1 命中蛋白质条目（氨基酸含羧基且具两性，语义信号强），目标排 top2。建议顺序：先扩语料到数百条观察阈值与排序分布，再评估 reranker 收益 | infra | 待处理 |
| I2 |补充 `AgentLoop.run` 的结构化返回（阻塞 sources 填充）|**已解决（2026-10-04）**：`_extract_sources` 从工具返回的 passages 解析出source_id/title/edition/locator/scope，Agent 层产出 `source` 事件，API 层映射为契约模型（含超长截断与缺省兜底）。**`/api/v1/ask` 的 `sources` 现在有值了**，前端可展示依据。解析失败静默返回空列表——来源是附加信息，不该让问答失败。实测依据见 `agent-streaming-verification.md` §4|infra|已解决|
| I3 |token 级流式（阻塞 SSE 完整体验）|**已解决（2026-10-04）**：`AgentLoop.stream` 改造为生成器，`run()` 改为消费它（两者共用同一循环，杜绝路径分裂）。SSE 转发 `delta` 事件实现逐 token 输出。**联网核实 + 容器内实测确认三前提**：华为 MaaS 支持 `stream:true`（官方文档 model-call-101 有流式示例）、`bind_tools` 后 Runnable 仍有 `.stream()`、`AIMessageChunk.__add__` 会自动归并 tool_calls 分片。**中间轮不产 delta**（模型可能先吐思考文本再吐 tool_calls）。流式失败自动降级为同步。**未验证**：openPangu 上的真实流式行为，须在演示机实测。详见 `agent-streaming-verification.md`|infra|已解决|
| I4 | 限流状态在多 worker 下不共享 | 令牌桶是**进程内存态**（刻意不引入 Redis）。多 worker 部署时每进程各算一份，实际总配额 = workers × rate。**生产多 worker 前必须换成分布式实现**，否则限流形同虚设。当前演示为单 worker，暂无实际风险 | infra | 待处理 |
| I5 |羟基 SMARTS 无法区分醇羟基与羧酸羟基|**已解决（2026-10-04）**：原 `[OX2H]` 只描述"连两个原子且带氢的氧"，无法区分醇羟基与羧酸羟基，实测导致**乙酸、苯甲酸、甘氨酸全部误报**——学生看到"乙酸含羟基"会误以为羧酸是醇。**但只修这处不够**：联网查到的两种"排除羧酸"写法（`[#6X4][OX2H]`、`[OX2H][CX4;!$(...)]`）都会**漏掉苯酚**（酚羟基连芳香碳而非 sp3 碳），等于把一个错误换成另一个。最终**拆成两条**：`醇羟基`（连 sp3 碳，排除缩醛类）+ `酚羟基`（连芳香碳），与语料`org-alcohol-phenol-difference` 的口径一致。16 个高中常见结构逐一实测。见 `chem-smarts-verification.md`|chem/infra|已解决|
| H9 | 是否采用 CPU 版 torch index-url | 保留 embedding 能力同时缩小体积的推荐路径 | infra | 待决策 |
| H10 | 若走远程 embedding，治理与断网方案 | 备选路径，断网演示场景不可用 | 项目负责人 | 暂不推进 |

---

## 决策记录

确认后的决策在此追加记录，便于追溯。

| 编号 | 结论 | 依据 | 确认人 | 日期 |
| --- | --- | --- | --- | --- |
| H1 | **项目 Python 基线确定为 3.12** | 实测 16 个直接依赖在 3.12 下全部满足、无阻断；解除 numpy（→2.5.3）与 pandas（→3.0.6）版本压制；决策时仓库无源码，迁移成本为零。详见 `docs/python-version-evaluation.md` | 项目负责人 | 2026-10-03 |
| H5 | pandas 版本冲突随基线升级解除，无需降级方案 | pandas 3.0.6 要求 Python>=3.11，在 3.12 基线下可正常安装 | 待确认 | 2026-10-03 |
| H6 | Python 3.12 下依赖安装与运行时验证完成 | venv Python 3.12.14 + pip 26.2.1，**131 个包安装成功（EXIT=0）**；import 冒烟测试 15/16 通过；numpy 2.5.3 / pandas 3.0.6 命中 cp312 wheel | 待确认 | 2026-10-03 |
| H7-corr | **更正**"云端精简版不含 torch"的错误表述 | sentence-transformers 硬依赖 torch>=2.2，实测解析结果含 torch-2.14.1；已在 README、requirements_cloud.txt、dependency-notes.md、requirements-lock.txt 四处更正 | 待确认 | 2026-10-03 |
| H8 | 云端模式不移除 sentence-transformers，**维持现状即可** | 实测 optimum-onnx 与 sentence-transformers 的 transformers 约束互斥（4.36-4.58 vs 5.0-6.0），走 ONNX 需降级 transformers；chromadb 基础依赖不含 torch 但需外部 embedding。**且 PyPI 的 torch 默认即 CPU 构建（`2.14.1+cpu`）**，无需额外配置 index-url | 待确认 | 2026-10-03 |
| H6-corr | **更正**：PyPI 的 torch 默认即 CPU 构建（实测 `2.14.1+cpu`，536MB，cuda 不可用） |因此 H8 无需额外配置 CPU index-url，维持现状即可 | 待确认 | 2026-10-03 |
| H11 | 本机 grpcio 原生扩展被 WDAC 拦截，不影响依赖选型 | numpy/scipy/pydantic-core/PyYAML 扩展均正常加载，仅 `grpc/_cython/cygrpc.cp312-win_amd64.pyd` 被阻止，属本机安全策略而非依赖冲突 | 待确认 | 2026-10-03 |
| H12 | 团队测试执行环境 | **已解决：使用 Docker 容器执行测试**。本机 SAC 拦截未签名二进制（conda 发行版自身也被拦），但容器内二进制不受主机策略管辖。已新增 `backend/tests/Dockerfile.test`，42/42 通过 | infra | 已解决 |
| H13 | 官能团 SMARTS 已实测通过，待化学审核 | 11 条模式已在容器内通过 RDKit 2026.03.6 编译与匹配验证（chem 模块 42/42 通过）。其中 `[CX2]#[CX2]` 经核实不匹配氰基 CC#N。**仍需化学教师确认覆盖范围适合高中课程** | 待确认 | 2026-10-03 |
| H14 | Docker 方案最终可行，需 UI 初始化 | 初次尝试时 daemon 无法启动（管道未创建、进程秒退）；**用户在 Docker Desktop 完成 WSL2 后端初始化后 daemon 29.8.1 正常**，容器内 RDKit 完全可用，验证了该路径 | 待确认 | 2026-10-03 |
| H12-corr | **更正**：能否加载原生扩展取决于 Python 发行来源，不是版本 |横向实测（均为 3.12.14）：uv 发行版 `_ctypes` 通过、RDKit 被拦；conda-forge 环境 `_ctypes` 本身即被拦。**换 conda 安装不能绕开 Smart App Control**，文档基线推荐的 conda 路径在启用 SAC 的设备上不适用 | 待确认 | 2026-10-03 |
| H12-res | **已解决**：Docker 容器作为测试执行环境 | 用户完成 Docker Desktop WSL2 后端初始化后，daemon 29.8.1 就绪。容器内 RDKit 2026.03.6 完全可用（不受主机 SAC 管辖），已建 backend/tests/Dockerfile.test，chem 模块 42/42 通过。**首次运行 28 项失败全部定位为我的判断错误**（GetSmarts API 不存在、"C" 是合法甲基、"CC#N" 是氰基非碳碳三键、num_atoms 不含隐式氢） | 待确认 | 2026-10-03 |
| B4-test | MaaS 连通性实测：密钥有效，阻塞在预置服务未开通 | 容器内用真实 MaaS API Key 请求 `openpangu-2.0-flash` 与 `openpangu-2.0-pro`，**均返回 403 / ModelArts.81004**。**403 而非 401 证明密钥已通过鉴权**；官方错误码表确认 81004 = "尚未开通调用的预置服务"。**密钥仅作容器环境变量传入，未写入任何文件**。待用户开通服务后重测 | 待确认 | 2026-10-03 |
| B4-final | MaaS 三项验证全部通过，含四项协议实测发现 | ① 连通成功；② Function Call 可用，**但 tool_choice 不支持指定具体函数**（只接受 none/auto/required，传 dict 报 81001）；③ **默认开启深度思考**，message 含 reasoning_content，token 设 200 会导致正文为空；④ 上下文上限 512,000 token（错误信息给出数值），342,895 tokens 时中间位置标记漏召回。短请求中位延迟 7.10s，5 并发未触发限流 | 待确认 | 2026-10-03 |
| A1-note | LangChain 与 OpenAI 原始 SDK 的 tool_call 结构差异（重要实现坑） | 实测：`bind_tools` 返回 **`AIMessage`（无 `choices`）**；`tool_calls` 元素是 **dict** `{"name","args","id","type"}`；**`args` 已是 dict**（非 JSON 字符串）；**`AIMessage` 可原样回传**。若按 OpenAI 原始规范写（`choices[0].message`、`call.function.name`、`json.loads(arguments)`）会全部失败。详见 `docs/agent-dispatcher-verification.md` §2 | 待确认 | 2026-10-03 |
| A1-rag | 知识检索工具接入完成（2026-10-04） | `search_knowledge` 已入白名单，单元 27 项 + 真实检索层 8 项 + 完整 Agent 链路 2 项全部通过。**关键设计：`threshold` 不暴露给模型**（§5 要求由标注问答集实测确定，交给模型自选等于绕过阈值治理）。**检索失败与「未找到」在结构上不可混淆**（失败返回 ok=False + 稳定错误码，payload 不含 found 字段），避免模型把「向量库不可用」误解为「教材里没有」后用记忆填补并标注教材出处 | 待确认 | 2026-10-04 |
| A2-api | 实现 FastAPI 网关（2026-10-04） | 落地 A2/A3：5 个路由 + 13 个 schema，OpenAPI 可生成。**不引入 slowapi**（PyPI 实测最新仅 0.1.10，维护停滞且全局状态难隔离），改用标准库令牌桶（10 项测试）。**实测纠正四处与直觉不符的框架行为**：① SSE 必须声明 `response_class` 后直接 yield，且需 `response_model=None`，否则 TypeError / FastAPIError；② 中文在 SSE 线格式中被转义为 `\uXXXX`，前端须 JSON.parse 才能还原；③ FastAPI 0.132+ 严格校验 Content-Type，缺 JSON 头返回 422（**非 415**）且**回显原始输入**——已重写处理器丢弃 input 并改中文文案；④ starlette 1.7.0 的 TestClient 已迁移 httpx2。**测试抓到 3 个真实缺陷**：限流器多放行一次（建桶后未扣减）、上游错误原文泄露（`LLMUpstreamError` 的 user_message 被原样透出）、配置校验位置错误（burst=0 要等到限流器构造才报错）。**sources 当前为空数组**：`AgentLoop` 返回结构里没有来源字段，前端在补齐前不应显示来源区。实测见 `interface-contract-verification.md` | 待确认 | 2026-10-04 |
| I2-streaming | 实现逐token 流与结构化来源（2026-10-04） | 一并解决 I2 与 I3（都需改 `AgentLoop` 同一循环，分开做等于两次侵入）。**核心设计：`run()` 改为 `stream()` 的消费者**——若新增 stream 而保留旧 run，两条路径会各自演化，「同步能答、流式答不通」比慢更糟。改造后既有 72 项 Agent 测试**无需修改即通过**，是行为等价的最强证据。**实测纠正三处**：① `AIMessageChunk` 只有半个 JSON 时访问 `.tool_calls` **不报错而返回 `args={}`**，故不能拿它判断「是否调用工具」，必须合并完整后再读；② 流式下 `tool_calls` 分片 LangChain **已代为聚合**，不需要手写按 index 拼接；③ 华为 MaaS **确实支持** `stream:true`。**被测试抓到的实现 bug**：`tool` 事件被我嵌在 `EVENT_STAGE` 分支里，两个工具调用一个都没进流——靠直接打印真实 SSE 线格式发现。另修三处替身不合约导致的假失败（缺 `__add__`、缺 `.content`、缺 `stream()`），**每一次看起来都像生产 bug**。详见 `agent-streaming-verification.md` | 待确认 | 2026-10-04 |
| I5-smarts | 拆分醇羟基与酚羟基（2026-10-04） | 原 `[OX2H]` 无法区分醇羟基与羧酸羟基，实测乙酸/苯甲酸/甘氨酸全部误报。**关键判断：不能只满足于消除误报**——联网查到的两种排除写法都会**漏掉苯酚**，而项目语料把「醇羟基 vs 酚羟基」列为高频易错点，工具口径必须与语料一致，故拆成两条。官能团从 11 条增至 12 条，影响 4 个测试文件。**一处断言方向被反转**：原 API 测试写明"乙酸命中羟基不是 bug，是 chem 模块的局限"，改写为禁止误报——保留"记录局限"的测试有价值，但局限消除后须改写而非删掉，否则成为无约束空白。另修正自己写错的期望值（羟基苯甲醇应同时命中两类）。详见 `chem-smarts-verification.md` | 待确认 | 2026-10-04 |
| D3-corpus | 以自编讲义替代教材原文（2026-10-04） | 依据课标四模块编写 41 条语料，交叉参考多个公开教学资料核对事实并梳理 5 处高频易错点（苯酚酸性顺序、苯萃取溴水 vs 苯酚取代、醇催化氧化取决于 α 碳氢数、醇酚双重差异、醛酮差异根源）。**全部标记 project-authored，规避 D3版权风险**。实测：首位正确 27/33、top3 命中 32/33。**内容尚未经化学教师审核**——这是当前最大的未决项 | 待确认 | 2026-10-04 |
| D3-note | 切分策略的实测依据（联网核实） | ① Vectara NAACL 2025（arXiv:2410.13070）：25 种配置 × 48 模型实测，**切分配置对检索质量影响 ≥ embedding 模型选择**，同语料召回率差距可达 9%。② FloTorch 2026（50 篇论文 90 万 token）：递归字符切分 512 token 准确率 69%，固定大小 67%，**语义切分仅 54%**（产出 43 token 碎片，检索中但答不出）。③ 据此确定：不采用语义切分；按字符而非 token 计数（中文 512 字符≈350-500 token，正落在 bge-small-zh 有效区间）。④ **评估必须分离检索命中与端到端正确**——详见 `docs/knowledge-base-implementation.md` §3.1 | 待确认 | 2026-10-04 |
| H19-fix | 工具描述互相点名可改善工具选择（实测） | 改动前"帮我解析乙醇的结构，SMILES 是 CCO"被分配给 `search_knowledge`；在 `search_knowledge` 描述中注明"不解析 SMILES、不做分子式计算，应改用 parse_smiles"，并在化学工具描述中反向注明后，**4/4 用例正确分配**。说明白名单只解决"能不能调"，工具描述的边界声明才影响"调哪个"。详见 `docs/agent-knowledge-tool-verification.md` §5.4 | 待确认 | 2026-10-04 |
| H17-note | chromadb 内置 EF 的中文限制（实测，重要） | `all-MiniLM-L6-v2` 为英文模型，中文语义检索不可用；且默认距离度量是 **l2 非余弦**；集合名须 3-512 字符 `[a-zA-Z0-9._-]`。本层已显式设 `cosine` 并在 `query()` 中**不设阈值默认值**（须由标注问答集实测确定）。详见 `docs/rag-verification.md` §2 | 待确认 | 2026-10-04 |
| H17-dec | 选定 `BAAI/bge-small-zh`（110M，中文） | 理由：体积小推理快，适合现场离线演示；国内最通用中文 embedding，sentence-transformers 原生支持。实测发现两点约束：① **HuggingFace 直连不通（HTTP 000），须代理或预下载权重**（影响 deployment-operations.md §5 断网要求）；② 模型仓库**只提供 pytorch_model.bin，无 safetensors**，加载须 `trust_remote_code=False` 且仅从可信来源获取（security-privacy.md §5 供应链要求）。详见 `docs/dependency-notes.md` | 待确认 | 2026-10-04 |
| H17-ver | bge-small-zh 中文检索质量实测通过 | 实测三个查询（酯化反应/苯的结构/乙醇的分子式）**首位全部正确**，top1 cos 分别为 0.9012 / 0.8249 / 0.9344，无关文档「今天天气很好」**稳定排第 5**。对照：内置英文模型下目标片段排第 3。另验证：向量模长 1.000000（已归一化）、同文本两次嵌入一致、加查询前缀后仍正确。测试以 `-W error::FutureWarning` 运行无警告。**遗留部署约束**：HuggingFace 直连不通（HTTP 000）须代理，断网演示须预下载约 400MB 权重 | 待确认 | 2026-10-04 |
| G6 | `.gitignore` venv 规则改为通配 | 实测 `.venv-xuezhi312/` 原未被忽略；已改 `.venv*/`、`venv*/`、`env*/`、`conda-env*/` | 待确认 | 2026-10-03 |
| G1 | 采用 `.gitignore` 覆盖密钥/权重/受版权材料/派生产物/本地目录，并显式声明应提交的 docs 与清单文件 | `docs/security-privacy.md` §3、§5；`docs/knowledge-base.md` §2 | 待确认 | 2026-10-03 |
| H2 | conda-forge rdkit 支持 Python 3.12，RDKit 不构成基线约束 | rdkit 2026.03.6 共 30 构建，覆盖 py310–py314 × 6 平台，见 `docs/h1-h3-verification.md` §1 | 待确认 | 2026-10-03 |
| H4 | chromadb 1.5.9 无需源码编译，abi3 wheel 适用 3.9+ | `cp39-abi3` 为稳定 ABI 标记；实测 pip 直接选用该 wheel，WHEEL 标签 `cp39-abi3-win_amd64` | 待确认 | 2026-10-03 |
| H4-corr | **更正**此前"chromadb 在 3.10/3.12 需源码编译"的错误判断 | 原误将 wheel 标签当作 Python 版本限制；abi3 表示稳定 ABI 而非仅限 3.9 | 待确认 | 2026-10-03 |
| G2 | 采用上界约束文件 `requirements-lock.txt` 作为过渡方案；传递依赖解析与 hash 待隔离环境补全 | PyPI 官方 JSON API 实测（2026-10-03），见 `docs/dependency-notes.md` | 待确认 | 2026-10-03 |
| G2-a | numpy 上界锁定 ~~2.2.6~~ → **2.5.3**（基线升至 3.12 后解除降级） | 原为适配 3.10 基线（2.3.0 起无 cp310 wheel、2.5.3 要求 >=3.12）而降至 2.2.6；H1 决策后放开至最新版 2.5.3 | 待确认 | 2026-10-03 |
| G3 | torch 暂不锁定，待目标机实测后回填 | torch 2.14.1 的 CUDA 依赖随平台与 CUDA 版本变化，无法跨平台单一锁定 | 待决策 | — |
| G4 | autoawq 暂不纳入锁定，评估 llmcompressor 等替代需重新验证量化精度 | autoawq 0.2.9 纯 sdist、2025-05-11 后停更；llmcompressor 为不同实现 | 待决策 | — |
