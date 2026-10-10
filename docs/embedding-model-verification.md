# 中文嵌入模型验证报告

本文记录 `BAAI/bge-small-zh` 的选型依据、实测规格与**中文检索质量验证结果**，
用于解除决策登记表 H17 阻塞项。

- 分支：`feat/rag-bge-chinese-embedding`
- 关联：`rag-verification.md`（内置 EF 的中文限制）、`dependency-notes.md` §4.1（规格）
- 验证日期：2026-10-04

## 1. 为什么换掉 chromadb 内置 EF

`docs/rag-verification.md` §2.1 已记录：chromadb 内置
`DefaultEmbeddingFunction` 使用 `all-MiniLM-L6-v2`，**是英文模型**，
中文查询排序完全颠倒（目标片段距离 0.8358，无关片段 0.4943）。

因此必须显式指定中文模型。

## 2. 选型与理由

选定 **`BAAI/bge-small-zh`**（用户决策，2026-10-04）：

| 理由 | 说明 |
| --- | --- |
| 体积小、推理快 | 110M 参数，适合现场离线演示 |
| 中文语义强 | 国内通用 embedding，学科术语与教材文本表现稳定 |
| 原生支持 | sentence-transformers 直接加载，无需自定义代码 |
| 已归一化 | 含 `2_Normalize` 模块，配合 cosine 空间等价于点积 |

## 3. 实测规格（取自官方元数据与配置，非记忆）

| 项 | 值 | 来源 |
| --- | --- | --- |
| 架构 | `BertModel` | `config.json` |
| **向量维度** | **512** | `config.json` 的 `hidden_size` |
| **最大序列长度** | **512** | `sentence_bert_config.json` |
| Pooling | `cls_token` | `1_Pooling/config.json` |
| 模块链 | Transformer → Pooling → **Normalize** | `modules.json` |
| 权重格式 | 仅 `pytorch_model.bin`（**无 safetensors**） | 仓库文件清单 |
| 仓库最后更新 | 2023-10-12 | HuggingFace API |
| 实际加载权重数 | 71 tensors | 实测加载日志 |

向量已验证为**单位向量**（模长 `1.000000`），维度实测 **512**，与配置一致。

## 4. 中文检索质量实测（核心验证）

测试语料（高中有机化学，覆盖发现问题时用的对照样本）：

1. 酯化反应是羧酸与醇在酸性条件下反应生成酯和水的化学反应。
2. 乙醇的分子式是C2H6O，结构简式为CH3CH2OH。
3. 今天天气很好，适合出门散步。
4. 苯环是最简单的芳香烃，六个碳原子形成平面正六边形结构。
5. 水解反应是盐与水作用生成酸和碱的反应。

### 4.1 三个查询的排序结果

**查询「酯化反应」** — 首位正确

| 排名 | cos | 文档 |
| --- | --- | --- |
| **1** | **0.9012** | **酯化反应是羧酸与醇…** ✓ |
| 2 | 0.7862 | 水解反应是盐与水作用… |
| 3 | 0.7612 | 乙醇的分子式是C2H6O… |
| 4 | 0.7106 | 苯环是最简单的芳香烃… |
| 5 | 0.6476 | 今天天气很好… |

**查询「苯的结构」** — 首位正确

| 排名 | cos | 文档 |
| --- | --- | --- |
| **1** | **0.8249** | **苯环是最简单的芳香烃…** ✓ |
| 2 | 0.7624 | 乙醇的分子式是C2H6O… |
| 3 | 0.7531 | 水解反应… |
| 4 | 0.7280 | 酯化反应… |
| 5 | 0.6636 | 今天天气很好… |

**查询「乙醇的分子式」** — 首位正确

| 排名 | cos | 文档 |
| --- | --- | --- |
| **1** | **0.9344** | **乙醇的分子式是C2H6O…** ✓ |
| 2 | 0.7811 | 酯化反应… |
| 3 | 0.7472 | 苯环… |
| 4 | 0.7428 | 水解反应… |
| 5 | 0.6226 | 今天天气很好… |

### 4.2 与内置 EF 的对比

| 查询 | 内置 EF（英文模型） | **bge-small-zh** |
| --- | --- | --- |
| 酯化反应 | 目标片段排**第 3** ❌ | 目标片段排**第 1** ✅ |
| 苯的结构 | — | 第 1 ✅ |
| 乙醇的分子式 | — | 第 1 ✅ |
| 无关文档排名 | 第 1 ❌ | **稳定第 5** ✅ |

**结论：中文排序问题已解决。** 三个查询首位全部正确，无关文档稳定排末位。
top1 相似度 0.82–0.93，区分度充足。

### 4.3 其他验证项

- **确定性**：同文本两次嵌入完全一致（索引可复现要求）。
- **归一化**：模长 `1.000000`，确认 `2_Normalize` 生效。
- **前缀兼容**：加 BGE 官方查询前缀
  `为这个句子生成表示以用于检索相关文章：` 后排序仍正确。
