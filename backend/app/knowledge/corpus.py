"""语料加载与准入。

**版权边界（本模块最重要的约束）**

检索到的法律与行业实践一致：把他人作品用于 AI 训练或知识库，
**在未获授权时不构成合理使用**。要点：

- 台湾高校图书馆公告明确：不得将电子书全文导出建立数据库供AI 训练
  或知识库建置之用，"合理使用并非所有教育用途皆适用"。
- 台湾经济部智慧财产局令函：使用他人原文书籍作线上教材，
  除合理使用情形外应取得著作权人同意，**建议向出版社或作者取得授权**。
- 大陆实务观点：将完整著作或大比例内容输入系统、
  使生成内容可替代原著功能，会显著提高侵权风险；
  较安全的做法是**避免输入完整著作，优先使用节录内容**。

因此本模块采取的设计：

1. **仓库内不放任何教材原文**。语料以「结构化元数据 + 自编内容」形式存在
   （见 `app.rag.dev_corpus`），`.gitignore` 已排除受版权材料。
2. **导入须逐条声明授权状态**。``KnowledgeStore.add`` 校验 6 个必填来源字段
   （`knowledge-base.md` §2），其中 ``license`` 与 ``reviewer`` 不可为空——
   没有授权信息的内容无法通过审核，这是刻意的设计。
3. **仅 ``APPROVED`` 可进入索引**（§3），草稿与待审一律拒绝。
4. **超纲内容拒绝**（§4）。拓展内容允许但须标记
   ``HIGH_SCHOOL_EXTENDED``，与基础内容区分。

本模块负责：读取结构化语料文件 → 校验 → 转为 :class:`KnowledgeChunk`。
**不负责**：教材 PDF 的版权获取与内容提取（属独立的人工流程，
须由项目负责人与出版社/作者完成，见决策登记表 D3）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

from app.rag.errors import InvalidDocumentError
from app.rag.models import (
    KnowledgeChunk,
    KnowledgeSource,
    ScopeLevel,
    VerificationStatus,
)

#: 允许的许可类型。**不含``unknown``**——无法确认许可的内容不得入库。
#:
#: 这是版权合规的硬性闸门：宁可拒收，不可默许。
ALLOWED_LICENSES: frozenset[str] = frozenset(
    {
        "project-authored",  # 项目自编
        "internal-dev-only",  # 仅内部开发使用
        "public-domain",  # 公有领域
        "cc-by-4.0",  # 知识共享署名
        "cc-by-sa-4.0",  # 知识共享署名-相同方式共享
        "licensed-official",  # 已获出版社/作者授权（须留存授权凭证编号）
    }
)

#: 语料文件的顶层结构版本。用于将来格式演进时识别。
CORPUS_SCHEMA = "knowledge-corpus/v1"


@dataclass(frozen=True, slots=True)
class CorpusEntry:
    """语料文件中的一条记录。

    与 :class:`KnowledgeChunk` 分开：前者是**文件层**的原始记录，
    后者是**索引层**的产物。这样校验逻辑可在两者之间清晰定位问题。
    """

    chunk_id: str
    text: str
    source: KnowledgeSource


def load_corpus(path: str | Path) -> list[CorpusEntry]:
    """加载结构化语料文件。

    期望格式（JSON）::

        {
          "schema": "knowledge-corpus/v1",
          "index_version": "kb-2026.10",
          "embedding_model": "BAAI/bge-small-zh",
          "entries": [
            {
              "chunk_id": "esterification-01",
              "text": "酯化反应是...",
              "source": {
                "source_id": "textbook-pep-2019",
                "title": "...",
                "publisher": "...",
                "edition": "...",
                "curriculum_level": "高中必修",
                "topic": "酯化反应",
                "locator": "第六章第三节",
                "license": "licensed-official",
                "reviewer": "...",
                "reviewed_at": "2026-10-04",
                "status": "approved",
                "scope": "high_school_required",
                "content_hash": "..."   # 可选，缺省时由文本计算
              }
            }
          ]
        }

    Args:
        path: JSON 文件路径。**不得来自用户输入**——
            路径由构建流程决定（`security-privacy.md` §4）。

    Returns:
        解析后的记录列表。**此阶段不做准入校验**，
        校验在 :func:`to_chunks` 中进行，便于区分"格式错误"与"不合规"。

    Raises:
        InvalidDocumentError: 文件不存在、格式错误或 schema 不匹配。
    """
    p = Path(path)
    if not p.is_file():
        raise InvalidDocumentError(f"语料文件不存在：{p}")

    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InvalidDocumentError(
            f"语料文件不是合法 JSON（第 {exc.lineno} 行）。",
            detail=f"JSONDecodeError: {exc.msg}",
        ) from None

    if not isinstance(raw, dict):
        raise InvalidDocumentError("语料文件顶层须为 JSON 对象。")

    schema = raw.get("schema")
    if schema != CORPUS_SCHEMA:
        raise InvalidDocumentError(
            f"语料 schema 不支持：{schema!r}，期望 {CORPUS_SCHEMA!r}。"
        )

    entries = raw.get("entries")
    if not isinstance(entries, list):
        raise InvalidDocumentError("语料文件缺少 entries 数组。")

    return list(_parse_entries(entries))


def _parse_entries(entries: list[Any]) -> Iterator[CorpusEntry]:
    """逐条解析记录。逐条校验以便精确报错位置。"""
    for i, item in enumerate(entries):
        if not isinstance(item, dict):
            raise InvalidDocumentError(f"第 {i} 条记录不是对象。")

        chunk_id = str(item.get("chunk_id") or "").strip()
        if not chunk_id:
            raise InvalidDocumentError(f"第 {i} 条记录缺少 chunk_id。")

        text = str(item.get("text") or "").strip()
        if not text:
            raise InvalidDocumentError(f"第 {i} 条记录（{chunk_id}）文本为空。")

        source_raw = item.get("source")
        if not isinstance(source_raw, dict):
            raise InvalidDocumentError(f"第 {i} 条记录（{chunk_id}）缺少 source 对象。")

        yield CorpusEntry(
            chunk_id=chunk_id,
            text=text,
            source=_parse_source(source_raw, chunk_id),
        )


def _parse_source(raw: dict[str, Any], chunk_id: str) -> KnowledgeSource:
    """解析来源字段。

    枚举值严格校验：``status`` 与 ``scope`` 只接受已知取值。
    未知取值应视为数据错误而非静默回退到默认值——
    静默回退会把"待审内容"变成"已批准"，是严重的治理失效。
    """
    try:
        status = VerificationStatus(str(raw.get("status") or ""))
    except ValueError:
        raise InvalidDocumentError(
            f"{chunk_id} 的 status 取值非法：{raw.get('status')!r}。"
            f"合法值：{[e.value for e in VerificationStatus]}"
        ) from None

    try:
        scope = ScopeLevel(str(raw.get("scope") or ""))
    except ValueError:
        raise InvalidDocumentError(
            f"{chunk_id} 的 scope 取值非法：{raw.get('scope')!r}。"
            f"合法值：{[e.value for e in ScopeLevel]}"
        ) from None

    text = str(raw.get("text") or "")
    return KnowledgeSource(
        source_id=str(raw.get("source_id") or "").strip(),
        title=str(raw.get("title") or "").strip(),
        publisher=str(raw.get("publisher") or "").strip(),
        edition=str(raw.get("edition") or "").strip(),
        curriculum_level=str(raw.get("curriculum_level") or "").strip(),
        topic=str(raw.get("topic") or "").strip(),
        locator=str(raw.get("locator") or "").strip(),
        license=str(raw.get("license") or "").strip(),
        reviewer=str(raw.get("reviewer") or "").strip(),
        reviewed_at=str(raw.get("reviewed_at") or "").strip(),
        status=status,
        scope=scope,
        content_hash=str(raw.get("content_hash") or "").strip()
        or compute_content_hash(text),
    )


def compute_content_hash(text: str) -> str:
    """计算内容哈希。

    用途（`knowledge-base.md` §2）：识别源文件变化。
    内容变了而哈希没变，说明流程有问题。
    """
    normalized = "".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def check_license(entry: CorpusEntry) -> None:
    """校验许可类型。

    本模块**最硬的一道闸门**：许可不在白名单内即拒绝入库。

    Args:
        entry: 待校验记录。

    Raises:
        InvalidDocumentError: 许可缺失或不在白名单内。
    """
    license_value = entry.source.license.strip()
    if not license_value:
        raise InvalidDocumentError(
            f"{entry.chunk_id} 缺少许可信息，无法确认使用授权，"
            "按 `knowledge-base.md` §2 不得进入生产索引。"
        )
    if license_value not in ALLOWED_LICENSES:
        raise InvalidDocumentError(
            f"{entry.chunk_id} 的许可类型 {license_value!r} 不在白名单内。"
            f"允许值：{sorted(ALLOWED_LICENSES)}。"
            "如确为已授权内容，请使用 licensed-official 并留存授权凭证编号。"
        )


def to_chunks(
    entries: Sequence[CorpusEntry],
    embedding_model: str,
    *,
    require_approved: bool = True,
    enforce_license: bool = True,
) -> list[KnowledgeChunk]:
    """转为可入库的片段，并在**入库前**完成全部校验。

    `knowledge-base.md` §2 的准入要求在此集中执行。校验失败即整体拒绝，
    不做部分写入——部分入库会让索引处于不一致状态。

    Args:
        entries: 语料记录。
        embedding_model: 目标embedding 模型标识。**所有片段必须一致**
            （§3：变更模型须完整重建索引，不可混用）。
        require_approved: 是否要求 ``APPROVED``。默认 ``True``。
            仅在准备草稿索引做内部预览时可设 ``False``——
            但那种索引**不得用于生产检索**。
        enforce_license: 是否校验许可白名单。默认 ``True``，
            **不建议关闭**。

    Returns:
        可直接传给 :meth:`~app.rag.store.KnowledgeStore.add` 的片段列表。

    Raises:
        InvalidDocumentError: 任一记录不合规（此时**不产出任何片段**）。
    """
    if not embedding_model.strip():
        raise InvalidDocumentError(
            "必须指定 embedding 模型标识（§3 要求记录），否则无法判断向量能否复用。"
        )
    if not entries:
        raise InvalidDocumentError("语料为空，无法构建索引。")

    seen: dict[str, str] = {}
    validated: list[KnowledgeChunk] = []

    for entry in entries:
        # 1) 重复 chunk_id 检查（同一索引内必须唯一）
        if entry.chunk_id in seen:
            raise InvalidDocumentError(
                f"chunk_id 重复：{entry.chunk_id!r}"
                f"（已出现于 {seen[entry.chunk_id]!r}）。"
                "重复 id 会导致向量覆盖，来源可追溯性失效。"
            )
        seen[entry.chunk_id] = entry.text[:40]

        # 2) 必填来源字段（§2）
        missing = entry.source.missing_required_fields()
        if missing:
            raise InvalidDocumentError(
                f"{entry.chunk_id} 来源元数据缺失：{'、'.join(missing)}。"
                "无来源信息的内容不得进入生产索引。"
            )

        # 3) 许可白名单（版权合规硬闸门）
        if enforce_license:
            check_license(entry)

        # 4) 审核状态（§3）
        if require_approved and not entry.source.status.is_retrievable:
            raise InvalidDocumentError(
                f"{entry.chunk_id} 审核状态为「{entry.source.status.value}」，"
                "仅已批准内容可进入生产索引。"
            )

        # 5) 课程范围（§4）
        if not entry.source.scope.is_in_scope:
            raise InvalidDocumentError(
                f"{entry.chunk_id} 课程范围为「{entry.source.scope.value}」，"
                "超出高中课程范围的内容不得进入生产索引。"
            )

        validated.append(
            KnowledgeChunk(
                chunk_id=entry.chunk_id,
                text=entry.text,
                source=entry.source,
                embedding_model=embedding_model,
            )
        )

    return validated


def corpus_stats(chunks: Sequence[KnowledgeChunk]) -> dict[str, Any]:
    """统计语料构成，供人工核对覆盖情况。

    输出供人判断，**不设合格线**——覆盖是否充分是教研判断
    （`product-scope.md` §7）。
    """
    topics: dict[str, int] = {}
    sources: dict[str, int] = {}
    scopes: dict[str, int] = {}
    total_chars = 0

    for c in chunks:
        topics[c.source.topic] = topics.get(c.source.topic, 0) + 1
        sources[c.source.source_id] = sources.get(c.source.source_id, 0) + 1
        scopes[c.source.scope.value] = scopes.get(c.source.scope.value, 0) + 1
        total_chars += len(c.text)

    return {
        "chunk_count": len(chunks),
        "source_count": len(sources),
        "topic_count": len(topics),
        "total_chars": total_chars,
        "avg_chars": total_chars // len(chunks) if chunks else 0,
        "by_topic": dict(sorted(topics.items())),
        "by_source": dict(sorted(sources.items())),
        "by_scope": dict(sorted(scopes.items())),
        "embedding_models": sorted({c.embedding_model for c in chunks}),
    }


__all__ = [
    "ALLOWED_LICENSES",
    "CORPUS_SCHEMA",
    "CorpusEntry",
    "check_license",
    "compute_content_hash",
    "corpus_stats",
    "load_corpus",
    "to_chunks",
]