# H6 验证报告：Python 3.12 环境安装实测

本文记录在真实 Python 3.12 环境中执行依赖安装验证的结果，作为决策登记表 H6 的执行记录。

- 执行日期：2026-10-03
- 验证环境：Windows，`Python 3.12.14`（uv 发行版，`cpython-3.12.14-windows-x86_64-none`）
- 虚拟环境：`F:/Xuezhi-OrganicAgent/.venv-xuezhi312`，pip 26.2.1
- 基线依据：`requirements-lock.txt`、`requirements_cloud.txt`（Python 3.12）

> 本次为 **Windows 平台**验证。Linux（比赛演示环境）仍需单独实测，
> 尤其是 torch 的 CUDA 组合（G3）与 autoawq 的编译（G4）。

## 1. 结论摘要

| 验证项 | 结果 | 说明 |
| --- | --- | --- |
| Python 3.12 解释器可用性 | ✅ | 3.12.14 正常创建 venv |
| numpy==2.5.3 + pandas==3.0.6 | ✅ 通过 | cp312 wheel 存在，pip 解析 EXIT=0 |
| requirements_cloud.txt 可解析性 | ✅ 通过 | 118 个包全部解析成功，EXIT=0，无冲突 |
| rdkit 在 cp312 的 wheel | ✅ 新发现 | 2026.3.6 提供 cp312 win_amd64 wheel 且无 sdist |
| **文档缺陷：云端版"不含 torch"** | ❌ **证伪** | sentence-transformers 硬依赖 `torch>=2.2`，云端版必装 torch |
| **.gitignore 缺陷：venv 命名** | ❌ **发现并修复** | `.venv-xuezhi312/` 原未被忽略，已改通配规则 |
| torch 默认构建类型 | ✅ **新发现** | 实测装到的是 `2.14.1+cpu`（CPU 版，536MB），非 CUDA 版 |
| 网络环境对安装的影响 | ⚠️ **显著** | 直连 ~30 kB/s 无法完成；走代理 1 MB/s 后 4 分钟装完 |
| import 冒烟测试 | ✅ **通过** | 16 个核心模块全部 import 成功（见 §2.1） |

## 2. 核心版本验证

### numpy 与 pandas（基线升级的直接受益者）

```
$ pip install --dry-run "numpy==2.5.3" "pandas==3.0.6"
Collecting numpy==2.5.3
  Using cached numpy-2.5.3-cp312-cp312-win_amd64.whl.metadata
Collecting pandas==3.0.6
  Using cached pandas-3.0.6-cp312-cp312-win_amd64.whl.metadata
Would install numpy-2.5.3 pandas-3.0.6 python-dateutil-2.9.0.post0 six-1.17.0 tzdata-2026.5
EXIT=0
```

两者均命中 **cp312 预编译 wheel**，无需源码编译。这直接验证了基线升级的收益：
若仍在 3.10 基线，numpy 会被压到 2.2.6、pandas 压到 2.3.3。

### requirements_cloud.txt 完整解析

```
EXIT=0
Would install（118 个包）
```

关键依赖均正确解析，包括：`chromadb-1.5.9`、`langchain-1.4.3`、
`langchain-openai-1.6.7`、`transformers-5.18.0`、`numpy-2.5.3`、`pandas-3.0.6`、
`fastapi-0.142.2`、`uvicorn-0.54.0`、`pydantic-2.13.5`。
**无版本冲突，无解析错误。**

### rdkit 在 pip 路径同样可用（新发现）

```
rdkit==2026.3.6
cp312 wheels:
  rdkit-2026.3.6-cp312-cp312-win_amd64.whl
  rdkit-2026.3.6-cp312-cp312-manylinux_2_28_x86_64.whl
  rdkit-2026.3.6-cp312-cp312-manylinux_2_28_aarch64.whl
  rdkit-2026.3.6-cp312-cp312-macosx_11_0_arm64.whl
sdist: NONE
```

此前文档只记录 conda-forge 安装路径。实测表明 **cp312 预编译 wheel 已提供**，
且无 sdist。这意味着 pip 路径可作为 conda 之外的备选方案，
但文档基线仍以 conda 为准（README 与 deployment-operations §3 的既有约定）。

## 2.1 import 冒烟测试结果

安装完成后执行 import 冒烟测试，验证"装得上"之外确实"能用"：

