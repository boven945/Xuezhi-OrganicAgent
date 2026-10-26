"""反应机理可视化模块。

提供反应库加载、帧生成（纯插值 + 惰性 RDKit 坐标）与端点。
"""
from app.reaction.frames import build_frames, generate_mechanism
from app.reaction.library import ReactionLibrary
from app.reaction.routes import MechanismRequest, MechanismResponse, router
from app.reaction.schemas import (
    AtomPos,
    BondFrame,
    BondState,
    MechanismFrame,
    ReactionDef,
    ReactionLibraryFile,
    ReactionMechanismFile,
)

__all__ = [
    "AtomPos",
    "BondFrame",
    "BondState",
    "MechanismFrame",
    "MechanismRequest",
    "MechanismResponse",
    "ReactionDef",
    "ReactionLibrary",
    "ReactionLibraryFile",
    "ReactionMechanismFile",
    "build_frames",
    "generate_mechanism",
    "router",
]
