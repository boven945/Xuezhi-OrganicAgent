# Python 版本评估：3.10 → 3.12

> **状态：已决策（2026-10-03）。项目 Python 基线确定为 3.12。**
> 决策记录见 `decision-register.md` H1。本文保留完整评估过程与对照数据，
> 作为决策依据与后续复核基准，不再作为待决事项。

本文基于 PyPI 官方元数据实测，对比 Python 3.10 与 3.12 两种基线下的依赖可用性，
为项目 Python 基线选型提供依据。

- 实测日期：2026-10-03
- 数据来源：PyPI 官方 JSON API（`https://pypi.org/pypi/<pkg>/json`），
  取 `info.version`、`info.requires_python`、`releases[*].filename`
- 结论性质：**依赖兼容性结论**，不等于运行时验证。实际安装与联调仍需在隔离环境执行

## 1. 结论摘要

**升级到 Python 3.12 不会使任何现有依赖变得不可用，且能解锁 numpy 与 pandas 的新版本。**

| 影响 | 结论 |
| --- | --- |
| 依赖兼容性 | **无阻断**。实测 16 个直接依赖在 3.12 下全部可满足 |
| numpy | **改善**。可从 2.2.6 升到 2.5.3（3.10 下 2.3.0+ 无 cp310 wheel） |
| pandas | **改善**。可从 2.3.3 升到 3.0.6（3.10 下 3.x 无 cp310 wheel） |
| torch | **无变化**。cp312 的 manylinux/win_amd64 wheel 均存在，非瓶颈 |
| autoawq | **无改善**。仍是纯 sdist 需编译，与 Python 版本无关 |
| chromadb | **无变化**。1.5.9 提供 `cp39-abi3` 稳定 ABI wheel，3.10/3.12 均可直接安装 |
| 文档现状 | `requirements-lock.txt` 中 `pandas==3.0.6` 在 3.10 下装不上；**基线升至 3.12 后已解除** |

> **核查更正（2026-10-03）**：本文初稿曾判断 chromadb 在 3.10/3.12 下需源码编译，
> 该结论**错误**。`chromadb-1.5.9-cp39-abi3-*.whl` 中的 `abi3` 是稳定 ABI 标记，
> 表示适用于 Python 3.9 及以上所有版本。已实测 pip 直接选用该 wheel，未进入编译路径。
> 详见 `h1-h3-verification.md`。conda-forge rdkit 对 3.12 的支持亦已确认（H1）。

## 2. 实测对照表

| 包 | 最新版本 | requires_python | cp310 wheel | cp312 wheel | 3.10 可用 | 3.12 可用 |
| --- | --- | --- | --- | --- | --- | --- |
| numpy | 2.5.3 | `>=3.12` | 无 | 有 | 需降至 2.2.6 | **最新可用** |
| pandas | 3.0.6 | `>=3.11` | 无 | 有（9 个） | 需降至 2.3.3 | **最新可用** |
| torch | 2.14.1 | `>=3.10` | 有 | 有 | 可用 | 可用 |
| transformers | 5.18.0 | `>=3.10.0` | 有 | 有 | 可用 | 可用 |
| accelerate | 1.15.0 | `>=3.10.0` | 有 | 有 | 可用 | 可用 |
| fastapi | 0.142.2 | `>=3.10` | 有 | 有 | 可用 | 可用 |
| uvicorn | 0.54.0 | `>=3.10` | 有 | 有 | 可用 | 可用 |
| pydantic | 2.13.5 | `>=3.9` | 纯 py3 wheel | 纯 py3 wheel | 可用 | 可用 |
| chromadb | 1.5.9 | `>=3.9` | abi3 wheel | abi3 wheel | 可用 | 可用 |
| sentence-transformers | 6.1.0 | `>=3.10` | 有 | 有 | 可用 | 可用 |
| loguru | 0.7.3 | `>=3.5,<4.0` | 有 | 有 | 可用 | 可用 |
| python-dotenv | 1.2.4 | `>=3.10` | 有 | 有 | 可用 | 可用 |
| requests | 2.34.2 | `>=3.10` | 有 | 有 | 可用 | 可用 |
| langchain | 1.4.3 | `>=3.10,<4.0` | 有 | 有 | 可用 | 可用 |
| langchain-openai | 1.6.7 | `>=3.10,<4.0` | 有 | 有 | 可用 | 可用 |
| autoawq | 0.2.9 | `>=3.8` | **无（纯 sdist）** | **无（纯 sdist）** | 需编译 | 需编译 |

**关键判定：不存在"cp310 可用但 cp312 不可用"的包。** 升级无兼容性阻断。

## 3. 升级的核心收益：解除 numpy/pandas 版本压制

原 3.10 基线把两个科学计算包压在旧版本：

| 包 | 3.10 下可用上限 | 3.12 下可用上限 | 压制原因 |
| --- | --- | --- | --- |
| numpy | 2.2.6 | 2.5.3 | 2.3.0 起不再发布 cp310 wheel；2.5.3 要求 `>=3.12` |
| pandas | 2.3.3 | 3.0.6 | 3.0.0 起不再发布 cp310 wheel；3.0.6 要求 `>=3.11` |

pandas 是更严重的一个：3.0.6 的 `requires_python` 为 `>=3.11`，**即使有 wheel 也不接受
3.10 环境**。这曾导致 `requirements-lock.txt` 中的 `pandas==3.0.6` 声明在 Python 3.10
下无法安装（H5）；**基线确定为 3.12 后该缺陷自然解除**，现锁定 3.0.6。

## 4. 升级无法解决的问题

### 4.1 autoawq（G4 不受 Python 版本影响）