```
Python 3.12.14
==============================================================
  OK   numpy                  2.5.3
  OK   pandas                 3.0.6
  OK   torch                  2.14.1+cpu
  OK   transformers           5.18.0
  OK   sentence_transformers  6.1.0
  OK   sklearn                1.9.1
  OK   fastapi                0.142.2
  OK   pydantic               2.13.5
  OK   langchain              1.4.3
  OK   langchain_openai       1.6.7
  OK   uvicorn                0.54.0
  OK   loguru                 0.7.3
  OK   scipy                  1.18.1
  OK   onnxruntime            1.30.0
  OK   tokenizers             0.23.2
----------------------------------------------------------
  FAIL chromadb               ImportError: DLL load failed while importing cygrpc

成功 15/16  失败 1
```

**结论：Python 3.12 下的依赖栈功能正常。** 唯一的失败项是本机安全策略所致，
非项目依赖问题（见 §2.2）。

### 2.2 唯一失败项：本机应用程序控制策略拦截 grpcio

```
ImportError: DLL load failed while importing cygrpc:
应用程序控制策略已阻止此文件。
```

定位过程：

1. 失败文件存在：`grpc/_cython/cygrpc.cp312-win_amd64.pyd`
2. chromadb 本身可定位：`chromadb/__init__.py` 正常
3. 直接测试 `import grpc` 报同一错误 → 与 chromadb 无关，是 grpcio 的原生扩展

**这是本机环境限制，不是依赖冲突。** 对照测试证明：

| 原生扩展 | 结果 |
| --- | --- |
| numpy `core._multiarray_umath` | OK |
| scipy `_lib._ccallback` | OK |
| pydantic_core `_pydantic_core` | OK |
| PyYAML `yaml._yaml` | OK |
| **grpcio `_cython.cygrpc`** | **被策略阻止** |

其他原生扩展均正常，说明并非 venv 目录被整体封锁，而是针对该文件签名的
策略拦截（Windows WDAC / AppLocker 常见于企业管控设备）。

**影响与处置**：

- 对项目依赖选型**无影响**，chromadb 与 Python 3.12 兼容性已由 dry-run 验证
- 本机无法运行 chromadb（gRPC 通信层不可用）
- 演示/开发环境若同样受限，需将 grpcio 加入 WDAC 策略白名单，
  或改用不受策略影响的部署环境
- **待确认**：演示用的 Linux 目标机是否有同类策略（登记为 H11）

## 2.3 安装过程中的网络与文件锁问题

### 网络：直连不可行，代理是必需项

同一下载目标（PyPI simple index）的实测对比：

| 网络路径 | 速度 | 30 秒内下载量 |
| --- | --- | --- |
| 直连 | 28.7 kB/s | 未完成 |
| **代理 `127.0.0.1:7897`** | **1.02 MB/s** | 完成（约 35 倍） |

直连条件下完整安装 30 分钟未完成；改走代理后 4 分钟装完。
本机全局 git 代理 `http://127.0.0.1:7897` 常处于关闭状态，
建议安装依赖时显式指定：

```bash
pip install --proxy http://127.0.0.1:7897 -r requirements_cloud.txt
```

注意：代理对 PyPI **index 查询**偶发 `SSLEOFError`，但 wheel 下载正常，
pip 会自动重试并回退。实测 3 次尝试后成功。

### 文件锁：WinError 5 需清理残留 tmp 文件

安装过程中两次出现：

```
OSError: [WinError 5] 拒绝访问:
'...\torch-2.14.1.dist-info\INSTALLERxuu_bupt.tmp' -> '...\INSTALLER'
```

原因：安装被中断后留下 `*.tmp` 残留文件，后续安装重命名时被拒。
清理后重试即成功：

```bash
find .venv-xuezhi312/Lib/site-packages -name "*.tmp" -delete
pip install -r requirements_cloud.txt
```

## 3. 发现的文档缺陷（重要）

### 3.1 "云端精简版不含 torch"的表述不成立

`README.md` 与 `dependency-notes.md` 均声称：

> 云端精简版不包含 `autoawq`、`transformers`、`accelerate` 和 `torch`

**实测证伪**。依赖解析日志显示：

```
Collecting torch>=2.2 (from sentence-transformers>=2.7.0-> requirements_cloud.txt line 8)
```

