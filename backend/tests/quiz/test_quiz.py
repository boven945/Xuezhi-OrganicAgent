"""习题模块回归测试（feat/quiz-practice）。

## 为什么是纯逻辑测试，不碰 RDKit / 向量库

判分是**确定性**的（:mod:`app.quiz.grade`），与模型、嵌入、化学引擎都无关；
题库 schema 校验是 pydantic（:mod:`app.quiz.models`）；加载是 JSON 解析
（:mod:`app.quiz.store`）。这三层都不依赖 RDKit，故本测试可在**任何环境**
运行——包括本机（Windows 应用控制策略拦截了 RDKit）与 Docker。

> 端到端的 HTTP 层（``/api/v1/quiz/*`` 端点 + 错题讲解的 RAG/LLM 降级）
> 需要完整服务与嵌入模型，留给 ``backend/tests/api/`` 在容器内跑。

运行::

    # 本机（无需 Docker）
    PYTHONPATH=backend .venv-xuezhi312/Scripts/python.exe -m pytest backend/tests/quiz -q
    # 容器内（完整栈）
    docker run --rm xuezhi-chem-test
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.quiz.grade import grade_one, normalize
from app.quiz.models import LETTERS, QuestionType, QuizBankFile, QuizQuestion
from app.quiz.store import QuizBank

REPO_ROOT = Path(__file__).resolve().parents[3]
CANDIDATES = REPO_ROOT / "data" / "quizzes" / "candidates.json"


# ---------------------------------------------------------------------------
# 题库加载与 schema 校验
# ---------------------------------------------------------------------------


def test_candidates_file_loads_and_validates() -> None:
    """candidates.json 必须能通过 schema 校验，且数量符合当前策划集。"""
    bank = QuizBank.load(CANDIDATES)
    qs = bank.all()
    assert len(qs) == 19, f"当前候选题数量应为 19，实为 {len(qs)}"
    by_type = {t: len(bank.filter(type=t)) for t in QuestionType}
    assert by_type[QuestionType.CHOICE] == 7
    assert by_type[QuestionType.FILL] == 5
    assert by_type[QuestionType.TRUE_FALSE] == 7


def test_quizbankfile_schema_field_ok() -> None:
    """顶层 schema 字段形如 quiz-bank/v1（与 knowledge-corpus 同级治理）。"""
    raw = CANDIDATES.read_text(encoding="utf-8")
    bank_file = QuizBankFile.model_validate_json(raw)
    assert bank_file.bank_schema == "quiz-bank/v1"
    assert bank_file.index_version == "quiz-2026.10-dev"


def test_filter_by_topic() -> None:
    assert len(QuizBank.load(CANDIDATES).filter(topic="苯")) == 3
    assert len(QuizBank.load(CANDIDATES).filter(topic="不存在的知识点")) == 0


# ---------------------------------------------------------------------------
# 逐题判分：正确答案判对、错误答案判错
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("q", QuizBank.load(CANDIDATES).all(), ids=lambda q: q.id)
def test_grade_each_question(q: QuizQuestion) -> None:
    if q.type is QuestionType.CHOICE:
        correct = q.answer
        wrong = next(L for L in LETTERS[: len(q.options or [])] if L != q.answer)
    elif q.type is QuestionType.FILL:
        correct = q.answer.split("|")[0]
        wrong = "一定不是标准答案的字符串"
    else:  # truefalse
        correct = q.answer
        wrong = "false" if q.answer == "true" else "true"
    assert grade_one(q, correct) is True
    assert grade_one(q, wrong) is False


def test_choice_accepts_option_text() -> None:
    """选择题提交选项文本也应判分（不只认字母）。"""
    for q in QuizBank.load(CANDIDATES).filter(type=QuestionType.CHOICE):
        opts = q.options or []
        idx = LETTERS.index(q.answer)
        assert grade_one(q, opts[idx]) is True
        assert grade_one(q, opts[1 - idx]) is False


# ---------------------------------------------------------------------------
# normalize 边界
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expect"),
    [
        ("A.", "a"),
        ("C2H5OH", "c2h5oh"),
        ("  酯. ", "酯"),
        ("True", "true"),
        ("aBc 123", "abc123"),
    ],
)
def test_normalize(raw: str, expect: str) -> None:
    assert normalize(raw) == expect


# ---------------------------------------------------------------------------
# 非法题目必须被 pydantic 拒绝（结构校验不替内容把关，但结构必须合法）
# ---------------------------------------------------------------------------


def test_rejects_choice_answer_out_of_range() -> None:
    with pytest.raises(Exception):
        QuizQuestion.model_validate(
            {
                "id": "x1",
                "type": "choice",
                "topic": "t",
                "stem": "s",
                "options": ["a", "b"],
                "answer": "Z",
            }
        )


def test_rejects_empty_fill_answer() -> None:
    with pytest.raises(Exception):
        QuizQuestion.model_validate(
            {"id": "x2", "type": "fill", "topic": "t", "stem": "s", "answer": "   "}
        )


def test_rejects_nonboolean_truefalse() -> None:
    with pytest.raises(Exception):
        QuizQuestion.model_validate(
            {"id": "x3", "type": "truefalse", "topic": "t", "stem": "s", "answer": "yes"}
        )


def test_truefalse_answer_normalized_on_load() -> None:
    """判断题答案在模型层归一化为小写 true/false。"""
    q = QuizQuestion.model_validate(
        {
            "id": "x4",
            "type": "truefalse",
            "topic": "t",
            "stem": "s",
            "answer": "TRUE",
        }
    )
    assert q.answer == "true"
