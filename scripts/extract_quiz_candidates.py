"""从教材语料批量抽取习题候选题（LLM 生成 + 人工校准前置）。

用法（**必须用 `python -m`，不能直接执行脚本**）::

    python -m scripts.extract_quiz_candidates
    python -m scripts.extract_quiz_candidates --limit 2
    python -m scripts.extract_quiz_candidates --corpus data/knowledge/dev-corpus.json \
        --out data/quizzes/extracted-draft.json

## 这个脚本解决什么问题

习题练习模块（feat/quiz-practice）的题库是**版本化 JSON 文件**，
经教师审核（``status=approved``）后方可进入正式练习；``draft`` 为待校准候选。

人工逐条出题效率低且易遗漏知识点覆盖。**本脚本用 LLM 从教材语料
（``data/knowledge/``）批量生成候选题**，产出 ``status=draft`` 的题库，
交给教师**校准答案与解析**后再转为 ``approved``。

## 为什么 LLM 只负责"生成"，不负责"判分"

对应方案 C 的核心约束：**题库保证答案权威性，模型只负责讲解与生成，
不负责判分**。故本脚本产出的是"待校准"候选——答案是否正确、
解析是否准确，必须由人把关（与决策登记表 D3 同源要求：生产内容须由
化学教研组审核）。判分是确定性的（见 ``app/quiz/grade.py``），
与模型无关，本题库上线后判分不再依赖任何模型。

## 输出形态

写入一个 ``quiz-bank/v1`` 结构的 JSON：

- ``status`` 恒为 ``draft``（**绝不**直接出 approved）；
- ``related_chunk_ids`` 指向生成来源 chunk，便于教师溯源核对；
- 每条都经 ``QuizQuestion`` 模型校验，结构非法的一律丢弃并告警。

## 与 build_index.py 的分工

``build_index.py`` 灌检索索引（派生产物，需嵌入模型）；
本脚本产出习题候选题（需 LLM，可离线仅做结构校验）。
两者都"显式触发"，不塞进服务启动流程。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 默认语料文件（与 candidates.json 的 generated_from 对齐）。
DEFAULT_CORPUS = REPO_ROOT / "data" / "knowledge" / "dev-corpus.json"

#: 默认输出：开发期草稿题库。
DEFAULT_OUT = REPO_ROOT / "data" / "quizzes" / "extracted-draft.json"

#: 题库 schema 版本（与后端 QuizBankFile 一致）。
SCHEMA = "quiz-bank/v1"
INDEX_VERSION = "quiz-2026.10-draft"

#: 每 chunk 最多生成几道题（控制 LLM 调用量与草稿规模）。
QUESTIONS_PER_CHUNK = 2


def _strip_fences(text: str) -> str:
    """去掉 LLM 可能加的 ```json ... ``` 围栏与首尾空白。"""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", t, re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else t


def build_prompt(text: str, topic: str) -> str:
    """构造抽取提示。

    明确约束答案格式（与 ``app/quiz/models.py`` 的校验一致），
    否则 LLM 容易产出非法结构而被校验丢弃。
    """
    return (
        "你是高中有机化学题库编写助手。下面是一段教材语料，"
        "请据此出 "
        f"{QUESTIONS_PER_CHUNK} 道考查基础概念的选择题/填空题/判断题。\n\n"
        f"知识点：{topic}\n\n"
        f"语料：{text}\n\n"
        "严格要求：\n"
        "1. 只出高中范围内、语料能支撑的题目，不超纲、不杜撰。\n"
        "2. 返回**纯 JSON 数组**，不要多余说明。数组元素形如：\n"
        "   {\n"
        '     "type": "choice",            // 取值 choice / fill / truefalse\n'
        '     "topic": "<知识点>",\n'
        '     "chapter": "<章节，可留空字符串>",\n'
        '     "stem": "<题干>",\n'
        '     "options": ["A", "B", "C", "D"],  // 仅 choice 需要，fill/truefalse 为 null\n'
        '     "answer": "A",                 // choice=选项字母；truefalse=true/false；fill=标准答案（多个用 | 分隔）\n'
        '     "explanation": "<解析>"\n'
        "   }\n"
        "3. choice 的 options 至少 2 项，answer 必须是 options 中的字母；\n"
        "   truefalse 的 answer 只能是 true 或 false；fill 的 answer 不为空。\n"
    )


def generate_for_chunk(client: Any, entry: dict[str, Any]) -> list[dict[str, Any]]:
    """对一个 chunk 调用 LLM 生成候选题，返回解析出的 dict 列表（未校验）。"""
    source = entry.get("source", {}) or {}
    topic = source.get("topic", entry.get("topic", ""))
    text = entry.get("text", "")
    if not text.strip():
        return []
    prompt = build_prompt(text, topic)
    raw = client.complete(prompt)
    cleaned = _strip_fences(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # LLM 偶尔不守约返回非 JSON；记日志但不中断整批。
        print(f"  ! chunk {entry.get('chunk_id')} 的返回不是合法 JSON，已跳过", file=sys.stderr)
        return []
    if isinstance(data, dict):
        data = data.get("questions", [])
    if not isinstance(data, list):
        return []
    return data


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(
        prog="python -m scripts.extract_quiz_candidates",
        description="从教材语料抽取习题候选题（status=draft）",
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="最多处理前 N 个 chunk（0 表示全部）。开发期用于快速验证。",
    )
    args = parser.parse_args(argv)

    backend_dir = REPO_ROOT / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    if not args.corpus.is_file():
        print(f"语料文件不存在：{args.corpus}", file=sys.stderr)
        return 1

    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    entries = corpus.get("entries", [])
    if args.limit > 0:
        entries = entries[: args.limit]
    print(f"语料：{args.corpus}（处理 {len(entries)} 个 chunk）")

    # 惰性导入 LLM 客户端——未配置时给出清晰提示而非堆栈。
    try:
        from app.llm.client import create_client
    except ImportError as exc:
        print(f"无法导入 LLM 客户端：{exc}", file=sys.stderr)
        return 2

    try:
        client = create_client()
    except Exception as exc:  # noqa: BLE001 - 缺密钥是预期配置状态
        print(
            "模型客户端未配置（缺 MAAS_API_KEY？），无法生成候选题。\n"
            "请配置后重试；若要仅校验结构，可手工编写 draft 题库。",
            file=sys.stderr,
        )
        print(f"（{type(exc).__name__}: {exc}）", file=sys.stderr)
        return 2

    # 惰性导入校验模型——只有真要校验时才需要。
    from app.quiz.models import QuizQuestion

    questions: list[dict[str, Any]] = []
    skipped = 0
    for entry in entries:
        chunk_id = entry.get("chunk_id", "")
        print(f"  - 生成 chunk {chunk_id} …")
        candidates = generate_for_chunk(client, entry)
        for c in candidates:
            c.setdefault("related_chunk_ids", [chunk_id])
            c.setdefault("status", "draft")
            c.setdefault("source", "dev-corpus")
            c.setdefault("difficulty", 1)
            try:
                q = QuizQuestion.model_validate(c)
            except Exception as exc:  # noqa: BLE001 - 非法草稿丢弃并告警
                skipped += 1
                print(f"      ! 跳过结构非法的草稿：{type(exc).__name__}", file=sys.stderr)
                continue
            questions.append(q.model_dump())

    if not questions:
        print("未生成任何有效候选题。", file=sys.stderr)
        return 1

    out = {
        "schema": SCHEMA,
        "index_version": INDEX_VERSION,
        "generated_from": str(args.corpus),
        "_notice": (
            "LLM 抽取草稿，未经人工校准。答案与解析须由化学教研组核对后，"
            "将 status 改为 approved 方可进入正式练习。"
        ),
        "questions": questions,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"\n已写入 {len(questions)} 道草稿（跳过 {skipped} 道非法）-> {args.out}\n"
        "请人工校准后，将 status 改为 approved 并入 candidates.json。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