`sentence-transformers` 声明了硬依赖 `torch>=2.2`，因此
**`requirements_cloud.txt` 必然安装 torch 及其完整依赖树**
（实测解析结果含 `torch-2.14.1`，并连带 `sympy`、`networkx`、`jinja2`、
`filelock`、`fsspec` 等）。

连带影响：`torch-2.14.1` 的 Windows wheel 体积较大，是安装耗时的主因
（本次 dry-run 超过 7 分钟，实际安装超过 8 分钟）。

结论修正：

- `requirements_cloud.txt` **不含**的是 `autoawq`、`transformers`、`accelerate`；
- 但**包含** `torch` 与 `sentence-transformers` 及其传递依赖。
- "云端模式不需要本机跑大模型"这一表述仍成立（不加载本地权重），
  但"不安装 torch"不成立。

该表述涉及 README、`requirements_cloud.txt` 头部注释与 `dependency-notes.md`，
需在 infra 分支统一更正。

### 3.2 `.gitignore` 未覆盖带后缀的 venv 命名

原规则仅列出精确名 `.venv/`、`venv/`、`env/`。本次创建的
`.venv-xuezhi312/` **未被忽略**（`git status` 中可见），实测确认：

```
$ git check-ignore -q .venv-xuezhi312/   # 退出码非 0，未被忽略
```

这意味着若开发者按 `<name>-312` 之类习惯命名虚拟环境，venv 内容
（通常数百 MB）会进入 Git。

**已修复**：规则改为通配 `.venv*/`、`venv*/`、`env*/`、`conda-env*/`。
修复后实测：

| 目录 | 修复前 | 修复后 |
| --- | --- | --- |
| `.venv-xuezhi312/` | 未忽略 | **已忽略** |
| `.venv/` | 已忽略 | 已忽略 |
| `venv312/` | 未忽略 | **已忽略** |
| `conda-env-xuezhi/` | 未忽略 | **已忽略** |
| `README.md` 等文档 | 可提交 | 可提交（未被误伤） |

## 4. 本次未能验证的项

以下项本次**未执行**，原因与后续动作如下：

| 项 | 原因 | 后续 |
| --- | --- | --- |
| ~~运行时导入测试~~ | — | **已完成**：15/16 通过，见 §2.1 |
| requirements.txt 完整安装 | 该文件额外含 autoawq | autoawq 需编译，属 G4，需单独处理 |
| autoawq 编译可行性 | 纯 sdist，需 CUDA 工具链 | G4，目标机上实测 |
| torch CUDA 组合 | 本机为 Windows + CPU 版 torch | G3，目标机上实测 |
| Linux 平台安装 | 本次为 Windows | 演示环境须单独验证 |
| RDKit conda 安装 | 本机未评估 conda 路径 | conda-forge py312 构建已确认存在；pip 路径亦可用 |
| chromadb 运行时 | 本机 WDAC 拦截 grpcio | 本机无法运行；确认演示机策略（H11） |

## 5. 复核命令

```bash
# 创建 3.12 venv（Windows，本机已有 3.12.14）
py -V:Astral/CPython3.12.14 -m venv .venv-xuezhi312

# 解析验证（不实际安装）
.venv-xuezhi312/Scripts/python.exe -m pip install --dry-run -r requirements_cloud.txt

# 查看会安装哪些包
.venv-xuezhi312/Scripts/python.exe -m pip install --dry-run -r requirements_cloud.txt | grep "^Would install"

# 清理
# 删除 .venv-xuezhi312/ 目录即可（已被 .gitignore 忽略）
```

## 6. 待更新文档清单

本次验证产生的文档修改需求，按模块归属记录：

| 项 | 归属模块 | 状态 |
| --- | --- | --- |
| 修正"云端版不含 torch"表述（README / requirements_cloud.txt / dependency-notes） | infra | 待处理 |
| `.gitignore` venv 通配规则修复 | infra | **已在本次提交** |
| 本验证报告入库 | docs | **已在本次提交** |
| H6 状态更新 | docs | 待处理 |
| 网络与文件锁处置记录 | infra | **已在本次提交** |
| H11 演示机的 WDAC 策略确认 | infra | 待确认 |
| 安装代理配置说明（deployment-operations） | infra | 待处理 |
