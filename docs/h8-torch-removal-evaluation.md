# H8 评估：云端模式是否应彻底移除 torch

本文评估 `requirements_cloud.txt` 是否应移除 `sentence-transformers`
以彻底避免安装 `torch` 及其传递依赖。结论基于 PyPI 官方元数据实测。

- 评估日期：2026-10-03
- 触发原因：H7 发现 `sentence-transformers` 硬依赖 `torch>=2.2`，
  导致"云端精简版"仍会安装约 200MB 的 torch Windows wheel
- 数据来源：PyPI 官方 JSON API 依赖声明

## 1. 结论摘要

**建议：不移除，且无需任何额外配置。**

实测发现 PyPI 默认提供的 `torch` Windows wheel 就是 **CPU 构建**
（`2.14.1+cpu`，536 MB，`cuda.is_available()` 为 False）。
因此"缩小云端安装体积"这一诉求，**默认安装即已满足**。

在此前提下，移除 `sentence-transformers` 只会带来净损失：
省下 536 MB，却失去本地 embedding 能力，而知识库治理文档要求索引
必须绑定固定的 embedding 模型标识。为体积而牺牲 RAG 核心能力与
治理可追溯性，代价不合理。

## 2. 依赖链实测

### sentence-transformers 6.1.0 的 torch 是无条件硬依赖

```python
transformers<6.0.0,>=5.0.0
tokenizers>=0.19
huggingface-hub<2.0.0,>=1.3.0
torch>=2.2          # ← 不在任何 extra 分组中
numpy>=1.24.0
scikit-learn>=1.1.0
scipy>=1.0.0
typing_extensions>=4.10.0
tqdm>=4.0.0
```

`torch>=2.2` 出现在基础依赖中，**不存在** `sentence-transformers[cpu]` 之类的
可跳过 torch 的安装选项。`optimum-onnx` 虽在 extras 中，但属另一条实现路径（见 §3.2）。

### 连带成本

实测 `requirements_cloud.txt` 解析出 118 个包，其中 torch 相关部分包括：

| 包 | 说明 |
| --- | --- |
| `torch` | Windows wheel 约 200MB |
| `sympy`、`networkx`、`jinja2`、`filelock`、`fsspec` | torch 基础依赖 |
| `scipy`、`scikit-learn`、`joblib`、`threadpoolctl` | sentence-transformers 依赖 |
| `tokenizers`、`huggingface-hub` | transformers 生态 |

## 3. 移除 sentence-transformers 的影响

### 3.1 失去本地 embedding 能力

`sentence-transformers` 是当前架构中唯一的本地 embedding 实现。
`docs/architecture.md` §2 明确将 ChromaDB + sentence-transformers 列为
"知识检索"层的组件；移除后该层需重新设计。

### 3.2 ONNX 替代路径存在明确冲突

`sentence-transformers` 提供 `optimum-onnx[onnxruntime]` extra，
理论上可用 ONNX Runtime 替代 PyTorch 后端。**但实测发现版本冲突**：

| 包 | 版本约束 |
| --- | --- |
| `sentence-transformers` 6.1.0 | `transformers<6.0.0,>=5.0.0` |
| `optimum-onnx` 0.1.0 | `transformers<4.58.0,>=4.36` |

两者对 `transformers` 的要求**互斥**。走 ONNX 路线必须把 transformers
降到 4.x，意味着放弃 transformers 5.x 的全部特性——这是明确的降级代价。

### 3.3 ChromaDB 本身不提供 embedding

实测 `chromadb` 1.5.9 的基础依赖**不含 torch，也不含任何 embedding 模型**：

```
onnxruntime>=1.14.1
tokenizers>=0.13.2
numpy>=1.22.5
grpcio>=1.58.0
...（其余为 API/存储/遥测相关）
```

ChromaDB 是纯存储与检索引擎，embedding 需由外部提供。这意味着：

- 若移除 `sentence-transformers`，仍可**通过远程 embedding 服务**（如华为云
  向量服务）向量化，ChromaDB 只负责存储与召回；
