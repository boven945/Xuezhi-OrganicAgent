# 依赖选型实测记录

本文记录 `requirements.txt` / `requirements-cloud.txt` 版本约束的实测依据。
**原则：所有版本号必须有 PyPI 官方元数据或目标机实测支撑，不凭记忆断言。**

- 抓取日期：2026-10-03
- 数据来源：PyPI 官方 JSON API（`https://pypi.org/pypi/<pkg>/json`）
- 抓取范围：`info.version`、`info.requires_python`、`info.requires_dist`、各版本 wheel 文件名与平台标签
- Python 基线：3.10（依据 `docs/deployment-operations.md` §2）

## 1. 版本实测结果

| 包 | 实测最新版本 | requires_python | 3.10 兼容 | 处置 |
| --- | --- | --- | --- | --- |
| fastapi | 0.142.2 | >=3.10 | 是 | 锁定 |
| uvicorn | 0.54.0 | >=3.10 | 是 | 锁定 |
| pydantic | 2.13.5 | 未声明 | 是 | 锁定 |
| langchain | 1.4.3 | >=3.10,<4.0 | 是 | 锁定 |
| langchain-openai | 1.6.7 | >=3.10,<4.0 | 是 | 锁定 |
| chromadb | 1.5.9 | >=3.9 | 是 | 锁定 |
| sentence-transformers | 6.1.0 | >=3.10 | 是 | 锁定 |
| loguru | 0.7.3 | >=3.5,<4.0 | 是 | 锁定 |
| numpy | 2.5.3 | **>=3.12** | **否** | 降至 2.2.6 |
| pandas | 3.0.6 | 未声明 | 是 | 锁定 |
| python-dotenv | 1.2.4 | >=3.10 | 是 | 锁定 |
| requests | 2.34.2 | >=3.10 | 是 | 锁定 |
| accelerate | 1.15.0 | >=3.10 | 是 | 锁定 |
| transformers | 5.18.0 | >=3.10 | 是 | 锁定（兼容性待验证） |
| torch | 2.14.1 | >=3.10 | 是 | **不锁定，见 §3** |
| rdkit | 2026.3.6 | 未声明 | 是 | conda-forge 安装，不入 pip 清单 |
| autoawq | 0.2.9 | >=3.8 | 是 | **不锁定，见 §4** |

## 2. numpy 版本降级依据（重要）

实测发现 **numpy 2.3.0 起不再发布 cp310 wheel**。

- numpy 2.5.3（当前最新）`requires_python >= 3.12`，且无 cp310 wheel。
- 仍提供 cp310 wheel 的最高 2.x 版本为 **2.2.6**。
- 项目 Python 基线为 3.10，故上界锁定 `numpy==2.2.6`。

若后续将 Python 基线升级至 3.12，可放开至 2.5.x；在此之前使用 2.3+ 会因
无 cp310 wheel 而退化为源码编译。相关约束登记于决策登记表 C6/G2。

## 3. torch 不做单一版本锁定的原因

实测事实：

- torch 2.14.1 提供 manylinux(12) / win_amd64(6) / macosx(6) 个 wheel，覆盖 cp310–cp314。
- Linux 下 CUDA 支持通过条件依赖引入：`cuda-toolkit[...]==13.0.3`、
  `nvidia-cudnn-cu13==9.24.0.43`、`nvidia-nccl-cu13==2.30.7`、`triton~=3.8.0`。

因此 torch 的实际安装结果取决于目标机的 CUDA 版本、驱动版本与平台，
无法用单一版本号跨平台锁定。目标演示机为 RTX 5070，其 CUDA 架构与所需驱动
组合**尚未实测**（决策登记表 B1/B2）。

处置：由 infra 模块在目标机执行实测后，将确定的版本与 `index-url` 回填
`requirements-lock.txt`。在此之前，本项目禁止沿用任何未经实测的 torch 版本。

## 4. autoawq 未纳入锁定的风险

实测事实：

- autoawq 0.2.9 为**纯 sdist**（`autoawq-0.2.9.tar.gz`），**无预编译 wheel**，
  安装需本地编译 CUDA 扩展，对 CUDA toolkit 与编译器有额外要求。
- 发布历史止于 2025-05-11，此后无新版本，维护活跃度存疑。
- 依赖声明 `transformers>=4.45.0`，**无上界**，与 transformers 5.18.0 的
  实际兼容性未经验证。

影响范围：仅本地 openPangu-7B AWQ 推理模式。云端 ModelArts MaaS API 模式
不使用该包，不受影响。

备选方案评估：`llmcompressor` 0.14.0（提供 wheel，requires_python>=3.10），
实测其约束为 `transformers>=5.15.0,<=5.17.0`、`torch>=2.10.0,<=2.14.0`、
`accelerate==1.15.0`。**但它与 autoawq 不是同一实现**，AWQ 量化精度与
openPangu-7B 权重的兼容性需重新评估，不能直接替换。

处置：本地 AWQ 推理在实测验证通过前不纳入锁定。该风险已登记为决策登记表
B1/B2 的前置阻塞项。

## 5. 未覆盖内容

- **传递依赖未解析**：本轮只核验直接依赖的版本与 Python 兼容性。
- **artifact hash 未记录**：`pip-compile --generate-hashes` 待在隔离环境执行。
- **许可证与漏洞审查未执行**：属决策登记表 E4。

因此 `requirements-lock.txt` 当前是**上界约束文件**，不等价于可复现锁定。
在上述三项完成前，任何环境都不得被描述为"可复现安装"，发布前须按
`docs/deployment-operations.md` §7 记录实测组合。

## 6. 复核方式

```bash
# 校验当前约束是否仍与 PyPI 一致（示例）
pip install --dry-run -c requirements-lock.txt -r requirements.txt

# 查看某包全部可用版本与平台标签
curl -s https://pypi.org/pypi/<pkg>/json | python -m json.tool | head -50
```

版本选型变化时，须更新本文并同步 `requirements*.txt` 与决策登记表。
