"""题库加载与查询。

题库是版本化 JSON 文件（默认随 ``./data`` 挂载进容器的
``/work/data/quizzes/candidates.json``），可通过 ``XUEZHI_QUIZ_PATH`` 覆盖。

加载失败（文件缺失/格式错误）由调用方处理——本类在缺失时返回空题库
（练习功能降级），在格式错误时原样抛出由 API 层转错误响应。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .models import QuizBankFile, QuizQuestion, QuestionType

#: 容器内默认路径（宿主 ./data 挂载到 /work/data）。
DEFAULT_QUIZ_PATH = "/work/data/quizzes/candidates.json"


class QuizBank:
    """内存题库。

    当前题库规模很小（数十至数百题），全量加载到内存即可，
    无需数据库；若未来规模增长，再替换为按需检索。
    """

    def __init__(
        self,
        questions: list[QuizQuestion],
        *,
        index_version: str = "unversioned",
        meta: dict[str, Any] | None = None,
    ) -> None:
        self._questions = questions
        self.index_version = index_version
        self._meta = meta or {}

    @classmethod
    def load(cls, path: str | Path | None = None) -> "QuizBank":
        """从 JSON 文件加载题库。"""
        p = Path(path or os.environ.get("XUEZHI_QUIZ_PATH", DEFAULT_QUIZ_PATH))
        if not p.is_file():
            # 文件缺失不致命：练习功能降级为「暂无题目」
            return cls([], index_version="unversioned")
        raw = json.loads(p.read_text(encoding="utf-8"))
        bank = QuizBankFile.model_validate(raw)
        return cls(
            list(bank.questions),
            index_version=bank.index_version,
            meta={"schema": bank.schema, "generated_from": bank.generated_from},
        )

    def all(self) -> list[QuizQuestion]:
        """全部题目（含待校准）。"""
        return list(self._questions)

    def by_id(self, qid: str) -> QuizQuestion | None:
        for q in self._questions:
            if q.id == qid:
                return q
        return None

    def filter(
        self,
        *,
        topic: str | None = None,
        type: QuestionType | None = None,
        limit: int | None = None,
    ) -> list[QuizQuestion]:
        """按知识点 / 题型筛选，可选截断。"""
        qs = self._questions
        if topic:
            qs = [q for q in qs if q.topic == topic]
        if type is not None:
            qs = [q for q in qs if q.type is type]
        if limit is not None and limit > 0:
            qs = qs[:limit]
        return qs


__all__ = ["DEFAULT_QUIZ_PATH", "QuizBank"]
