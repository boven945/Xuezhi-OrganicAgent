"""错题讲解（LLM 讲解 + RAG 教材补救）。

设计要点：LLM 是**可降级能力**——不可用时只返回题库自带解析与关联教材片段，
绝不阻断主交付（与语音同理，见 ``architecture.md`` §6）。

错题补救复用现有 RAG 检索（与教材知识库同索引）：
用题目 ``topic`` 检索对应教材片段，让学生「答错后能立刻看到该看的知识点」。
"""
from __future__ import annotations

import logging
from typing import Any

from .models import QuizQuestion

logger = logging.getLogger(__name__)


def explain_wrong(registry: Any, question: QuizQuestion, submitted: str) -> dict[str, Any]:
    """生成错题讲解。

    Returns:
        含 ``explanation``（题库自带解析）、``remedy_texts``（RAG 教材片段）、
        ``llm_explanation``（LLM 讲解，可能 ``None``）、``llm_available`` 的字典。
    """
    base = question.explanation or ""

    # 1) RAG 补救：用知识点检索教材片段
    remedy_texts: list[str] = []
    try:
        store = registry.get_store()
        result = store.query(question.topic, top_k=2)
        if getattr(result, "has_results", False):
            remedy_texts = [c.text for c in result.chunks]
    except Exception as exc:  # noqa: BLE001 - 补救检索失败不阻断讲解
        logger.warning("错题补救检索失败：%s", type(exc).__name__)

    # 2) LLM 讲解（可选，降级）
    llm_text: str | None = None
    try:
        from app.llm.client import create_client

        client = create_client()
        prompt = (
            "这是一道高中有机化学练习题。\n"
            f"题干：{question.stem}\n"
            f"学生的答案：{submitted}\n"
            f"标准答案：{question.answer}\n"
            "请用高中化学教师的口吻，简明讲解为什么标准答案正确，"
            "并指出学生这一题可能陷入的误区。不要超出高中范围。"
        )
        llm_text = client.complete(prompt)
    except Exception as exc:  # noqa: BLE001 - LLM 不可用即降级
        logger.info("LLM 讲解不可用，降级：%s", type(exc).__name__)

    return {
        "explanation": base,
        "remedy_texts": remedy_texts,
        "llm_explanation": llm_text,
        "llm_available": llm_text is not None,
    }


__all__ = ["explain_wrong"]
