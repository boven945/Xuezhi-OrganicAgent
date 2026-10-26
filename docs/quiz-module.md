# 习题练习模块（feat/quiz-practice）

## 1. 目标与定位

为**学生端**提供"自测 → 判分 → 错题讲解/教材补救 → 错题本"的闭环，补上
"智能诊断"系统中缺失的**练习巩固**半环（Practice–Diagnosis–Remediation）。

- **不替代问答**：问答是"有疑问来问"，练习是"系统巩固"。两者共用同一套
  教材语料与 RAG 检索，但练习的判分是确定的、不依赖模型。
- **可信优先**：题目答案由教师审核的**权威题库**给出，模型**不参与判分**，
  只参与"讲解"与"生成候选题"——这是方案 C 的核心约束。

## 2. 方案 C（混合）：权威题库 + 模型仅作增强

| 能力 | 由谁负责 | 是否可降级 |
| --- | --- | --- |
| 出题 | LLM 从语料抽取草稿（`scripts/extract_quiz_candidates.py`），**人工校准**后入库 | 可：人工直接编写 |
| 判分 | **确定性比对**（`app/quiz/grade.py`），不调模型 | 不可降（这是主交付） |
| 讲解 | LLM 生成教师口吻讲解（`app/quiz/explain.py`） | 可：退化为题库解析 + 教材片段 |
| 补救 | RAG 检索对应知识点教材片段 | 可：检索失败则只返回题库解析 |

> **为什么不让模型判分**：模型判分会引入不确定性（同一题两次可能不同结论），
> 且学生拿到的对错信号必须权威、可复现。模型只做"解释者"，不做"裁判"。

## 3. 后端结构（`backend/app/quiz/`）

| 文件 | 职责 |
| --- | --- |
| `models.py` | `QuizQuestion` / `QuizBankFile`：schema 校验。**只校验结构**（题型相关约束），内容正确性由教研审核把关 |
| `store.py` | `QuizBank`：从 JSON 加载到内存（规模小，无需数据库）；按知识点/题型筛选；文件缺失则降级为空题库 |
| `grade.py` | `grade_one` / `normalize`：**确定性判分**。选择认字母或选项文本；判断认 `true`/`false`；填空支持 `\|` 分隔多答案 |
| `explain.py` | `explain_wrong`：题库解析 + RAG 教材片段 +（可选）LLM 讲解；任一失败都降级，不阻断主交付 |
| `routes.py` | 三个端点（见下）；复用 `app/api/deps.ServiceRegistry` 拿 RAG 存储 |

### API 端点

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/v1/quiz/questions?topic=&type=&limit=` | 列题/抽题 |
| POST | `/api/v1/quiz/submit` | 提交答案，确定性判分 |
| POST | `/api/v1/quiz/explain` | 错题讲解（LLM + RAG，可降级） |

## 4. 数据 schema（`quiz-bank/v1`）

与 `knowledge-corpus/v1` **同级治理**（见 `data/quizzes/candidates.json`）：

- `status`：`draft`（待校准）/ `approved`（已审核，可上线）。
- 答案格式约定：选择=选项字母；填空=标准答案（`\|` 分隔多个可接受答案）；
  判断=`true`/`false`（大小写不敏感，入库时归一化）。
- `related_chunk_ids`：关联教材 chunk，用于答错时 RAG 补救检索。

> LLM 抽取的草稿**恒为 `draft`**，经教师核对答案与解析后改为 `approved`
> 方可进入正式练习（与决策登记表 D3 同源要求：生产内容须教研组审核）。

## 5. 前端（`frontend/`）

- `components/QuizPractice.vue`：练习主视图。按题型渲染（单选/判断/填空），
  提交后展示对错、标准答案、解析；答错题可"查看讲解"。
- `stores/index.ts` 的 `useQuizStore`：练习状态机 + **错题本**。
- **错题本仅存 `localStorage`（不跨设备）**，且携带题目快照，
  故"重练错题"离线可用。键：`xuezhi_quiz_wrongbook_v1`。

## 6. 语料抽取脚本（`scripts/extract_quiz_candidates.py`）

`python -m scripts.extract_quiz_candidates`：从 `data/knowledge/` 语料按 chunk
调用 LLM 生成候选 `draft` 题，经 `QuizQuestion` 校验后写入草稿 JSON 供人工校准。
缺 LLM 配置时给出清晰提示而非堆栈。

## 7. 测试

`backend/tests/quiz/test_quiz.py`：**纯逻辑回归**，覆盖 schema 校验、逐题判分、
`normalize` 边界、非法题目拒绝。不依赖 RDKit/向量库，可在任意环境运行。

> HTTP 端点与 RAG/LLM 降级的端到端验证需在**容器内**完整栈下进行
> （见 `backend/tests/Dockerfile.test`），当前尚未在本地跑（Docker 未启动）。

## 8. 已知缺口 / 待办

- [ ] 容器内跑通 `/health` + 三个 quiz 端点的端到端实测（需 Docker）。
- [ ] 前端练习页与真实后端的 E2E（浏览器层）验证。
- [ ] 题库随语料扩充：用抽取脚本批量产出 `draft`，教研组校准后并入 `candidates.json`。
