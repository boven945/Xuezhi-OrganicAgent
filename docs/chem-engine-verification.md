# 化学引擎实现与验证状态

本文记录 `backend-chem` 模块的实现范围、验证结果与**当前无法验证的部分**。

- 分支：`feat/backend-chem-rdkit-engine`
- 模块标识：`backend-chem`（见 `docs/development-workflow.md` §2）
- 基线：Python 3.12 + RDKit 2026.3.6

## 1. 实现范围

| 文件 | 职责 |
| --- | --- |
| `backend/app/chem/__init__.py` | 模块导出与职责边界说明 |
| `backend/app/chem/errors.py` | 受控错误类型与稳定错误码 |
| `backend/app/chem/models.py` | 数据契约（性质/官能团/结构/结果） |
| `backend/app/chem/engine.py` | RDKit 封装：解析、校验、属性、官能团 |
| `backend/tests/chem/test_engine.py` | 单元测试 |

### 落实的文档约束

| 约束来源 | 实现方式 |
| --- | --- |
| `architecture.md` §5：SMILES 解析成功 ≠ 机理正确 | `ParseResult` 恒带 `notes` 声明"不代表反应机理已被验证"；`Verification` 枚举在类型层面区分工具校验与模型推断 |
| `product-scope.md` §6：不得把"模型生成"表述成"已校验" | 所有输出带 `verification` 标记，默认为 `PARSE_ONLY` |
| `interface-contract.md` §5：错误可分类、不泄露堆栈 | `ChemError` 携带稳定 `code` 与 `user_message`；`detail` 仅入服务端日志 |
| `security-privacy.md` §4：限制 RDKit 输入规模 | `MAX_SMILES_LENGTH=2000`、`MAX_HEAVY_ATOMS=200`，超限在解析前拒绝 |
| `security-privacy.md` §4：禁传可执行代码 | `viz_data` 仅含 SMILES 文本与 JSON，不含 HTML/JS；测试断言其无 `<`、`function` |
| `interface-contract.md` §3：可视化数据须版本化 | `viz_data["schema"] = "molecule-structure/v1"` |

### 错误分类

| 错误码 | 触发条件 | 上层应给出的提示 |
| --- | --- | --- |
| `chem_invalid_structure` | SMILES 语法错误、结构非法、价态不合理 | 检查写法 |
| `chem_structure_too_large` | 输入超长或重原子数超限 | 超出支持范围 |
| `chem_unsupported_structure` | 可解析但规模超限 | 超出支持范围（与"非法"区分） |
| `chem_internal_error` | 兜底 | 稍后重试 |

## 2. 验证状态：✅ 已通过 Docker 容器验证（42/42 通过）

这是本模块当前最重要的状态，**不作为已验证宣称**。

### 2.1 阻塞原因：Windows Smart App Control 拦截未签名二进制

```
ImportError: DLL load failed while importing rdchem:
应用程序控制策略已阻止此文件。
```

事件日志（`Microsoft-Windows-CodeIntegrity/Operational`，事件 ID 3077）给出确切原因：

> Smart App Control Block ... attempted to load
> `rdkit.libs\RDKitSubstructMatch-*.dll` that did not meet the Enterprise
> signing level requirements or violate code integrity policy
> (Policy ID:{0283ac0f-fff1-49ae-ada1-8a933130cad6})

实测确认（`Get-AuthenticodeSignature`）：

| 原生扩展 | 签名状态 | 能否加载 |
| --- | --- | --- |
| `numpy/_core/_multiarray_umath.pyd` | 有 Microsoft 签名 | ✅ |
| `scipy/_lib/_ccallback.pyd` | 有 Microsoft 签名 | ✅ |
| `PIL/_imaging.pyd` | 有 Microsoft 签名 | ✅ |
| `rdkit/Chem/rdchem.pyd` + `rdkit.libs/*.dll` | **NotSigned** | ❌ 被拦 |
| `grpc/_cython/cygrpc.cp312-win_amd64.pyd` | **NotSigned** | ❌ 被拦 |

结论：Smart App Control 按**发布者签名**判定，未签名的第三方 Cython/原生构建
一律拦截。numpy/scipy/Pillow 因由 Microsoft 签名而可用。

### 2.1b 拦截机制的精确边界（2026-10-04 深度实测）

