"""知识检索的数据契约。

字段设计直接对应 `docs/knowledge-base.md` §2 的来源准入元数据表，
不自行发明字段——这样审核流程与检索实现能一一对应。

核心约束（`knowledge-base.md` §3、§5、§7）：

- 每条片段必须绑定**固定 embedding 模型标识**，否则索引不可复用。
- 只有 ``APPROVED`` 状态且课程范围匹配的片段才可进入检索结果。
- 来源信息不完整时**拒绝入库**，而不是留空——空来源等于伪造引用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class VerificationStatus(str, Enum):
    """知识条目审核状态。

    对应 ``knowledge-base.md`` §2 的 ``status`` 字段：草稿、待审、已批准、
    已弃用或已撤回。只有 ``APPROVED`` 可被检索层返回给模型。
    """

    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    DEPRECATED = "deprecated"
    WITHDRAWN = "withdrawn"

    @property
    def is_retrievable(self) -> bool:
        """是否可被检索层返回。

        ``knowledge-base.md`` §3：只有通过验收并发布的内容才进入生产索引。
        """
        return self is VerificationStatus.APPROVED


class ScopeLevel(str, Enum):
    """课程范围。

    ``knowledge-base.md`` §1 要求知识库限定在高中课程范围，
    §4 要求识别并排除超纲内容。
    """

    #: 高中必修/选择性必修（课标要求）
    HIGH_SCHOOL_REQUIRED = "high_school_required"
    #: 高中范围内拓展（需明确标注，不得混入基础答案）
    HIGH_SCHOOL_EXTENDED = "high_school_extended"
    #: 大学内容——**禁止进入检索结果**
    UNDERGRADUATE = "undergraduate"

    @property
    def is_in_scope(self) -> bool:
        """是否在高中课程范围内。

        ``knowledge-base.md`` §4：拓展内容须标注，大学内容须过滤。
        """
        return self in (
            ScopeLevel.HIGH_SCHOOL_REQUIRED,
            ScopeLevel.HIGH_SCHOOL_EXTENDED,
        )


@dataclass(frozen=True, slots=True)
class KnowledgeSource:
    """一条来源的完整元数据。

    字段对应 ``knowledge-base.md` §2 的准入表。``locator`` 可为空
    （不是所有材料都有页码），但**其余字段必须有值**。
    """

    #: 稳定且唯一的来源标识
    source_id: str
    title: str
    #: 教材出版社或发布机构
    publisher: str
    #: 版本 / 适用年份
    edition: str
    #: 学段与课程范围
    curriculum_level: str
    #: 知识主题或反应类别
    topic: str
    #: 章节、页码、题号（可获得时）
    locator: str = ""
    #: 使用许可与授权范围
    license: str = ""
    #: 审核人
    reviewer: str = ""
    #: 审核时间（ISO 日期）
    reviewed_at: str = ""
    status: VerificationStatus = VerificationStatus.DRAFT
    scope: ScopeLevel = ScopeLevel.HIGH_SCHOOL_REQUIRED
    #: 内容校验标识，用于识别源文件变化（§2 的 content_hash）
    content_hash: str = ""

    def to_metadata(self) -> dict[str, Any]:
        """转为存入向量库的 metadata。

        ChromaDB 的 metadata 只接受标量值，故全部转为字符串。
        """
        return {
            "source_id": self.source_id,
            "title": self.title,
            "publisher": self.publisher,
            "edition": self.edition,
            "curriculum_level": self.curriculum_level,
            "topic": self.topic,
            "locator": self.locator,
            "license": self.license,
            "reviewer": self.reviewer,
            "reviewed_at": self.reviewed_at,
            "status": self.status.value,
            "scope": self.scope.value,
            "content_hash": self.content_hash,
        }

    def missing_required_fields(self) -> list[str]:
        """返回缺失的必填字段名。

        ``knowledge-base.md`` §2：无来源、授权状态不明的内容不得进入生产索引。
        """
        required = {
            "source_id": self.source_id,
            "title": self.title,
            "publisher": self.publisher,
            "edition": self.edition,
            "topic": self.topic,
            "reviewer": self.reviewer,
        }
        return [k for k, v in required.items() if not (v or "").strip()]


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    """一个可检索的知识片段。

    切分原则遵循 ``knowledge-base.md`` §3：按完整语义单元拆分，
    优先保持"概念/反应步骤/条件/例题/解析"上下文，**不在关键条件中间截断**。
    """

    chunk_id: str
    text: str
    source: KnowledgeSource
    #: 生成该向量时使用的 embedding 模型标识。
    #: §3 要求不得在未记录 embedding 模型变化的情况下复用旧向量。
    embedding_model: str

    def to_metadata(self) -> dict[str, Any]:
        meta = self.source.to_metadata()
        meta["chunk_id"] = self.chunk_id
        meta["embedding_model"] = self.embedding_model
        return meta


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """一次检索的结果。

    ``knowledge-base.md`` §5 要求：召回不足或来源冲突时应拒绝确定性结论。
    因此本类显式携带 ``is_sufficient``，由上层据此提示模型说明"资料不足"。
    """

    #: 是否召回到任何片段
    has_results: bool
    chunks: tuple[KnowledgeChunk, ...] = ()
    #: 相似度阈值是否被满足。低于阈值的结果不计入。
    threshold_met: bool = False
    #: 未召回原因（供诊断，不直接展示给学生）
    reason: str = ""
    #: 来源标记：本层只返回检索事实，不含模型推断
    source: str = "retrieval"

    def to_model_payload(self) -> str:
        """转为回填给 Agent 的内容。

        无结果时明确告知"知识库中没有找到相关内容"，
        使模型如实说明而非凭记忆作答（``knowledge-base.md`` §5）。
        """
        import json

        if not self.has_results or not self.chunks:
            return json.dumps(
                {
                    "found": False,
                    "message": "知识库中未找到符合条件的内容。"
                    "请基于已有知识说明，并明确标注这不来自知识库检索结果。",
                },
                ensure_ascii=False,
            )

        return json.dumps(
            {
                "found": True,
                "note": "以下为知识库检索到的原文片段，请据此作答并标注来源。"
                "若片段不足以回答，应明确说明。",
                "passages": [
                    {
                        "source_id": c.source.source_id,
                        "title": c.source.title,
                        "edition": c.source.edition,
                        "locator": c.source.locator,
                        "scope": c.source.scope.value,
                        "text": c.text,
                    }
                    for c in self.chunks
                ],
            },
            ensure_ascii=False,
        )


__all__ = [
    "KnowledgeSource",
    "KnowledgeChunk",
    "RetrievalResult",
    "ScopeLevel",
    "VerificationStatus",
]
