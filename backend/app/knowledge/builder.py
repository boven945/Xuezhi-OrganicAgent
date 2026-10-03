"""知识库构建流水线。

把散落在多个语料文件中的条目合并为**单一索引**，
并保证跨文件的治理一致性。

设计要点：

- **跨文件重复 ``chunk_id`` 会被拒绝**。同一个 id 出现在两个文件时，
  向量会互相覆盖、来源可追溯性失效（`knowledge-base.md` §2）。
- **embedding 模型必须全局一致**。§3 要求变更模型时完整重建索引，
  不可混用，因此合并阶段就校验。
- **语料规模以实测为准**。联网核实：观察检索阈值效果需要数百条量级
  样本（距离分布才有区分度）。当前有机化学语料仅 41 条，
  属"最小可用集"，阈值决策须待规模扩充后重做（C5/H18）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Sequence

from app.knowledge.corpus import load_corpus, to_chunks
from app.rag.errors import InvalidDocumentError
from app.rag.models import KnowledgeChunk

logger = logging.getLogger(__name__)


def load_corpus_dir(
    directory: str | Path,
    embedding_model: str,
    *,
    require_approved: bool = True,
    enforce_license: bool = True,
) -> list[KnowledgeChunk]:
    """加载目录下全部语料文件并合并。

    文件按名称排序遍历，保证构建顺序确定（可复现）。

    Args:
        directory: 语料目录。**不得来自用户输入**
            （`security-privacy.md` §4 要求路径由构建流程决定）。
        embedding_model: 目标模型标识，所有文件必须一致。
        require_approved: 是否要求已批准。
        enforce_license: 是否校验许可白名单。

    Returns:
        合并后的片段列表，按 ``chunk_id`` 排序。

    Raises:
        InvalidDocumentError: 目录不存在、为空，或跨文件id 重复。
    """
    d = Path(directory)
    if not d.is_dir():
        raise InvalidDocumentError(f"语料目录不存在：{d}")

    files = sorted(d.glob("*.json"))
    if not files:
        raise InvalidDocumentError(f"语料目录中没有 JSON 文件：{d}")

    merged: list[KnowledgeChunk] = []
    seen: dict[str, str] = {}
    models: set[str] = set()

    for f in files:
        entries = load_corpus(str(f))
        chunks = to_chunks(
            entries,
            embedding_model,
            require_approved=require_approved,
            enforce_license=enforce_license,
        )
        for c in chunks:
            if c.chunk_id in seen:
                raise InvalidDocumentError(
                    f"chunk_id 跨文件重复：{c.chunk_id!r}"
                    f"（已出现于 {seen[c.chunk_id]}，又出现于 {f.name}）。"
                    "重复 id 会导致向量覆盖、来源可追溯性失效。"
                )
            seen[c.chunk_id] = f.name
            models.add(c.embedding_model)
        merged.extend(chunks)
        logger.info("已加载语料文件: file=%s chunks=%d", f.name, len(chunks))

    if len(models) > 1:
        raise InvalidDocumentError(
            f"语料目录内存在多个 embedding 模型：{sorted(models)}。"
            "变更模型时须完整重建索引，不可混用（§3）。"
        )

    merged.sort(key=lambda c: c.chunk_id)
    logger.info(
        "语料合并完成: files=%d chunks=%d model=%s",
        len(files),
        len(merged),
        embedding_model,
    )
    return merged


def build_index(
    persist_directory: str,
    corpus_directory: str,
    embedding: Any,
    *,
    collection_name: str = "knowledge_main",
    index_version: str = "unversioned",
) -> dict[str, Any]:
    """构建完整索引：加载语料 → 向量化 → 写入向量库。

    Args:
        persist_directory: 索引持久化目录。
        corpus_directory: 语料目录。
        embedding:嵌入提供者（须实现 :class:`~app.rag.store.EmbeddingProvider`）。
        collection_name: 集合名。**须 3-512 字符且只含
            ``[a-zA-Z0-9._-]``**（实测 chromadb 的约束）。
        index_version: 索引版本号，须与评估报告对应（§7）。

    Returns:
        含写入条数、主题分布等信息的报告字典。

    Raises:
        InvalidDocumentError: 语料不合规。
        app.rag.errors.IndexNotReadyError: 向量库不可用。
    """
    from app.rag.store import build_store

    chunks = load_corpus_dir(corpus_directory, embedding.model_id)
    store = build_store(
        persist_directory,
        collection_name,
        embedding,
        index_version=index_version,
    )
    written = store.add(chunks)

    from app.knowledge.corpus import corpus_stats

    stats = corpus_stats(chunks)
    report = {
        "index_version": index_version,
        "embedding_model": embedding.model_id,
        "collection": collection_name,
        "written": written,
        "chunk_count": len(chunks),
        "topic_count": stats["topic_count"],
        "by_topic": stats["by_topic"],
        "avg_chars": stats["avg_chars"],
        "total_chars": stats["total_chars"],
    }
    logger.info(
        "索引构建完成: written=%d topics=%d version=%s",
        written,
        stats["topic_count"],
        index_version,
    )
    return report


__all__ = ["build_index", "load_corpus_dir"]