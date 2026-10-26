"""反应库加载与（纯逻辑）校验。

加载 ``data/reactions/*.json``（``reaction-library/v1``）。

**RDKit 深度校验**（SMILES 解析、映射号一致性、断键/成键存在性）放在
``scripts/validate_reactions.py``，须在容器内运行——本模块只做**不依赖 RDKit** 的
结构校验与读取，确保无 RDKit 环境（如主机单测）也能导入与使用，不会因缺依赖而崩。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from .schemas import ReactionDef, ReactionLibraryFile

logger = logging.getLogger(__name__)

#: 默认反应库路径（容器内挂载；主机回退到仓库内 data/）。
DEFAULT_REACTION_PATH = os.environ.get(
    "XUEZHI_REACTION_PATH", "/work/data/reactions/candidates.json"
)
_LOCAL_FALLBACK = (
    Path(__file__).resolve().parents[3] / "data" / "reactions" / "candidates.json"
)


class ReactionLibrary:
    """已加载的反应库，提供按 id 查询与过滤。"""

    def __init__(self, data: ReactionLibraryFile) -> None:
        self._data = data

    @classmethod
    def load(cls, path: Optional[str | Path] = None) -> "ReactionLibrary":
        """加载反应库。

        路径优先级：显式 ``path`` → 环境变量 ``XUEZHI_REACTION_PATH`` →
        容器内默认 → 仓库内 ``data/reactions/candidates.json``。

        文件缺失或结构损坏**不致命**：返回空库，端点据此返回
        ``reaction_not_found`` 降级，而非在启动时崩溃。
        """
        candidate = path or DEFAULT_REACTION_PATH
        p = Path(candidate)
        if not p.exists() and candidate == DEFAULT_REACTION_PATH:
            p = _LOCAL_FALLBACK
        if not p.exists():
            logger.warning("反应库文件不存在：%s，返回空库（降级）", candidate)
            return cls(ReactionLibraryFile())
        try:
            raw = p.read_text(encoding="utf-8")
            data = ReactionLibraryFile.model_validate_json(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("反应库 %s 解析失败，返回空库（降级）：%s", p, exc)
            return cls(ReactionLibraryFile())
        return cls(data)

    def all(self) -> list[ReactionDef]:
        return list(self._data.reactions)

    def by_id(self, rid: str) -> Optional[ReactionDef]:
        for r in self._data.reactions:
            if r.id == rid:
                return r
        return None

    def filter(
        self,
        topic: Optional[str] = None,
        grade: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[ReactionDef]:
        out = self._data.reactions
        if topic is not None:
            out = [r for r in out if r.topic == topic]
        if grade is not None:
            out = [r for r in out if r.grade == grade]
        if status is not None:
            out = [r for r in out if r.status == status]
        return out

    def approved(self) -> list[ReactionDef]:
        return self.filter(status="approved")


__all__ = ["ReactionLibrary", "DEFAULT_REACTION_PATH"]
