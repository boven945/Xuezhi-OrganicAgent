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
| A1 | MCP 调度层的协议版本、传输方式、工具 schema 与权限边界 | architecture.md §7 | backend-agent | 架构 | 待决策 |
| A2 | API 路由、认证方式、会话存储方式 | architecture.md §7 | backend-api | 架构 | 待决策 |
| A3 | 同步响应 / 流式输出 / 任务轮询 / 推送通道的取舍 | architecture.md §3 | backend-api、frontend-web | 架构 | 待决策 |
| A4 | Fay SDK/服务版本、通信协议、端口、音视频数据流向 | architecture.md §7 | backend-speech | 架构 | 待决策 |
| A5 | 本地模型服务适配器形式、权重分发与校验方式 | architecture.md §7 | backend-llm | 架构 | 待决策 |
| A6 | ChromaDB 部署方式、持久化卷、备份周期、并发访问策略 | architecture.md §7 | backend-rag | 架构 | 待决策 |
| A7 | 服务部署位置、TLS/反向代理、监控与告警系统 | architecture.md §7 | infra | 运维 | 待决策 |

> A3 与 A4 相互依赖：Fay 服务的实际能力决定前端是否需要流式通道。建议 A4 先于 A3 确认。

## B. 实测与硬件基线

| 编号 | 决策项 | 来源 | 验证方式 | 负责角色 | 状态 |
| --- | --- | --- | --- | --- | --- |
| B1 | RTX 5070 显存实测容量与本地推理可行性 | architecture.md §7、deployment-operations.md §2 | 目标机器实跑模型加载并记录峰值显存 | 开发 | 待决策 |
| B2 | NVIDIA 驱动 / CUDA / PyTorch / Transformers / AutoAWQ 兼容组合 | deployment-operations.md §5 | 隔离环境逐项验证并记录版本 | 开发 | 待决策 |
| B3 | openPangu-7B-Instruct AWQ 4-bit 权重来源、授权与校验和 | deployment-operations.md §5 | 官方渠道核验并记录 checksum | 项目负责人 | 待决策 |
| B4 | MaaS 模型 ID、endpoint、认证方式、配额、区域、服务条款 | deployment-operations.md §4 | 华为云控制台核实 | 项目负责人 | 待决策 |
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
| D2 | 知识库内容的使用许可与授权范围 | knowledge-base.md §2 | 项目负责人 | 待决策 |
| D3 | 已批准测试集的题目构成与标注规范 | knowledge-base.md §6 | 教师/审核者 | 待决策 |

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
| F1 | 实际启动命令、监听地址、端口、健康检查地址 | deployment-operations.md | backend-api、infra | 待回填 |
| F2 | 配置键清单（键名、用途、必需性、默认行为、密钥级别、覆盖方式） | deployment-operations.md §6 | infra | 待回填 |
| F3 | `.env.example` | 仓库根 | infra | 待回填 |
| F4 | OpenAPI 文件与契约测试 | interface-contract.md | backend-api | 待回填 |
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
| H6 | 在 Python 3.12 环境下执行完整安装实测并记录组合 | 3.12.14 venv 中 dry-run 通过（118 包无冲突）；完整安装与 import 冒烟测试因网络 33 kB/s 未完成 | infra | 部分完成 |
| H7 | 云端精简版实际会安装 torch | sentence-transformers 硬依赖 `torch>=2.2`，实测解析出 118 包含 torch；原文档"不含 torch"表述错误 | infra | 已修正表述 |
| H8 | 是否需要彻底避免安装 torch | 若云端模式无需本地 embedding，可移除 sentence-transformers；影响 RAG 向量化方案选型 | 项目负责人 | 待决策 |

---

## 决策记录

确认后的决策在此追加记录，便于追溯。

| 编号 | 结论 | 依据 | 确认人 | 日期 |
| --- | --- | --- | --- | --- |
| H1 | **项目 Python 基线确定为 3.12** | 实测 16 个直接依赖在 3.12 下全部满足、无阻断；解除 numpy（→2.5.3）与 pandas（→3.0.6）版本压制；决策时仓库无源码，迁移成本为零。详见 `docs/python-version-evaluation.md` | 项目负责人 | 2026-10-03 |
| H5 | pandas 版本冲突随基线升级解除，无需降级方案 | pandas 3.0.6 要求 Python>=3.11，在 3.12 基线下可正常安装 | 待确认 | 2026-10-03 |
| H6 | Python 3.12 下依赖解析验证通过 | venv Python 3.12.14 + pip 26.2.1，numpy 2.5.3 / pandas 3.0.6 均命中 cp312 wheel；requirements_cloud.txt 解析 118 包 EXIT=0 无冲突 | 待确认 | 2026-10-03 |
| H7-corr | **更正**"云端精简版不含 torch"的错误表述 | sentence-transformers 硬依赖 torch>=2.2，实测解析结果含 torch-2.14.1；已在 README、requirements_cloud.txt、dependency-notes.md 更正 | 待确认 | 2026-10-03 |
| G6 | `.gitignore` venv 规则改为通配 | 实测 `.venv-xuezhi312/` 原未被忽略；已改 `.venv*/`、`venv*/`、`env*/`、`conda-env*/` | 待确认 | 2026-10-03 |
| G1 | 采用 `.gitignore` 覆盖密钥/权重/受版权材料/派生产物/本地目录，并显式声明应提交的 docs 与清单文件 | `docs/security-privacy.md` §3、§5；`docs/knowledge-base.md` §2 | 待确认 | 2026-10-03 |
| H2 | conda-forge rdkit 支持 Python 3.12，RDKit 不构成基线约束 | rdkit 2026.03.6 共 30 构建，覆盖 py310–py314 × 6 平台，见 `docs/h1-h3-verification.md` §1 | 待确认 | 2026-10-03 |
| H4 | chromadb 1.5.9 无需源码编译，abi3 wheel 适用 3.9+ | `cp39-abi3` 为稳定 ABI 标记；实测 pip 直接选用该 wheel，WHEEL 标签 `cp39-abi3-win_amd64` | 待确认 | 2026-10-03 |
| H4-corr | **更正**此前"chromadb 在 3.10/3.12 需源码编译"的错误判断 | 原误将 wheel 标签当作 Python 版本限制；abi3 表示稳定 ABI 而非仅限 3.9 | 待确认 | 2026-10-03 |
| G2 | 采用上界约束文件 `requirements-lock.txt` 作为过渡方案；传递依赖解析与 hash 待隔离环境补全 | PyPI 官方 JSON API 实测（2026-10-03），见 `docs/dependency-notes.md` | 待确认 | 2026-10-03 |
| G2-a | numpy 上界锁定 ~~2.2.6~~ → **2.5.3**（基线升至 3.12 后解除降级） | 原为适配 3.10 基线（2.3.0 起无 cp310 wheel、2.5.3 要求 >=3.12）而降至 2.2.6；H1 决策后放开至最新版 2.5.3 | 待确认 | 2026-10-03 |
| G3 | torch 暂不锁定，待目标机实测后回填 | torch 2.14.1 的 CUDA 依赖随平台与 CUDA 版本变化，无法跨平台单一锁定 | 待决策 | — |
| G4 | autoawq 暂不纳入锁定，评估 llmcompressor 等替代需重新验证量化精度 | autoawq 0.2.9 纯 sdist、2025-05-11 后停更；llmcompressor 为不同实现 | 待决策 | — |
