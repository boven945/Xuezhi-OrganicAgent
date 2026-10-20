"""反应机理可视化端点。

端点：``POST /api/v1/reaction/mechanism``

设计（对应计划 §6 与架构 §6 降级原则）：

- 入参 ``reaction_id`` 不在库中 → ``200 + ok=false``（``reaction_not_found``），
  **不抛 5xx**——未收录是正常情况，前端据此退回文本讲解。
- RDKit 不可用（本机被策略拦截 / 容器缺失）→ ``503 + api_component_unavailable``，
  与分子端点对化学引擎的状态处理一致。
- 反应数据校验失败（SMILES 无法解析等）→ ``200 + ok=false``（``reaction_generation_failed``）。
- **不依赖 LLM / 向量库**：机理帧由 RDKit 确定性生成，模型不可用时该端点仍可用。
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from uuid import uuid4

from app.reaction.frames import generate_mechanism
from app.reaction.library import ReactionLibrary

logger = logging.getLogger(__name__)

router = APIRouter()

#: 反应库延迟加载（文件缺失则空库，端点降级）。
_LIB: Optional[ReactionLibrary] = None


def _get_library() -> ReactionLibrary:
    global _LIB
    if _LIB is None:
        _LIB = ReactionLibrary.load()
    return _LIB


class MechanismRequest(BaseModel):
    """机理动画请求。"""

    reaction_id: str = Field(..., min_length=1)
    frame_count: int = Field(default=24, ge=2, le=120)
    fps: int = Field(default=12, ge=1, le=60)


class MechanismResponse(BaseModel):
    """机理动画响应（沿用分子端点的 ``ok`` 信封）。"""

    model_config = ConfigDict(populate_by_name=True)

    request_id: str
    ok: bool = True
    error: Optional[dict] = None
    #: 已是 ``ReactionMechanismFile.model_dump(by_alias=True)`` 的字典。
    data: Optional[dict] = None


@router.post(
    "/api/v1/reaction/mechanism",
    response_model=MechanismResponse,
    tags=["反应机理"],
)
def reaction_mechanism(payload: MechanismRequest) -> MechanismResponse:
    """生成有机反应断键/成键 3D 机理动画帧序列。

    返回 ``reaction-mechanism/v1``（经 ``data`` 字段）。
    """
    request_id = _new_request_id()
    definition = _get_library().by_id(payload.reaction_id)
    if definition is None:
        return MechanismResponse(
            request_id=request_id,
            ok=False,
            error={
                "code": "reaction_not_found",
                "message": "未收录该反应的机理动画，已退回文本讲解。",
                "retryable": False,
                "request_id": request_id,
            },
        )
    try:
        mech = generate_mechanism(
            definition, frame_count=payload.frame_count, fps=payload.fps
        )
    except RuntimeError as exc:
        msg = str(exc)
        if "RDKit 不可用" in msg:
            # 化学引擎缺失 → 503（与分子端点 classify 结果一致）。
            return MechanismResponse(
                request_id=request_id,
                ok=False,
                error={
                    "code": "api_component_unavailable",
                    "message": "化学引擎暂不可用，机理动画需要后端 RDKit 支持。",
                    "retryable": True,
                    "request_id": request_id,
                },
            )
        return MechanismResponse(
            request_id=request_id,
            ok=False,
            error={
                "code": "reaction_generation_failed",
                "message": "机理帧生成失败，请稍后重试或联系教师补充该反应。",
                "retryable": True,
                "request_id": request_id,
            },
        )
    except ValueError as exc:
        return MechanismResponse(
            request_id=request_id,
            ok=False,
            error={
                "code": "reaction_generation_failed",
                "message": f"反应数据校验失败：{exc}",
                "retryable": False,
                "request_id": request_id,
            },
        )
    return MechanismResponse(
        request_id=request_id, ok=True, data=mech.model_dump(by_alias=True)
    )


def _new_request_id() -> str:
    # 不依赖 app.api.deps（其 agent 链会拉起 RDKit，本机被策略拦截）。
    # request_id 仅作关联/日志串，uuid 足够。
    return uuid4().hex


__all__ = ["router", "MechanismRequest", "MechanismResponse"]
