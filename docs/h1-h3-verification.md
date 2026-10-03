# H1 / H3 核查结论

本文记录对决策登记表 H1（conda-forge rdkit 是否支持 Python 3.12）与
H3（chromadb 源码编译可行性）的实测核查结果。

- 核查日期：2026-10-03
- 数据来源：Anaconda.org conda-forge API（`api.anaconda.org/package/conda-forge/rdkit`）、
  PyPI 官方 JSON API、pip 实际下载行为、wheel 元数据解压
- 关联文档：`python-version-evaluation.md`、`dependency-notes.md`

## 1. H1 结论：conda-forge rdkit 支持 Python 3.12

**判定：支持。H1 无阻塞。**

conda-forge `rdkit` 最新版本为 **2026.03.6**，共 30 个构建，覆盖
Python 3.10–3.14 × 6 平台。`py312` 构建在全部平台齐备。

| Python | 平台支持 |
| --- | --- |
| 3.10 | linux-64, linux-aarch64, linux-ppc64le, osx-64, osx-arm64, win-64 |
| 3.11 | 同上 |
| **3.12** | **同上** |
| 3.13 | 同上 |
| 3.14 | 同上 |

原始构建文件名（节选，均为 linux-64）：

```
rdkit-2026.03.6-py310h2cedb74_0.conda
rdkit-2026.03.6-py311hc027fe1_0.conda
rdkit-2026.03.6-py312h5eb2359_0.conda
rdkit-2026.03.6-py313hf4faf10_0.conda
rdkit-2026.03.6-py314hd4f27b0_0.conda
```

安装方式不受影响，仍为：

```bash
conda install -c conda-forge rdkit
```

结论：**Python 3.10 与 3.12 在 RDKit 上均可安装，基线选择不受 RDKit 约束。**

### 核查方法说明

`api.anaconda.org/package/conda-forge/rdkit` 返回的 `latest_version` 字段为
`Release_2017_09_3`，**该字段不可信**（返回的是历史归档视图）。真实版本需从
`files[*].basename` 扫描并按 `upload_time` / `version` 判定。

## 2. H3 结论：chromadb 无需源码编译

**判定：不需要编译。此前的判断有误，现予更正。**

chromadb 1.5.9 的分发文件：

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `chromadb-1.5.9-cp39-abi3-win_amd64.whl` | wheel | 23.5 MB |
| `chromadb-1.5.9-cp39-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl` | wheel | 23.3 MB |
| `chromadb-1.5.9-cp39-abi3-manylinux_2_17_aarch64.manylinux2014_aarch64.whl` | wheel | 22.7 MB |
| `chromadb-1.5.9-cp39-abi3-macosx_11_0_arm64.whl` | wheel | 21.7 MB |
| `chromadb-1.5.9-cp39-abi3-macosx_10_12_x86_64.whl` | wheel | 22.6 MB |
| `chromadb-1.5.9.tar.gz` | sdist | 2.6 MB，**不会被 pip 自动选用** |

### 更正说明

`cp39-abi3` 中的 `abi3` 是 **稳定 ABI（Stable ABI）标记**，含义是该 wheel
可在 Python 3.9 及其后的所有版本上安装。**`cp39-abi3` 不等于"仅支持 3.9"。**

此前把 wheel 标签当作 Python 版本限制，因而误判"3.10/3.12 需从源码编译"。
该判断错误，现更正。

### 实测验证

pip 实际下载行为（在 Python 3.13 环境下执行，验证标签通用性）：

```
$ pip download --no-deps "chromadb==1.5.9"
Using cached chromadb-1.5.9-cp39-abi3-win_amd64.whl (23.5 MB)
Successfully downloaded chromadb
```

pip 直接选中预编译 wheel，**未进入源码编译路径**。

wheel 元数据：

```
Wheel-Version: 1.0
Generator: maturin (1.13.1)
Root-Is-Purelib: false
Tag: cp39-abi3-win_amd64
```

