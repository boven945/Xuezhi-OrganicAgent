"""习题题库数据模型与校验。

题型：选择 ``choice``、填空 ``fill``、判断 ``truefalse``。
题库以版本化 JSON 文件存在（与教材语料同级治理），
``status=approved`` 方可进入正式练习，``draft`` 为待校准候选。

答案表示约定（判分侧见 :mod:`app.quiz.grade`）：

- 选择：``answer`` 为选项字母（``A``/``B``/``C``/``D``…），与 ``options`` 顺序对应；
- 填空：``answer`` 为标准文本，可用 ``|`` 分隔多个可接受答案；
- 判断：``answer`` 为 ``true``/``false``（大小写不敏感）。
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: 选项字母表（最多 6 项，覆盖单选常见规模）。
LETTERS: list[str] = ["A", "B", "C", "D", "E", "F"]


class QuestionType(str, Enum):
    """习题题型。"""

    CHOICE = "choice"
    FILL = "fill"
    TRUE_FALSE = "truefalse"


class QuizQuestion(BaseModel):
    """单道习题。

    仅做**结构**校验（题型相关约束）。
    内容正确性（答案是否真对、解析是否准）属于教研审核，不在此强制。
    """

    id: str
    type: QuestionType
    topic: str
    chapter: str = ""
    stem: str
    #: 选择题选项；填空/判断为 ``None``。
    options: list[str] | None = None
    #: 标准答案（格式见模块 docstring）。
    answer: str
    explanation: str = ""
    difficulty: int = Field(default=1, ge=1, le=3)
    #: 关联教材语料 chunk_id，用于答错时 RAG 补救检索。
    related_chunk_ids: list[str] = Field(default_factory=list)
    #: ``draft``（待校准）或 ``approved``（已审核，可上线）。
    status: str = "draft"
    #: 来源说明（如 ``dev-corpus`` 表示由开发语料抽取）。
    source: str = "dev-corpus"

    @model_validator(mode="after")
    def _validate_type_specific(self) -> "QuizQuestion":
        if self.type is QuestionType.CHOICE:
            if not self.options or len(self.options) < 2:
                raise ValueError(f"选择题 {self.id!r} 至少需要 2 个选项")
            if self.answer not in LETTERS[: len(self.options)]:
                raise ValueError(
                    f"选择题 {self.id!r} 的答案 {self.answer!r} 不在选项字母"
                    f"范围内（应有 {LETTERS[: len(self.options)]}）"
                )
        elif self.type is QuestionType.TRUE_FALSE:
            norm = self.answer.strip().lower()
            if norm not in ("true", "false"):
                raise ValueError(
                    f"判断题 {self.id!r} 的答案须为 true/false，收到 {self.answer!r}"
                )
            # 归一化存储，避免大小写差异影响判分
            self.answer = norm
        else:  # FILL
            if not self.answer or not self.answer.strip():
                raise ValueError(f"填空题 {self.id!r} 的答案不能为空")
        return self


class QuizBankFile(BaseModel):
    """题库文件顶层结构（与 ``knowledge-corpus/v1`` 同级治理）。"""

    # 字段名用 bank_schema，但 JSON 键仍为 ``schema``（与语料治理一致）。
    # 不能直接命名为 ``schema``：它会遮蔽 pydantic BaseModel 的同名属性，
    # 触发 UserWarning 且语义不清。
    model_config = ConfigDict(populate_by_name=True)

    bank_schema: str = Field(default="quiz-bank/v1", alias="schema")
    index_version: str = "unversioned"
    generated_from: str = ""
    questions: list[QuizQuestion] = Field(default_factory=list)


__all__ = ["LETTERS", "QuestionType", "QuizQuestion", "QuizBankFile"]