- 但这会引入**对外部服务的强依赖**，与 `knowledge-base.md` §5 要求的
  "使用固定 embedding 模型并保留来源元数据" 需要额外治理设计；
- 且断网演示场景（`product-scope.md` 部署目标）下不可用。

## 4. 推荐方案：使用 CPU 版 torch（实测已默认生效）

**关键实测发现：PyPI 上的 `torch` Windows/Linux wheel 默认就是 CPU 构建，
无需额外配置 index-url。**

在 Python 3.12.14 venv 中实测安装结果：

```
torch.__version__   = 2.14.1+cpu
torch.cuda.is_available() = False
torch 目录体积      = 536 MB
venv 总体积         = 1.1 GB
```

版本号中的 **`+cpu` 后缀**表明这是 CPU 构建，且 `cuda.is_available()` 为 False。

因此 §1 的结论需修正：**"改用 CPU 版 torch"不需要任何额外动作**，
按 `requirements_cloud.txt` 默认安装即为 CPU 版。这比原先设想的方案更简单。

若某环境确需 CUDA 版（如 G3 目标机本地推理），则需按 PyTorch 官方说明
显式指定索引，此时 CPU 版的体积优势不再适用：

```bash
pip install torch --index-url https://download.pytorch.org/whl/cu128
```

| 场景 | torch 变体 | 体积 | 说明 |
| --- | --- | --- | --- |
| 云端 API 模式（本项目默认） | `+cpu` | 536 MB | 默认即是，无需配置 |
| 本地离线 GPU 推理（G3） | CUDA 版 | 显著更大 | 须在目标机实测后确定 |

## 5. 方案对比

| 方案 | torch | embedding 能力 | 文档影响 | 评价 |
| --- | --- | --- | --- | --- |
| 现状 | 装（默认即 CPU 版） | 完整 | 无 | **推荐，保持不变** |
| 显式指定 CPU index-url | 装（CPU） | 完整 | 需记录 index-url | 多余，默认已是 CPU |
| 移除 sentence-transformers | 不装 | 失去本地 embedding | 需重写 RAG 层设计 | 不推荐 |
| 改用远程 embedding 服务 | 不装 | 依赖外部服务 | 需重新设计治理 | 断网场景不可用 |
| 降级 transformers 到 4.x | 装 | 完整（ONNX） | 需评估 5.x 特性损失 | 不推荐 |

**结论：维持现状即可。** 默认安装已是 CPU 版，536 MB 在可接受范围内，
而移除 embedding 能力会破坏知识库治理设计（`knowledge-base.md` §3/§7
要求索引绑定固定 embedding 模型标识）。

## 6. 待决策项

| 编号 | 事项 | 负责角色 | 状态 |
| --- | --- | --- | --- |
| H8 | 云端模式是否移除 torch | 项目负责人 | **建议不移除**；实测默认即 CPU 版（`2.14.1+cpu`，536MB） |
| H9 | 是否需显式指定 CPU index-url | infra | **不需要**，PyPI 默认已提供 CPU 构建 |
| H10 | 若走远程 embedding，治理与断网方案如何设计 | 项目负责人 | 备选，暂不推进 |

## 7. 复核命令

```bash
# 确认 torch 是否为无条件依赖
curl -s https://pypi.org/pypi/sentence-transformers/json | python -c "
import sys, json
d = json.load(sys.stdin)
for r in (d['info'].get('requires_dist') or []):
    if 'torch' in r and 'extra ==' not in r:
        print('硬依赖:', r)
"

# 确认 optimum-onnx 与 sentence-transformers 的 transformers 约束是否冲突
curl -s https://pypi.org/pypi/optimum-onnx/json | python -c "
import sys, json
d = json.load(sys.stdin)
for r in (d['info'].get('requires_dist') or []):
    if 'transformers' in r: print('optimum-onnx:', r)
"

# 确认 chromadb 是否依赖 torch
curl -s https://pypi.org/pypi/chromadb/json | python -c "
import sys, json
d = json.load(sys.stdin)
rt = [r for r in (d['info'].get('requires_dist') or []) if 'torch' in r and 'extra ==' not in r]
print('chromadb 基础依赖含 torch:', bool(rt))
"
```