wheel 内含 1 个二进制扩展：`chromadb_rust_bindings/chromadb_rust_bindings.pyd`。
即 chromadb 确有 Rust 扩展（由 maturin 构建），但通过 abi3 稳定 ABI 打包，
因此**跨 Python 版本通用，用户侧无需 Rust 工具链或 cmake**。

结论：**chromadb 1.5.9 在 Python 3.10 与 3.12 下均可直接 pip 安装预编译 wheel。**

## 3. 修正后的风险清单

| 编号 | 原判断 | 修正后 | 状态 |
| --- | --- | --- | --- |
| H1 | conda rdkit 是否支持 3.12 待确认 | **支持**，30 个构建覆盖 py310–py314 × 6 平台 | 已解决 |
| H3 | chromadb 需源码编译 | **无需编译**，abi3 wheel 直接可用 | 已解决 |
| H2 | Fay / Edge-TTS 的 Python 版本约束 | 仍未知，随 A4 一并确认 | 待决策 |
| H4 | chromadb 编译可行性 | 已由 H3 覆盖，关闭 | 已关闭 |

### 仍然存在的真实风险

| 项 | 风险 | 说明 |
| --- | --- | --- |
| **G4** | autoawq 0.2.9 纯 sdist | 无 wheel、无 abi3，必须本地编译。**这是当前唯一确需编译的依赖**，风险不变 |
| **G3** | torch CUDA 组合 | cp312 wheel 齐备，但 RTX 5070 的驱动/CUDA 组合仍未实测 |
| 传递依赖 | 未做全量解析 | `grpcio`、`onnxruntime`、`tokenizers`、`bcrypt` 等传递依赖的平台覆盖尚未逐一核对 |

## 4. 对 Python 基线建议的影响

H1 与 H3 的核查结果**移除了此前的两项顾虑**：

- RDKit 不再是基线约束（3.10/3.12 均可装）。
- chromadb 不再是"需编译"的架构风险（abi3 wheel 通用）。

因此 `python-version-evaluation.md` 中"升级 3.12 无兼容性阻断"的结论得到进一步支持，
且反对理由进一步减少。**升级到 3.12 的收益（解除 numpy/pandas 压制）与风险现在更加不对等。**

剩余待确认项仅剩 H2（Fay/Edge-TTS 版本约束），属 A4 的附属问题，不影响基线决策。

## 5. 核查方法复用

```bash
# conda-forge 包的真实版本与构建矩阵（注意 latest_version 字段不可信）
curl -s https://api.anaconda.org/package/conda-forge/<pkg> \
  | python -c "
import sys, json, re
from collections import defaultdict
d = json.load(sys.stdin)
latest = sorted({f['version'] for f in d['files']})[-1]
m = defaultdict(set)
for f in d['files']:
    if f['version'] != latest: continue
    bn = f['basename']
    tag = re.search(r'-py(\d{2,3})', bn)
    if tag:
        t = tag.group(1)
        m['3.' + t[:-1] if len(t) == 3 else '3.' + t].add(bn.split('/')[0])
for k in sorted(m): print(k, sorted(m[k]))
"

# 验证某包在当前环境是否有可用 wheel（不实际安装）
pip download --no-deps -d /tmp/probe <pkg>==<version>

# 检查 wheel 的 ABI 标签与是否含二进制扩展
python -c "
import zipfile
z = zipfile.ZipFile('<wheel 路径>')
print(z.read([n for n in z.namelist() if n.endswith('WHEEL')][0]).decode())
print([n for n in z.namelist() if n.endswith(('.pyd', '.so'))])
"
```

## 6. 教训记录

本次核查暴露两类误判，均已在项目记忆与 `dependency-notes.md` 中留痕：

1. **wheel 标签 ≠ Python 版本支持范围**。`cp39-abi3` 表示稳定 ABI，
   适用于 3.9+；只有不带 `abi3` 的 `cp312` 才真正绑定单一版本。
2. **conda API 的 `latest_version` 字段可能指向历史归档**，不可直接采信，
   须从 `files[*].basename` 与 `upload_time` 交叉验证。
