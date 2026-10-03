# Python 版本升级评估：3.10 → 3.12

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
| 依赖兼容性 | **无阻断**。实测 14 个直接依赖在 3.12 下全部可满足 |
| numpy | **改善**。可从 2.2.6 升到 2.5.3（3.10 下 2.3.0+ 无 cp310 wheel） |
| pandas | **改善**。可从 2.3.3 升到 3.0.6（3.10 下 3.x 无 cp310 wheel） |
| torch | **无变化**。cp312 的 manylinux/win_amd64 wheel 均存在，非瓶颈 |
| autoawq | **无改善**。仍是纯 sdist 需编译，与 Python 版本无关 |
| chromadb | **无变化**。1.5.9 仅 cp39 wheel + sdist，两个版本同样需编译 |
| 文档现状 | `requirements-lock.txt` 中 `pandas==3.0.6` **在 3.10 下装不上**，属既存缺陷 |

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
| chromadb | 1.5.9 | `>=3.9` | 仅 cp39 + sdist | 仅 cp39 + sdist | 需编译 | 需编译 |
| sentence-transformers | 6.1.0 | `>=3.10` | 有 | 有 | 可用 | 可用 |
| loguru | 0.7.3 | `>=3.5,<4.0` | 有 | 有 | 可用 | 可用 |
| python-dotenv | 1.2.4 | `>=3.10` | 有 | 有 | 可用 | 可用 |
| requests | 2.34.2 | `>=3.10` | 有 | 有 | 可用 | 可用 |
| langchain | 1.4.3 | `>=3.10,<4.0` | 有 | 有 | 可用 | 可用 |
| langchain-openai | 1.6.7 | `>=3.10,<4.0` | 有 | 有 | 可用 | 可用 |
| autoawq | 0.2.9 | `>=3.8` | **无（纯 sdist）** | **无（纯 sdist）** | 需编译 | 需编译 |

**关键判定：不存在"cp310 可用但 cp312 不可用"的包。** 升级无兼容性阻断。

## 3. 升级的核心收益：解除 numpy/pandas 版本压制

Python 3.10 基线当前把两个科学计算包压在旧版本：

| 包 | 3.10 下可用上限 | 3.12 下可用上限 | 压制原因 |
| --- | --- | --- | --- |
| numpy | 2.2.6 | 2.5.3 | 2.3.0 起不再发布 cp310 wheel；2.5.3 要求 `>=3.12` |
| pandas | 2.3.3 | 3.0.6 | 3.0.0 起不再发布 cp310 wheel；3.0.6 要求 `>=3.11` |

pandas 是更严重的一个：3.0.6 的 `requires_python` 为 `>=3.11`，**即使有 wheel 也不接受
3.10 环境**。这意味着 `requirements-lock.txt` 中当前的 `pandas==3.0.6` 声明在
Python 3.10 下无法安装——这是需要修正的既存缺陷（见 §6）。

## 4. 升级无法解决的问题

### 4.1 autoawq（G4 不受 Python 版本影响）

实测 autoawq 0.2.9 仅有 `autoawq-0.2.9.tar.gz`，**无任何预编译 wheel**，且
`requires_python>=3.8` 对 3.12 同样成立。安装仍需本地编译 CUDA 扩展。

其依赖声明中 `transformers>=4.45.0` **无上界**，而 transformers 当前为 5.18.0。
该组合能否正常工作与 Python 版本无关，需实测。

结论：升级到 3.12 **不能**解决 G4。

### 4.2 chromadb

chromadb 1.5.9 的发布文件为 5 个 cp39 wheel + 1 个 sdist，**不提供 cp310/cp312 wheel**。
在 3.10 和 3.12 下都需从源码编译。升级不改变这一点。

需在隔离环境实测编译能否通过（依赖 Rust toolchain 与 cmake），属决策登记表 A6。

### 4.3 torch（G3 不受 Python 版本影响）

实测 torch 2.14.1 提供 cp310–cp314 全系列 wheel，含
`torch-2.14.1-cp312-cp312-manylinux_2_28_x86_64.whl`。Python 版本不是 torch 的约束条件。

G3 的真正约束是目标机 RTX 5070 的 CUDA 架构与驱动组合，与 Python 版本选择无关。

## 5. 其他需要一并考虑的因素

| 因素 | 评估 | 说明 |
| --- | --- | --- |
| conda RDKit | 需确认 | 须确认 conda-forge 是否提供 Python 3.12 的 rdkit 构建 |
| Fay 服务 | 需确认 | Fay 与 Edge-TTS 的 Python 版本约束未知，属决策登记表 A4 |
| AutoAWQ 编译链 | 3.12 风险略高 | 编译型扩展对新 Python 的适配通常滞后；3.12 发布于 2023-10，距今已久，风险已下降 |
| 迁移成本 | **当前接近零** | 仓库尚无任何源码，不存在代码改写与回归验证负担 |
| 生态一致性 | 3.12 更优 | langchain 1.x、transformers 5.x 新特性优先面向 3.11+ |

## 6. 建议与待办

### 建议

**建议将 Python 基线从 3.10 提升至 3.12。** 理由：

1. 无兼容性阻断（实测 14 个直接依赖全部满足）。
2. 解除 numpy/pandas 版本压制，pandas 尤其关键（3.10 下无法使用 3.x）。
3. 当前无源码，迁移成本接近零；一旦开始写代码再改基线，成本会显著上升。
4. 与 langchain 1.x / transformers 5.x 的版本支持方向一致。

### 无论选哪个版本都需修正的既存缺陷

`requirements-lock.txt` 中的 `pandas==3.0.6` 与 Python 3.10 基线冲突
（`requires_python>=3.11`），**当前状态下无法安装**。修正方式取决于基线决策：

- 基线保持 3.10 → 改为 `pandas==2.3.3`
- 基线升至 3.12 → 维持 `pandas==3.0.6`，同时 numpy 可从 2.2.6 放开至 2.5.3

### 待办

| 编号 | 事项 | 负责模块 | 状态 |
| --- | --- | --- | --- |
| H1 | 确认 conda-forge 的 rdkit 是否支持 Python 3.12 | infra | 待决策 |
| H2 | 确认 Fay / Edge-TTS 的 Python 版本约束 | infra | 待决策 |
| H3 | 在隔离环境实测 chromadb 1.5.9 源码编译是否通过 | infra | 待决策 |
| H4 | 更新 `requirements*.txt`、`deployment-operations.md`、README 中的 Python 版本声明 | infra | 待决策 |
| H5 | 在隔离环境执行 Python 3.12 下的完整安装并记录实测组合 | infra | 待决策 |

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