上文「按发布者签名判定」的说法**需要修正为更精确的描述**。
本次逐文件实测发现，判定并非「签名有无」这样二元：

| 样本 | 无数字签名 | 能否加载 |
| --- | --- | --- |
| RDKit 的 80 个 DLL（conda-forge） | 全部 NotSigned | **22 可/ 58 拦** |
| RDKit 的 63 个 .pyd（pip venv） | 全部 NotSigned | **7 可 / 56 拦** |

**同一批无签名文件里部分可用、部分被拦** —— 故判据不是「签名有无」，
而是更细粒度的代码完整性规则（事件日志给出的 Policy ID
`{0283ac0f-fff1-49ae-ada1-8a933130cad6}`）。

进一步实测排除了以下常见归因：

| 假设 | 实测结果 |
| --- | --- |
| PE 头有差异 | **无**。都是 PE32+/x64、都无签名、都有 CFG |
| 与路径有关 | **无**。复制到别处仍被拦 |
| 与 Python 版本有关 | **无**。换 3.13 仍被拦 |
| 与发行来源有关 | **无**。换 conda-forge 仍被拦 |

**致命的一环是依赖链**：`rdchem.pyd` 需要 7 个 RDKit 自带 DLL，
其中 3 个被拦，故无论如何都加载不了：

```
✅ RDKitDataStructs  ✅ RDKitRDBoost  ✅ RDKitRDGeneral  ✅ RDKitRDGeometryLib
🔒 RDKitGraphMol     🔒 RDKitSmilesParse     🔒 RDKitSubstructMatch
```

**排查时的两个陷阱**（本轮各走了一次弯路）：

1. `ctypes.CDLL` 报「找不到模块」而 `import` 报「策略阻止」——
   这是**两条不同的失败路径**。前者是加载器找不到 `python312.dll`
   （次生错误），后者才是真实原因。**先看 `import` 的错误码**。
2. 错误码 **`WinError 4551` = `ERROR_BLOCKED_BY_POLICY`** 明确指向策略，
   不必往「缺依赖 / 缺 VC 运行库 / glibc」方向猜。

**附带发现**：pip 的 rdkit wheel **未附带任何 DLL**
（`rdkit/**/*.dll` 数量为 0），conda-forge 则有完整 80 个——
但**同样被拦**，故两条安装路线在本机无差别。

### 2.2 为何不采取"关闭策略"的处置

技术上可关闭 Smart App Control（`VerifiedAndReputablePolicyState`），
但**本项目不采用**，理由：

1. 该操作削弱系统级代码完整性防护，且需重启、不可立即回退。
2. 属于改动用户系统安全配置，超出项目范围，应由设备所有者决定。
3. 若团队其他成员也用未签名科学计算包，同一问题会反复出现。

### 2.3 后续尝试：三条绕行路径均受阻（2026-10-03 晚）

在确认 venv 方案不可用后，依次尝试了三条路径，全部失败。记录如下以避免重复投入。

#### 尝试 A：conda 独立环境（`xuezhi312`）

为避免影响现有 7 个 conda 环境（cellpose、wsi_segmentation、imds-py310、
backend 等），新建了独立环境。结果：**失败，但失败原因与 WDAC 无关**。

- `conda install -n base rdkit` 报大量 `Permission denied`
  （`anaconda3/pkgs` 下的 libpq、tk、qt-main、libgrpc 等包）。
- 实测 `anaconda3/pkgs` 目录**权限完全正常**（新建目录、写入、读取、删除
  均成功），而报错涉及的包目录甚至**不存在**。
- 真实原因：conda 需替换/删除已有包缓存，而 `MsMpEng`
  （Microsoft Defender，RealTimeProtection=True）在解压期间锁定文件。
- 改用 `--no-deps` 绕过冲突后，**环境与 RDKit 均安装成功（EXIT=0）**。

但运行时仍失败：

```
Error in sitecustomize; set PYTHONVERBOSE for traceback:
ImportError: DLL load failed while importing _ctypes: 应用程序控制策略已阻止此文件。
ImportError: DLL load failed while importing rdBase: 找不到指定的模块。
```

**连 Python 标准库的 `_ctypes` 都被拦截。**

#### 关键发现：能否加载原生扩展取决于 Python 的**发行来源**，而非版本

