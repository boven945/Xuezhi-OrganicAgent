"""习题判分（确定性比对，不依赖模型）。

答案来自题库，判分是字符串归一化后的相等性比较——
与模型无关，结果可复现。这是方案 C 的刻意设计：
**题库保证答案权威性，模型只负责讲解，不负责判分**。
"""
from __future__ import annotations

import re

from .models import LETTERS, QuestionType, QuizQuestion

#: 归一化：保留中英文字母、数字与中文，去除标点与空白。
_NORMALIZE_RE = re.compile(r"[^\w\u4e00-\u9fff]", re.UNICODE)


def normalize(text: str) -> str:
    """归一化答案文本，使 ``A``/``a``/``A. 酯`` 与 ``C2H5OH``/``c2h5oh`` 可比对。"""
    return _NORMALIZE_RE.sub("", (text or "").strip().lower())


def grade_one(question: QuizQuestion, submitted: str) -> bool:
    """判单题对错。

    - 选择：学生可提交字母（``A``）或选项文本，均归一化比对；
    - 判断：归一化后与 ``true``/``false`` 比对（``answer`` 已归一化）；
    - 填空：支持以 ``|`` 分隔多个可接受答案。
    """
    if question.type is QuestionType.CHOICE:
        options = question.options or []
        sub = (submitted or "").strip().upper()
        if sub in LETTERS[: len(options)]:
            return sub == question.answer
        answer_text = (
            options[LETTERS.index(question.answer)]
            if question.answer in LETTERS[: len(options)]
            else ""
        )
        return normalize(sub) == normalize(answer_text)

    if question.type is QuestionType.TRUE_FALSE:
        return normalize(submitted) == question.answer

    # 填空：多候选答案以 | 分隔
    accepted = [normalize(a) for a in (question.answer or "").split("|")]
    return normalize(submitted) in accepted


__all__ = ["grade_one", "normalize"]
