"""知识检索层。

职责边界见 `docs/architecture.md` §2：过滤、嵌入、召回高中课程范围内的知识条目。

设计要点：

- **来源可追溯是硬要求**。`docs/knowledge-base.md` §5 要求检索结果携带
  来源 ID、标题、版本、定位和审核状态；§3 要求"仅将已批准且课程范围
  匹配的片段提供给模型"。因此本层的每条结果都带完整来源元数据，
  且**未批准条目在检索阶段即被排除**。
- **召回不足时如实返回空结果**，不降低标准凑数（`knowledge-base.md` §5）。
  空结果会明确告知模型"资料不足"，而不是让它凭记忆编造。
- **模型推断与检索事实严格分离**（`architecture.md` §5）。本层只返回
  检索到的原文片段与来源，不生成任何结论。
"""

from .errors import (
    RAGError,
    EmbeddingUnavailableError,
    IndexNotReadyError,
    InvalidDocumentError,
    RetrievalError,
)
from .models import (
    KnowledgeChunk,
    KnowledgeSource,
    RetrievalResult,
    ScopeLevel,
    VerificationStatus,
)
from .store import KnowledgeStore, build_store

__all__ = [
    "RAGError",
    "EmbeddingUnavailableError",
    "IndexNotReadyError",
    "InvalidDocumentError",
    "RetrievalError",
    "KnowledgeChunk",
    "KnowledgeSource",
    "RetrievalResult",
    "ScopeLevel",
    "VerificationStatus",
    "KnowledgeStore",
    "build_store",
]