- **返回类型**：确认返回原生 `float`（非 numpy 标量），可 JSON 序列化。

## 5. 测试结果

### 5.1 不依赖模型下载的测试：16/16 通过

规格常量、延迟加载行为、错误处理、本地路径解析。**任何环境都能跑。**

### 5.2 模型质量测试：8/8 通过

```
$ docker run --rm -e XUEZHI_RUN_MODEL_TESTS=1 xuezhi-chem-test \
    python -W error::FutureWarning -m pytest \
    "backend/tests/rag/test_embeddings.py::TestChineseRetrievalQualityRequiresModel"
8 passed in 115.61s
```

以 `-W error::FutureWarning` 运行**无警告**，确认未使用过时 API。

## 6. 落实的约束

| 约束来源 | 实现方式 |
| --- | --- |
| `knowledge-base.md` §3：索引须绑定 embedding 模型标识 | `model_id = "BAAI/bge-small-zh"`，与 `model_name` 一致，写入集合 metadata |
| `knowledge-base.md` §3：不得在未记录模型变化时复用旧向量 | 换模型即换 `model_id`，入库时校验一致性 |
| `security-privacy.md` §5：供应链安全 | `trust_remote_code=False`；实测该模型不需要 remote code |
| `security-privacy.md` §5：记录权重来源 | 仓库仅提供 `pytorch_model.bin`（pickle 格式），须从官方渠道获取 |
| `deployment-operations.md` §5：断网可用 | 提供 `local_files_only` 与 `resolve_local_model_path` 供预下载权重 |
| `architecture.md` §6：区分冷启动 | `warmup()` 供服务启动时预加载，不计入首个请求延迟 |
| `architecture.md` §6：嵌入失败明确报错 | 加载/推理失败转 `EmbeddingUnavailableError`，**不回退其他模型** |
| `architecture.md` §6：不影响整体可用性 | 延迟加载——构造对象不下载权重，断网时 import 不失败 |

## 7. 实测发现的两个部署约束

### 7.1 HuggingFace 直连不通

```
直连: HTTP 000（连接失败）
代理: HTTP 200
```

**断网演示必须预下载权重**（约 400 MB），通过
`XUEZHI_EMBEDDING_PATH` 环境变量或 `cache_folder` 指定本地路径。
权重体积与加载时间须记录，用于评估演示机资源（决策登记表 E4）。

### 7.2 权重为 pickle 格式

该仓库**不提供 safetensors 版本**。虽然 `trust_remote_code=False` 能避免执行
自定义代码，但 pickle 本身的反序列化风险需通过"仅从官方渠道获取"来控制
（`security-privacy.md` §5 的要求）。

## 8. 测试暴露的缺陷（已修复）

| # | 缺陷 | 发现方式 | 修复 |
| --- | --- | --- | --- |
| 1 | `get_sentence_embedding_dimension` 已改名 `get_embedding_dimension` | `-W error::FutureWarning` 下测试失败并给出警告 | 改用新方法名，`getattr` 兼容旧版 |
| 2 | `Dockerfile.test` 注释写了 sentence-transformers 但 `RUN` 段因 `\` 续行漏装 | 镜像内无该包但测试报"依赖缺失" | 补上包名，并在文件头说明改依赖后须重建 |
| 3 | 测试类名不含 `requires_model`，`-k` 筛选选中 0 个 | `24 deselected` | 类名加 `RequiresModel` 后缀使其可被筛选 |

第 1 项是典型的"凭记忆写 API"问题——若不开启 `-W error` 而只看"测试通过"，
这个过时 API 会一直潜伏到未来版本移除时才暴露。

## 9. 尚未实现

| 能力 | 归属模块 | 说明 |
| --- | --- | --- |
| 权重预下载脚本 | `infra` | 断网演示必需，含来源与校验和记录 |
| GPU 推理性能实测 | `infra` | 显存占用与检索延迟未测（决策登记表 B1） |
| 阈值实测 | `knowledge-data` | `query()` 的 `threshold` 仍无默认值，须用标注问答集确定 |
| 索引重建工具 | `knowledge-data` | 换模型时须完整重建（§3） |

## 10. 复核方式

```bash
# 不需模型的测试（任何环境可跑）
docker run --rm xuezhi-chem-test python -m pytest backend/tests/rag/test_embeddings.py

# 模型质量测试（需下载约 400MB 权重）
docker run --rm -e XUEZHI_RUN_MODEL_TESTS=1 xuezhi-chem-test \
    python -W error::FutureWarning -m pytest \
    "backend/tests/rag/test_embeddings.py::TestChineseRetrievalQualityRequiresModel" -v
