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
| G1 | 仓库缺少 `.gitignore` | 存在密钥、`.env`、模型权重、构建产物误提交风险 | infra | 待处理 |
| G2 | 依赖未锁定 | requirements 仅给出下限版本，非可复现安装 | infra | 待处理 |

---

## 决策记录

确认后的决策在此追加记录，便于追溯。

| 编号 | 结论 | 依据 | 确认人 | 日期 |
| --- | --- | --- | --- | --- |
| — | — | — | — | — |