| Python 来源 | 版本 | `_ctypes` | RDKit | 结论 |
| --- | --- | --- | --- | --- |
| uv 发行（venv） | 3.12.14 | 通过 | 被拦 | 发行版通过，第三方包未签名 |
| conda-forge 环境 | 3.12.14 | **被拦** | 被拦 | **发行版本身即被拦** |
| 系统 Python | 3.14.4 | 通过 | 未测 | 发行版通过 |
| 托管运行时 | 3.13.14 | 未装 numpy | 未测 | — |

**结论：Smart App Control 按二进制签名判定，conda 渠道的 Python 自身二进制
未通过校验，因此 conda 环境内一切原生扩展都不可用。** 换 conda 安装并不能
绕开该限制，反而因发行版被拦而更严格。

> 附带发现：文档基线中 `conda install -c conda-forge rdkit` 的推荐路径，
> 在启用 Smart App Control 的设备上**不适用**。基线建议保持 conda（兼容性更稳），
> 但需在此注明设备策略限制。

#### 尝试 B：Docker 容器

见决策登记表 H14。Docker Desktop 4.93.0 已装（非标准路径
`AppData/Local/Programs/DockerDesktop`），WSL2 Ubuntu v2 与硬件虚拟化均正常，
但后端引擎管道 `dockerDesktopLinuxEngine` 未创建、进程启动即退出，
需在 Docker Desktop UI 中完成 WSL2 后端初始化。

#### 尝试 C：关闭 Smart App Control

**不采用**，理由见 §2.2。

### 2.4 建议的处置方式（待项目负责人选择）

| 方案 | 说明 | 代价 |
| --- | --- | --- |
| A. 在未受管控环境执行 | 演示/开发机、Linux 服务器、CI 容器 | 需一台可用机器 |
| B. 由 IT 将 RDKit 加入 WDAC 白名单 | 合规做法，需管理权限 | 需走审批流程 |
| C. 关闭 Smart App Control | 立即可用 | 削弱防护、需重启，**不推荐** |
| ~~E. 换 conda 安装~~ | 已实测不可行，见 §2.3 尝试 A | 发行版自身被拦 |
| ~~F. Docker 容器化~~ | 需 UI 初始化，见 H14 | 待用户完成初始化 |
| D. 团队约定统一环境 | 在指定机器/CI 上跑全部测试 | 需要基础设施 |

**推荐 A + D**：把测试放到未受管控的环境执行，代码本身不含任何规避
安全策略的处理——**不为绕过本机策略而污染产品代码**。

### 2.5 验证结果：42/42 通过（2026-10-03 晚）

Docker daemon 就绪后，在容器中完成实跑验证：

```
docker build -f backend/tests/Dockerfile.test -t xuezhi-chem-test .
docker run --rm xuezhi-chem-test
```

结果：**42 passed**。

容器内 RDKit 2026.03.6 实测可用：

```
RDKit: 2026.03.6    CCO -> CCO    MolWt: 46.069    Formula: C2H6O
SMARTS ok: True      MATCH hydroxyl: True
```

首次运行曾出现 28 项失败，全部定位并修正如下（**均为我的判断错误，非环境问题**）：

| # | 问题 | 根因 | 处理 |
| --- | --- | --- | --- |
| 1 | `AttributeError: 'Mol' object has no attribute 'GetSmarts'` | 凭印象假设存在该 API；实测 `dir()` 查无此方法 | 改为保留 `_FUNCTIONAL_GROUPS` 表中的原始 SMARTS 文本，编译结果改为三元组 |
| 2 | `"C"` 未抛异常 | 单个 `C` 是**合法**的甲基自由基（实测 `formula=CH4`） | 换成真正非法的用例：`CC(`、`CC)`、`C1CC`（环未闭合）等 |
| 3 | `CC#N` 未识别出碳碳三键 | `CC#N` 是**氰基**（C≡N），不含碳碳三键 | 测试用例改用乙炔 `C#C`；并核实 `[CX2]#[NX1]` 才是氰基模式 |
| 4 | 乙醇 `num_atoms` 期望 9 实际 3 | `GetNumAtoms()` 返回**显式原子数，不含隐式氢** | 修正期望值为 3，并在 `models.py` 的字段注释中写明该语义 |

> 教训：第 1、2、3 项均源于未先验证即下判断。此后新增 RDKit 相关代码时，
> 先用 `dir()` / 实跑确认 API 与行为，再写入实现。

