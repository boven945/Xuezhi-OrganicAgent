"""知识数据层：教材文本切分、语料准入与检索质量评估。

模块标识 `knowledge-data`，对应 `docs/knowledge-base.md` 的治理要求。

核心职责（与 `backend-rag` 的边界）：
- `chunking`：把教材原文切成语义完整的片段
- `corpus`：语料加载与**准入校验**（含版权合规闸门）
- `evaluation`：检索质量评估（供阈值决策使用）

**边界**：`backend-rag` 负责存储与检索，本模块负责
"什么内容可以进索引"与"检索效果如何"。二者分离，
这样准入策略变更不影响检索实现。

详见 `docs/knowledge-base-implementation.md`。
"""

from .builder import build_index, load_corpus_dir
from .chunking import (
    CN_SEPARATORS,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    TEXTBOOK_HEADING,
    TextChunk,
    chunk_by_section,
    looks_like_formula,
    looks_like_table,
    merge_headings,
    normalize_whitespace,
    split_text,
)
from .corpus import (
    ALLOWED_LICENSES,
    CORPUS_SCHEMA,
    CorpusEntry,
    check_license,
    compute_content_hash,
    corpus_stats,
    load_corpus,
    to_chunks,
)
from .evaluation import (
    EvalCase,
    RetrievalMetrics,
    RetrievalRun,
    evaluate,
    hit_rate_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    run_store_evaluation,
    suggest_threshold,
)

__all__ = [
    # 构建
    "build_index",
    "load_corpus_dir",
    # 切分
    "TextChunk",
    "CN_SEPARATORS",
    "TEXTBOOK_HEADING",
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_CHUNK_OVERLAP",
    "chunk_by_section",
    "looks_like_formula",
    "looks_like_table",
    "merge_headings",
    "normalize_whitespace",
    "split_text",
    # 语料
    "ALLOWED_LICENSES",
    "CORPUS_SCHEMA",
    "CorpusEntry",
    "check_license",
    "compute_content_hash",
    "corpus_stats",
    "load_corpus",
    "to_chunks",
    # 评估
    "EvalCase",
    "RetrievalMetrics",
    "RetrievalRun",
    "evaluate",
    "hit_rate_at_k",
    "ndcg_at_k",
    "precision_at_k",
    "recall_at_k",
    "reciprocal_rank",
    "run_store_evaluation",
    "suggest_threshold",
]