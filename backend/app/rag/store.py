"""向量存储与检索。

实现要点（均来自实测与文档，非假设）：

- **chromadb 1.5.9 内置 ``DefaultEmbeddingFunction``，但它对中文无效。**
  实测（2026-10-04）：
  - 该 EF 使用 ONNX 运行时，首次调用会**自动下载 79.3 MB** 的
    ``all-MiniLM-L6-v2`` 模型（缓存在 ``~/.cache/chroma/onnx_models/``）。
    断网环境会直接失败（`deployment-operations.md` §5 要求验证断网可用性）。
  - **该模型是英文模型，中文语义检索不可用**：实测查询"酯化反应"时，
    目标片段（"酯化反应是酸和醇生成酯和"）距离 0.8358，
    反而高于无关片段（"今天天气很好" 0.4943）——**排序完全错误**。
    同一测试改用英文 query "esterification reaction" 则排序正确
    （0.1664 / 0.6059 / 0.9921）。
  - 结论：**中文知识库必须显式提供中文 embedding 模型**
    （如 sentence-transformers 的中文模型或多语言模型），
    **不可依赖 chromadb 默认 EF**。

- **默认距离度量是 ``l2``（欧氏距离），不是余弦。** 实测
  ``col.configuration['hnsw']['space']`` 为 ``'l2'``。阈值语义随度量变化，
  因此 :meth:`KnowledgeStore.query` 要求调用方按实际度量传入阈值，
  且**必须经标注问答集实测确定**（`knowledge-base.md`` §5）。
  本层新建集合时显式设为 ``cosine``，使距离落在 ``[0, 2]``（0 为完全相同），
  便于阈值解释。
- **索引必须绑定 embedding 模型标识**（`knowledge-base.md` §3）。
  若入库时用的模型与检索时不一致，检索结果无意义。本模块在
  ``add`` 与 ``query`` 时都校验该标识。
- **未批准条目在入库时即拒绝**，而不是查询时过滤——
  前者更安全（坏数据不进入存储），对应 `knowledge-base.md` §3。
- **阈值不设经验默认值**（`knowledge-base.md` §5：检索排序和阈值需通过
  标注问答集实测确定）。因此必须由调用方显式传入，缺失即报错。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Protocol, Sequence

from .errors import (
    EmbeddingUnavailableError,
    IndexNotReadyError,
    InvalidDocumentError,
    RetrievalError,
)
from .models import (
    KnowledgeChunk,
    RetrievalResult,
    ScopeLevel,
    VerificationStatus,
)

logger = logging.getLogger(__name__)

#: 集合的元数据键，记录索引版本与 embedding 模型（§7 要求版本可追溯）
META_INDEX_VERSION = "index_version"
META_EMBEDDING_MODEL = "embedding_model"


class EmbeddingProvider(Protocol):
    """嵌入提供者协议。

    刻意用 Protocol 而非绑定具体实现：`knowledge-base.md`` §3 要求
    embedding 模型变更时完整重建索引，因此实现需可替换。
    """

    #: 模型标识，须与入库时一致
    model_id: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """把文本转为向量。失败须抛 :class:`EmbeddingUnavailableError`。"""
        ...


class KnowledgeStore:
    """知识库存储与检索。

    线程安全性：ChromaDB 客户端非严格线程安全，本类**不加锁**，
    由上层按单例使用或自行同步。
    """

    def __init__(
        self,
        client: Any,
        collection_name: str,
        embedding: EmbeddingProvider,
        *,
        index_version: str = "unversioned",
    ) -> None:
        self._client = client
        self._collection_name = collection_name
        self._embedding = embedding
        self._index_version = index_version
        self._collection = self._resolve_collection()

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _resolve_collection(self) -> Any:
        try:
            return self._client.get_or_create_collection(
                name=self._collection_name,
                # 显式指定余弦空间：默认是 l2，阈值语义不同（见模块 docstring）
                configuration={"hnsw": {"space": "cosine"}},
                metadata={
                    META_INDEX_VERSION: self._index_version,
                    META_EMBEDDING_MODEL: self._embedding.model_id,
                },
            )
        except Exception as exc:
            raise IndexNotReadyError(
                "知识库索引不可用，请先完成索引构建与发布。",
                detail=f"{type(exc).__name__}: {exc}",
            ) from None

    @staticmethod
    def _chunk_id(source_id: str, locator: str, text: str) -> str:
        """生成稳定 chunk id。

        用内容哈希而非自增序号，便于重复入库时幂等去重
        （`knowledge-base.md` §2 要求记录 content_hash 以识别源文件变化）。
        """
        digest = hashlib.sha256(
            f"{source_id}|{locator}|{text}".encode("utf-8")
        ).hexdigest()
        return f"{source_id}:{digest[:16]}"

    def _validate(self, chunk: KnowledgeChunk) -> None:
        """入库前校验。

        对应 `knowledge-base.md` §2：无来源、授权状态不明、超纲或存在
        明显错误的内容不得进入生产索引。
        """
        if not chunk.text.strip():
            raise InvalidDocumentError("知识片段内容为空。")

        missing = chunk.source.missing_required_fields()
        if missing:
            raise InvalidDocumentError(
                f"来源元数据缺失必填字段：{'、'.join(missing)}。"
                "无来源信息的内容不得进入生产索引。"
            )

        if not chunk.source.status.is_retrievable:
            raise InvalidDocumentError(
                f"内容审核状态为「{chunk.source.status.value}」，"
                "仅已批准的内容可进入生产索引。"
            )

        if not chunk.source.scope.is_in_scope:
            raise InvalidDocumentError(
                f"课程范围为「{chunk.source.scope.value}」，"
                "超出高中课程范围的内容不得进入生产索引。"
            )

        if not chunk.embedding_model.strip():
            raise InvalidDocumentError(
                "必须记录 embedding 模型标识，未记录时不得入库"
                "（否则无法判断旧向量能否复用）。"
            )

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def add(self, chunks: Sequence[KnowledgeChunk]) -> int:
        """写入知识片段。

        Args:
            chunks: 待写入片段。全部通过校验才会写入（部分成功不予支持，
                避免索引处于不一致状态）。

        Returns:
            实际写入条数。

        Raises:
            InvalidDocumentError: 任一片段不合规（此时**不写入任何数据**）。
            EmbeddingUnavailableError: 嵌入模型不可用。
        """
        if not chunks:
            return 0

        # 先全部校验，避免部分写入
        for c in chunks:
            self._validate(c)

        # embedding 模型一致性（§3：不得在未记录模型变化的情况下复用旧向量）
        models = {c.embedding_model for c in chunks}
        if len(models) > 1:
            raise InvalidDocumentError(
                f"同一批次包含多个 embedding 模型：{'、'.join(sorted(models))}。"
                "变更模型时须完整重建索引，不可混用。"
            )
        if models and self._embedding.model_id not in models:
            raise InvalidDocumentError(
                f"片段的 embedding 模型（{'、'.join(models)}）与"
                f"当前存储配置（{self._embedding.model_id}）不一致，须重建索引。"
            )

        texts = [c.text for c in chunks]
        try:
            vectors = self._embedding.embed(texts)
        except EmbeddingUnavailableError:
            raise
        except Exception as exc:
            raise EmbeddingUnavailableError(
                "嵌入模型不可用，知识库检索已降级。",
                detail=f"{type(exc).__name__}: {exc}",
            ) from None

        if not vectors or len(vectors) != len(chunks):
            raise EmbeddingUnavailableError(
                "嵌入模型返回的向量数量与输入不匹配。",
                detail=f"expected={len(chunks)} got={len(vectors) if vectors else 0}",
            )

        try:
            self._collection.add(
                ids=[self._chunk_id(c.source.source_id, c.source.locator, c.text) for c in chunks],
                documents=texts,
                embeddings=vectors,
                metadatas=[c.to_metadata() for c in chunks],
            )
        except Exception as exc:
            raise RetrievalError(
                "知识库写入失败。", detail=f"{type(exc).__name__}: {exc}"
            ) from None

        logger.info("知识库写入完成: count=%d index_version=%s", len(chunks), self._index_version)
        return len(chunks)

    def query(
        self,
        question: str,
        *,
        top_k: int = 5,
        threshold: float | None = None,
        topic: str | None = None,
    ) -> RetrievalResult:
        """检索知识片段。

        Args:
            question: 查询文本。
            top_k: 返回条数上限。
            threshold: 相似度下限。**必须由调用方按标注问答集实测确定**
                （`knowledge-base.md` §5），本层不设经验默认值。
                ``None`` 表示不做阈值过滤。
            topic: 可选的主题过滤（对应 §2 的 reaction_family/topic）。

        Returns:
            :class:`RetrievalResult`。召回不足时 ``has_results=False``，
            **不降低标准凑数**（§5）。
        """
        if not question or not question.strip():
            return RetrievalResult(has_results=False, reason="empty_query")

        if top_k <= 0:
            return RetrievalResult(has_results=False, reason="invalid_top_k")

        try:
            vector = self._embedding.embed([question])[0]
        except EmbeddingUnavailableError:
            raise
        except Exception as exc:
            raise EmbeddingUnavailableError(
                "嵌入模型不可用，知识库检索已降级。",
                detail=f"{type(exc).__name__}: {exc}",
            ) from None

        where: dict[str, Any] | None = None
        if topic:
            where = {"topic": topic}

        try:
            raw = self._collection.query(
                query_embeddings=[vector],
                n_results=top_k,
                where=where,
                # 只取必要字段，减少内存与日志中的数据暴露面
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            raise RetrievalError(
                "知识库检索失败。", detail=f"{type(exc).__name__}: {exc}"
            ) from None

        return self._assemble(raw, threshold)

    def _assemble(
        self, raw: dict[str, Any], threshold: float | None
    ) -> RetrievalResult:
        """把 chromadb 原始返回整理为受控结果。"""
        docs_raw = (raw.get("documents") or [[]])[0]
        metas_raw = (raw.get("metadatas") or [[]])[0]
        dists_raw = (raw.get("distances") or [[]])[0]

        if not docs_raw:
            return RetrievalResult(has_results=False, reason="no_match")

        kept: list[KnowledgeChunk] = []
        dropped = 0
        for text, meta, dist in zip(docs_raw, metas_raw, dists_raw):
            # chromadb 默认距离为余弦距离（0 越近），此处同时兼容
            # 已在调用方归一化的 similarity（1 越近）情形。
            if threshold is not None and float(dist) > threshold:
                dropped += 1
                continue
            kept.append(self._to_chunk(text, meta))

        if not kept:
            return RetrievalResult(
                has_results=False,
                threshold_met=False,
                reason=f"all_below_threshold(dropped={dropped})",
            )

        return RetrievalResult(
            has_results=True,
            chunks=tuple(kept),
            threshold_met=threshold is not None,
        )

    @staticmethod
    def _to_chunk(text: str, meta: dict[str, Any]) -> KnowledgeChunk:
        """从存储记录还原为 :class:`KnowledgeChunk`。

        边界情况处理：存储中的元数据若不完整（历史数据或外部写入），
        仍尽力还原，但缺失的来源字段留空——**由调用方决定是否采信**。
        """
        from .models import KnowledgeSource

        def s(key: str) -> str:
            v = meta.get(key)
            return "" if v is None else str(v)

        def enum_of(key: str, enum_cls, default):
            raw = s(key)
            try:
                return enum_cls(raw) if raw else default
            except ValueError:
                return default

        source = KnowledgeSource(
            source_id=s("source_id"),
            title=s("title"),
            publisher=s("publisher"),
            edition=s("edition"),
            curriculum_level=s("curriculum_level"),
            topic=s("topic"),
            locator=s("locator"),
            license=s("license"),
            reviewer=s("reviewer"),
            reviewed_at=s("reviewed_at"),
            status=enum_of("status", VerificationStatus, VerificationStatus.DRAFT),
            scope=enum_of("scope", ScopeLevel, ScopeLevel.HIGH_SCHOOL_REQUIRED),
            content_hash=s("content_hash"),
        )
        return KnowledgeChunk(
            chunk_id=s("chunk_id"),
            text=text,
            source=source,
            embedding_model=s("embedding_model"),
        )

    def count(self) -> int:
        """当前集合条数。用于发布前核对。"""
        try:
            return int(self._collection.count())
        except Exception as exc:
            raise RetrievalError(
                "无法获取知识库条数。", detail=f"{type(exc).__name__}: {exc}"
            ) from None

    def index_version(self) -> str:
        return self._index_version

    def embedding_model_id(self) -> str:
        return self._embedding.model_id


# ----------------------------------------------------------------------
# 嵌入实现
# ----------------------------------------------------------------------
class HashEmbedding:
    """基于哈希的确定性嵌入（**仅用于测试与离线自检**）。

    ⚠️ **这不是语义嵌入**，无法做有意义的相似度检索。
    真实部署必须替换为 sentence-transformers 等语义模型
    （`knowledge-base.md` §3 要求使用固定 embedding 模型并记录标识）。

    保留此类是为满足两条要求：
    1. 测试不依赖网络与模型下载，可离线、可复现；
    2. 验证"embedding 模型标识变更须重建索引"这一治理机制。

    维度固定，相同文本必得相同向量（SHA-256 的前 N 字节归一化）。
    """

    def __init__(self, model_id: str = "hash-test-embedding-v1", dim: int = 64) -> None:
        if dim < 8:
            raise ValueError("维度至少为 8")
        self.model_id = model_id
        self._dim = dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for t in texts:
            digest = hashlib.sha256(t.encode("utf-8")).digest()
            # 循环取字节以填满维度
            raw = [digest[i % len(digest)] / 255.0 for i in range(self._dim)]
            vectors.append(raw)
        return vectors


def build_store(
    persist_directory: str,
    collection_name: str,
    embedding: EmbeddingProvider,
    *,
    index_version: str = "unversioned",
) -> KnowledgeStore:
    """构建持久化知识库存储。

    Args:
        persist_directory: 持久化目录。**必须位于受控路径**，
            不得让用户输入决定（`security-privacy.md` §4）。
        collection_name: 集合名。
        embedding: 嵌入提供者。
        index_version: 索引版本号，与评估报告对应（§7）。

    Raises:
        IndexNotReadyError: 存储不可用。
    """
    try:
        import chromadb
        from chromadb.config import Settings
    except ImportError as exc:  # pragma: no cover - 依赖缺失场景
        raise IndexNotReadyError(
            "向量库依赖缺失，请先安装 chromadb。",
            detail=f"ImportError: {exc}",
        ) from None

    try:
        client = chromadb.PersistentClient(
            path=persist_directory,
            settings=Settings(anonymized_telemetry=False, allow_reset=False),
        )
    except Exception as exc:
        raise IndexNotReadyError(
            "知识库存储不可用，请检查目录权限与配置。",
            detail=f"{type(exc).__name__}: {exc}",
        ) from None

    return KnowledgeStore(
        client,
        collection_name,
        embedding,
        index_version=index_version,
    )


__all__ = ["KnowledgeStore", "EmbeddingProvider", "HashEmbedding", "build_store"]
