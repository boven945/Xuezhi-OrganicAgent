"""反应机理检索工具：把反应库桥接进 Agent 工具白名单。

设计约束（与 :mod:`app.agent.knowledge_tools` 同源）：

- 工具**只做关键词匹配并返回 ``reaction_id``**；真正的逐帧动画由独立的
  ``POST /api/v1/reaction/mechanism`` 端点负责（该端点需 RDKit 生成坐标）。
- 本工具本身**不依赖 RDKit**：匹配失败返回 ``found=false``，
  前端据返回值退回文本讲解（与 ``search_knowledge`` 的"未找到"同口径）。
- 因此即使 RDKit 被本机策略拦截、化学工具整体降级，本工具仍应注册——
  学生照样拿到 ``reaction_id``，动画端点会优雅降级为 503，
  不阻断文本主交付（`architecture.md` §6）。
- `security-privacy.md` §4：返回的是**已审核的反应 id 与名称**，
  不是脚本或任意内容；前端据此请求动画，不对工具输出做解释执行。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .tools import Tool
from ..reaction.library import ReactionLibrary

logger = logging.getLogger(__name__)

#: 反应库延迟加载（与 reaction 端点同口径，文件缺失则空库）。
_LIB: ReactionLibrary | None = None


def _get_library() -> ReactionLibrary:
    global _LIB
    if _LIB is None:
        _LIB = ReactionLibrary.load()
    return _LIB


def _match(query: str, library: ReactionLibrary) -> dict[str, str] | None:
    """在反应库里挑与 ``query`` 最匹配的一条。

    用**加权打分**而非首条命中：把 query 拆成小写后与每条反应的可匹配词
    （id / 名称 / 主题 / keywords）做子串匹配，命中词的字符长度之和作为分数，
    取最高分者。越长越具体的词权重越高，避免"乙醇"同时命中两条酯/卤代烃时
    被库的排列顺序任意决定。

    Args:
        query: 学生原问题（中文）。
        library: 已加载的反应库。

    Returns:
        ``{"reaction_id": ..., "name": ...}``；无任何命中返回 ``None``。
    """
    q = query.lower()
    best: dict[str, str] | None = None
    best_score = 0
    for r in library.all():
        terms: list[str] = [
            r.id.lower(),
            r.name.lower(),
            r.topic.lower(),
            *(k.lower() for k in r.keywords),
        ]
        score = 0
        for t in terms:
            if t and t in q:
                score += len(t)
        if score > best_score:
            best_score = score
            best = {"reaction_id": r.id, "name": r.name}
    return best


def build_reaction_tools(library: ReactionLibrary | None = None) -> list[Tool]:
    """构造反应机理检索工具。

    Args:
        library: 用于测试注入的内存库；``None`` 时走延迟文件加载。

    Returns:
        含单个 ``find_reaction_mechanism`` 工具的列表。
    """

    def find_reaction_mechanism(query: str) -> str:
        """根据学生问题匹配一个可展示 3D 机理动画的有机反应。"""
        lib = library if library is not None else _get_library()
        hit = _match(query, lib)
        if hit is None:
            return json.dumps({"found": False}, ensure_ascii=False)
        return json.dumps({"found": True, **hit}, ensure_ascii=False)

    return [
        Tool(
            name="find_reaction_mechanism",
            description=(
                "当学生询问某个有机反应的机理（断键、成键、反应过程、"
                "如何发生）时，先用本工具检索是否有可交互的 3D 机理动画。"
                "返回 found=true 时给出 reaction_id，前端会加载断键/成键动画；"
                "found=false 时表示暂无该反应的动画，应退回文字讲解，不要编造动画。"
                "\n"
                "【使用时机】学生明确问机理、过程、怎么断键成键、为什么会这样发生时调用；"
                "只问命名、性质、实验现象或条件时不必调用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "学生的原问题，用中文表述。"
                            "例如「乙烯和溴加成的机理是什么」。"
                        ),
                    }
                },
                "required": ["query"],
            },
            handler=find_reaction_mechanism,
            timeout=10.0,
        )
    ]


__all__ = ["build_reaction_tools"]