```

**H17 结论：中文 embedding 阻塞已解除**，选型与检索质量均验证通过。
仍需注意 §7 的两个部署约束。

---

## 11. 权重获取实测（2026-10-10，替代「约 400MB」的说法）

### 体积实测

**91.4 MiB（95,842,633 字节）**，不是原先各处记载的「约 400MB」。
文档已同步更正（`.env`、`.env.example`、`dependency-notes.md`、
`container-runtime-verification.md`、`decision-register.md` H17）。

### 直连 huggingface.com 失败，须用镜像

实测（2026-10-10，本机）：

| 目标 | 直连 | 走 7897 代理 |
| --- | --- | --- |
| `huggingface.co/api/models/...` | — | HTTP 200 |
| `huggingface.co/.../resolve/main/config.json` | — | **HTTP 307**（只到重定向） |
| `cdn-lfs.hf.co`（权重实际所在） | — | **HTTP 403** |
| **`hf-mirror.com`（国内镜像）** | **HTTP 200** | — |

**结论：权重在 `cdn-lfs.hf.co` 上，走代理仍 403，故必须用镜像。**

### 镜像的坑：必须跟随重定向

`hf-mirror.com/.../resolve/main/<file>` 返回的是**纯文本**：

```text
Temporary Redirect. Redirecting to /api/resolve-cache/models/...
```

**这不是 JSON。** `huggingface_hub` 直接把它当 JSON 解析 →
`JSONDecodeError: Expecting value: line 1 column 1`，
报错信息里**没有任何上下文**，看不出是重定向问题。

- `curl` 必须加 `-L`（跟随重定向）才能拿到真文件；
- `huggingface_hub` 走镜像会失败——**故本次是手工 `curl -L` 下载**。

### 可复现的获取步骤

```bash
# 1. 下载配置文件（注意 -L，不可省略）
BASE="https://hf-mirror.com/BAAI/bge-small-zh/resolve/main"
D=data/models/bge-small-zh && mkdir -p $D/1_Pooling
for f in config.json config_sentence_transformers.json modules.json \
         sentence_bert_config.json special_tokens_map.json \
         tokenizer_config.json tokenizer.json vocab.txt; do
  curl -sL -o "$D/$f" "$BASE/$f"
done

# 2. **1_Pooling/config.json 必须单独下**（漏了会报
#    Pooling.__init__() missing 1 required positional argument:
#    'embedding_dimension'）
curl -sL -o "$D/1_Pooling/config.json" "$BASE/1_Pooling/config.json"

# 3. 权重 91.4 MiB
curl -L -o "$D/pytorch_model.bin" "$BASE/pytorch_model.bin"

# 4. 指向本地权重（写入 .env）
#    XUEZHI_EMBEDDING_PATH=data/models/bge-small-zh
```

实测下载速率约 **13 MB/s**（7.2 秒下完 91.4 MiB）。

### 构建索引

```bash
python -m scripts.build_index
```

实测输出：`written: 41 / chunk_count: 41 / topic_count: 21`，
与语料实测一致（41 条、21 个主题）。

> **脚本不读 `.env`**——`XUEZHI_EMBEDDING_PATH` 须**显式传环境变量**：
> ```bash
> XUEZHI_EMBEDDING_PATH=data/models/bge-small-zh python -m scripts.build_index
> ```
> 只有 `scripts/run-local.sh` 会载入 `.env`。

### 两个代码缺陷（本轮修复，见提交 `fix/rag-local-embedding-path`）

**① `XUEZHI_EMBEDDING_PATH` 被当成 `cache_folder` 传**

`cache_folder` 期望的是 **HF 缓存根目录**（其下须有
`models--BAAI--bge-small-zh/snapshots/<sha>/`），
而该变量指的是**权重所在目录**。两者语义不同。

症状：设了路径仍报 `OSError`；不设则 `JSONDecodeError`（走网络遇镜像重定向）。
**两种都加载不出来**，而 `/health` 照报 `rag=已加载`——
这是本项目**第四次**「health 说就绪、实际不可用」。

修法：把路径作为 `model_name` 传（`SentenceTransformer` 接受本地目录），
`local_files_only` 只约束"不许再联网"。
改动两处：`scripts/build_index.py:104` 与 `backend/app/api/deps.py:256`。

**② 排查时我犯的错**

只改了 `build_index.py` 就以为好了，结果后端仍失败——
**日志里 `model=BAAI/bge-small-zh` 直接暴露了它没用新路径**，
而后端走的是 `deps.py` 里另一处构造点。
**两处构造点必须一起改**，只改一处会得到"索引能建但服务用不了"的假象。

### 验证结果（真实浏览器）

| 项 | 修复前 | 修复后 |
| --- | --- | --- |
| 答复开头 | "检索服务暂时不可用" | "根据教材内容…" |
| `tool_invocations` | 1 | 3 |
| `sources` | 0 | 9（含章节定位与 source_id） |
| 界面警告条 | 有 | 无 |
| 健康徽章 | 服务正常（但检索实为不可用） | 服务正常（与实际一致） |