### 2.6 测试环境：Docker 容器（推荐）

因本机 Smart App Control 限制（RDKit 与 grpcio 的 `.pyd` 未签名），
Windows venv 与 conda 环境均无法运行本模块测试。**Docker 容器内的二进制
不受主机应用控制策略管辖，是当前可行的执行环境。**

新增 `backend/tests/Dockerfile.test`：

| 项 | 值 | 依据 |
| --- | --- | --- |
| 基础镜像 | `python:3.12-slim` | 对齐项目基线 3.12 |
| RDKit | `rdkit==2026.3.6` | `docs/dependency-notes.md` 实测版本 |
| pytest | `pytest==9.1.1` | 实测安装版本 |

执行方式：

```bash
docker build -f backend/tests/Dockerfile.test -t xuezhi-chem-test .
docker run --rm xuezhi-chem-test
```

适用范围说明：容器仅用于**执行测试**，不作为部署形态。项目正式部署形态
仍按 `deployment-operations.md` 执行（见决策登记表 A7）。

`backend/tests/chem/test_engine.py` 覆盖：

- 6 个高中常见分子的解析（乙醇/乙酸/苯/丙烯/乙腈/丙氨酸）
- 6 类非法输入的受控报错、空串与 None 输入
- 规模限制（超长输入、超原子数）
- 8 个官能团的识别 + 未命中返回 + 原子索引
- 属性计算正确性（分子式、分子量、环数、杂原子数）
- 确定性（同输入两次结果一致）
- 可视化数据无可执行内容、结果可 JSON 序列化
- 错误码稳定性、错误信息不泄露内部细节

**以上用例均未在本机执行通过（导入阶段即被拦截）。**
需要在未受管控环境跑一次全绿后，才能把本模块标记为已验证。

## 3. 官能团 SMARTS 的验证状态

`engine.py` 中 11 条 SMARTS 模式来自 RDKit 官方文档与化学实践通用写法：

| 官能团 | SMARTS |
| --- | --- |
| 羟基 | `[OX2H]` |
| 醛基 | `[CX3H1](=O)[#6]` |
| 酮羰基 | `[#6][CX3](=O)[#6]` |
| 羧基 | `[CX3](=O)[OX2H1]` |
| 酯基 | `[CX3](=O)[OX2H0][#6]` |
| 醚键 | `[OD2]([#6])[#6]` |
| 氨基 | `[NX3;H2,H1]` |
| 碳碳双键 | `[CX3]=[CX3]` |
| 碳碳三键 | `[CX2]#[CX2]` |
| 苯环 | `c1ccccc1` |
| 卤素原子 | `[F,Cl,Br,I]` |

**状态：已通过 RDKit 2026.03.6 实测编译与匹配验证**（容器内，42/42 通过）。
其中碳碳三键模式 `[CX2]#[CX2]` 经核实不匹配氰基（`CC#N`），需用乙炔 `C#C` 验证。

即便测试已通过，仍需**化学领域审核**确认覆盖是否适合高中课程：

1. 所有模式能成功编译（`Chem.MolFromSmarts` 返回非 `None`）；
2. 各模式的正例命中、负例不命中（`test_engine.py` 已含对照用例）；
3. 高中范围内是否存在误判或漏判（需教师/化学背景审核）。

若发现误判，应由**化学教师或领域审核者**确认后修改，
不可仅凭测试通过就认定覆盖正确（`docs/knowledge-base.md` §6 的要求）。

## 4. 未实现的功能（属后续模块，不在本分支范围）

| 能力 | 归属模块 | 说明 |
| --- | --- | --- |
| 反应规则校验 | `backend-chem`（后续迭代） | 需先确定反应模板数据来源，属决策登记表待定项 |
| 3D 构象生成 | `backend-chem` 或 `frontend-viz` | 当前 `conformer` 显式标注为 `none`，不假装已有坐标（已由测试断言） |
| SMILES → 反应动画参数 | `backend-agent` | 依赖前端可视化 schema 约定 |
| 与知识库检索联动 | `backend-rag` | — |

本分支刻意**不做**反应机理判断，因为 `docs/product-scope.md` §5 明确
"在缺少可靠反应模板/数据时不得声称已完成严格的反应机理证明"。
