"""习题练习 API。

路由清单（新增）：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET  | ``/api/v1/quiz/questions`` | 抽题/列题（按题型/知识点筛选） |
| POST | ``/api/v1/quiz/submit``    | 提交答案，确定性判分 |
| POST | ``/api/v1/quiz/explain``   | 错题讲解（LLM + RAG 补救，可降级） |

设计（对应方案 C）：

- 判分是**确定性**的，与模型无关（:mod:`app.quiz.grade`）；
- 讲解是**可降级**的，LLM 不可用时退化为题库解析 + 教材片段；
- 题库以文件形式存在，经教师审核（``approved``）后方可进入正式练习。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.deps import ServiceRegistry, new_request_id
from app.quiz.explain import explain_wrong
from app.quiz.grade import grade_one
from app.quiz.models import QuestionType
from app.quiz.store import QuizBank

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_registry(request: Request) -> ServiceRegistry:
    """从应用状态取服务容器（与 ``app.api.routes`` 同款，避免循环导入）。"""
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        registry = ServiceRegistry()
        request.app.state.registry = registry
    return registry


class QuizAnswerItem(BaseModel):
    id: str
    submitted: str


class SubmitQuizRequest(BaseModel):
    answers: list[QuizAnswerItem]


class ExplainRequest(BaseModel):
    id: str
    submitted: str


@router.get("/api/v1/quiz/questions", tags=["习题"])
def list_questions(
    topic: str | None = None,
    type: str | None = None,
    limit: int = 50,
    registry: ServiceRegistry = Depends(_get_registry),
) -> dict[str, Any]:
    """列题/抽题。未指定筛选时返回全量（含待校准），便于开发与校准。"""
    bank = QuizBank.load()
    type_enum: QuestionType | None = None
    if type:
        try:
            type_enum = QuestionType(type)
        except ValueError:
            type_enum = None  # 非法题型忽略筛选，不阻断
    questions = bank.filter(topic=topic, type=type_enum, limit=limit)
    return {
        "total": len(questions),
        "index_version": bank.index_version,
        "questions": [q.model_dump() for q in questions],
    }


@router.post("/api/v1/quiz/submit", tags=["习题"])
def submit_quiz(
    payload: SubmitQuizRequest,
    registry: ServiceRegistry = Depends(_get_registry),
) -> dict[str, Any]:
    """提交答案并判分。判分确定性，不依赖模型。"""
    bank = QuizBank.load()
    by_id = {q.id: q for q in bank.all()}
    results: list[dict[str, Any]] = []
    for item in payload.answers:
        q = by_id.get(item.id)
        if q is None:
            results.append({"id": item.id, "found": False, "ok": False})
            continue
        correct = grade_one(q, item.submitted)
        results.append(
            {
                "id": q.id,
                "found": True,
                "ok": correct,
                "type": q.type.value,
                "answer": q.answer,
                "options": q.options,
                "explanation": q.explanation,
            }
        )
    correct_count = sum(1 for r in results if r.get("ok"))
    return {"total": len(results), "correct": correct_count, "results": results}


@router.post("/api/v1/quiz/explain", tags=["习题"], response_model=None)
def explain_quiz(
    payload: ExplainRequest,
    registry: ServiceRegistry = Depends(_get_registry),
) -> dict[str, Any] | JSONResponse:
    """错题讲解。LLM 不可用时自动降级为题库解析 + 教材片段。"""
    bank = QuizBank.load()
    q = bank.by_id(payload.id)
    if q is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "quiz_question_not_found",
                    "message": "题目不存在。",
                    "retryable": False,
                    "request_id": new_request_id(),
                }
            },
        )
    return explain_wrong(registry, q, payload.submitted)


__all__ = ["router"]