实测 autoawq 0.2.9 仅有 `autoawq-0.2.9.tar.gz`，**无任何预编译 wheel**，且
`requires_python>=3.8` 对 3.12 同样成立。安装仍需本地编译 CUDA 扩展。

其依赖声明中 `transformers>=4.45.0` **无上界**，而 transformers 当前为 5.18.0。
该组合能否正常工作与 Python 版本无关，需实测。

结论：升级到 3.12 **不能**解决 G4。

### 4.2 chromadb（H3 已核查：无需编译）

chromadb 1.5.9 分发文件中的 `cp39-abi3` 为 **稳定 ABI（Stable ABI）** 标记，
可在 Python 3.9 及以上所有版本安装。实测 pip 直接选用
`chromadb-1.5.9-cp39-abi3-win_amd64.whl`，未进入源码编译路径。

该 wheel 由 maturin 构建，内含 `chromadb_rust_bindings` Rust 扩展，但通过 abi3
打包后跨 Python 版本通用，**用户侧无需 Rust 工具链或 cmake**。

结论：chromadb 在 3.10 与 3.12 下均可直接安装，**不构成架构风险**。
核查过程见 `h1-h3-verification.md`。

### 4.3 torch（G3 不受 Python 版本影响）

实测 torch 2.14.1 提供 cp310–cp314 全系列 wheel，含
`torch-2.14.1-cp312-cp312-manylinux_2_28_x86_64.whl`。Python 版本不是 torch 的约束条件。

G3 的真正约束是目标机 RTX 5070 的 CUDA 架构与驱动组合，与 Python 版本选择无关。

## 5. 其他需要一并考虑的因素

| 因素 | 评估 | 说明 |
| --- | --- | --- |
| conda RDKit | **已确认支持** | conda-forge rdkit 2026.03.6 覆盖 py310–py314 × 6 平台（H1 已核查） |
| Fay 服务 | 需确认 | Fay 与 Edge-TTS 的 Python 版本约束未知，属决策登记表 A4/H2 |
| AutoAWQ 编译链 | 3.12 风险略高 | 编译型扩展对新 Python 的适配通常滞后；3.12 发布于 2023-10，距今已久，风险已下降 |
| chromadb | **已确认可用** | abi3 稳定 ABI wheel，3.10/3.12 均可直接安装（H3 已核查） |
| 迁移成本 | **当前接近零** | 仓库尚无任何源码，不存在代码改写与回归验证负担 |
| 生态一致性 | 3.12 更优 | langchain 1.x、transformers 5.x 新特性优先面向 3.11+ |

## 6. 决策结论与执行状态

### 已采纳的决策

**项目 Python 基线确定为 3.12。** 理由：

1. 无兼容性阻断（实测 16 个直接依赖全部满足）。
2. 解除 numpy/pandas 版本压制：numpy 放开至 2.5.3、pandas 放开至 3.0.6
   （3.10 下分别被压制在 2.2.6 与 2.3.3）。
3. 迁移成本为零：决策时仓库尚无源码。
4. 与 langchain 1.x / transformers 5.x 的版本支持方向一致。

### 已执行的变更

| 文件 | 变更 |
| --- | --- |
| `README.md` | 技术栈改为 Python 3.12；`conda create` 命令改为 `python=3.12` |
| `docs/deployment-operations.md` | 环境基线表与发布流程改为 Python 3.12；RDKit 标注 py312 构建可用 |
| `requirements.txt` / `requirements_cloud.txt` | 头部基线声明改为 3.12；numpy/pandas 下限放开 |
| `requirements-lock.txt` | `numpy==2.2.6` → `numpy==2.5.3`；pandas 冲突注释改为已解除 |
| `docs/dependency-notes.md` | 基线改为 3.12；对照表增加"3.12 兼容"列；numpy 章节改为"压制已解除" |

原 pandas 版本冲突（`pandas==3.0.6` 要求 `>=3.11`）随基线升级自然解除，
无需降级方案。

### 后续待办

| 编号 | 事项 | 负责模块 | 状态 |
| --- | --- | --- | --- |
| H1 | Python 基线选型 | — | **已决策：3.12** |
| H2 | 确认 conda-forge rdkit 对 3.12 的支持 | infra | **已解决**（py310–py314 × 6 平台） |
| H3 | 确认 Fay / Edge-TTS 的 Python 版本约束 | infra | 待决策（随 A4 一并确认） |
| H4 | chromadb 源码编译可行性 | infra | **已关闭**（abi3 wheel 无需编译） |
| H5 | pandas 版本冲突 | infra | **已解除**（随基线升级） |
| H6 | 在 Python 3.12 环境下执行完整安装实测并记录组合 | infra | **部分完成**：dry-run 通过（118 包无冲突）；完整安装因网络 33 kB/s 未完成，见 `py312-install-verification.md` |
| H7 | 云端精简版实际会安装 torch | infra | **已发现并更正表述**（sentence-transformers 硬依赖） |
| H8 | 是否需要彻底避免安装 torch | 项目负责人 | 待决策（涉及 RAG 向量化选型） |

## 7. 复核方式

```bash
# 校验某包在指定 Python 版本下是否有可用 wheel
curl -s https://pypi.org/pypi/<pkg>/json | python -c "
import sys, json
d = json.load(sys.stdin)
v = d['info']['version']
print('version:', v, 'requires_python:', d['info']['requires_python'])
for f in d['releases'][v]:
    print(' ', f['filename'])
"

# 目标机上的实际安装验证（务必在隔离环境执行）
conda create -n xuezhi312 python=3.12
conda activate xuezhi312
pip install --dry-run -c requirements-lock.txt -r requirements.txt
```

基线决策确定后，须同步更新本文、决策登记表与相关文档。
