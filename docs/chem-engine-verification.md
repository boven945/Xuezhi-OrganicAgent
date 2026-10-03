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

## 2. 验证状态：⚠️ 测试已写但**未能在本机实跑**

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

### 2.2 为何不采取"关闭策略"的处置

技术上可关闭 Smart App Control（`VerifiedAndReputablePolicyState`），
但**本项目不采用**，理由：

1. 该操作削弱系统级代码完整性防护，且需重启、不可立即回退。
2. 属于改动用户系统安全配置，超出项目范围，应由设备所有者决定。
3. 若团队其他成员也用未签名科学计算包，同一问题会反复出现。

### 2.3 建议的处置方式（待项目负责人选择）

| 方案 | 说明 | 代价 |
| --- | --- | --- |
| A. 在未受管控环境执行 | 演示/开发机、Linux 服务器、CI 容器 | 需一台可用机器 |
| B. 由 IT 将 RDKit 加入 WDAC 白名单 | 合规做法，需管理权限 | 需走审批流程 |
| C. 关闭 Smart App Control | 立即可用 | 削弱防护、需重启，**不推荐** |
| D. 团队约定统一环境 | 在指定机器/CI 上跑全部测试 | 需要基础设施 |

**推荐 A + D**：把测试放到未受管控的环境执行，代码本身不含任何规避
安全策略的处理——**不为绕过本机策略而污染产品代码**。

### 2.4 当前测试已写但未执行

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

**状态：未实跑验证。** 这些模式**未在本机通过 RDKit 编译与匹配检验**，
因此不作为已验证结论。首次在可运行环境执行测试时，应确认：

1. 所有模式能成功编译（`Chem.MolFromSmarts` 返回非 `None`）；
2. 各模式的正例命中、负例不命中（`test_engine.py` 已含对照用例）；
3. 高中范围内是否存在误判或漏判（需教师/化学背景审核）。

若发现误判，应由**化学教师或领域审核者**确认后修改，
不可仅凭测试通过就认定覆盖正确（`docs/knowledge-base.md` §6 的要求）。

## 4. 未实现的功能（属后续模块，不在本分支范围）

| 能力 | 归属模块 | 说明 |
| --- | --- | --- |
| 反应规则校验 | `backend-chem`（后续迭代） | 需先确定反应模板数据来源，属决策登记表待定项 |
| 3D 构象生成 | `backend-chem` 或 `frontend-viz` | 当前 `conformer` 显式标注为 `none`，不假装已有坐标 |
| SMILES → 反应动画参数 | `backend-agent` | 依赖前端可视化 schema 约定 |
| 与知识库检索联动 | `backend-rag` | — |

本分支刻意**不做**反应机理判断，因为 `docs/product-scope.md` §5 明确
"在缺少可靠反应模板/数据时不得声称已完成严格的反应机理证明"。
