"""开发期测试语料。

用途：为端到端验证提供最小可用的知识库内容。**不是生产教材内容**。

每条均按 `knowledge-base.md` §2 补齐来源元数据、§3 标记为已批准、
§4 限定在高中课程范围内。这三条是入库的硬性前置条件，
缺任何一项都会被 `KnowledgeStore.add` 拒绝。

内容取自高中有机化学的通识性表述，用于验证「检索 → 引用标注」链路是否
打通，**不作为教材原文的替代**。生产语料须由化学教研组提供并经审核
（`knowledge-base.md` §3）。

许可字段统一为 `internal-dev-only`，明确禁止进入生产索引。
"""

from __future__ import annotations

from app.rag.models import (
    KnowledgeChunk,
    KnowledgeSource,
    ScopeLevel,
    VerificationStatus,
)

#: 默认嵌入模型标识，仅供**离线测试**（配 :class:`~app.rag.store.HashEmbedding`）。
#:
#: ⚠️ 真实检索必须显式传入实际模型标识——`KnowledgeStore.add` 会校验
#: 片段声明的模型与 store 配置是否一致，不一致即拒绝（`knowledge-base.md` §3：
#: 变更 embedding 模型须完整重建索引）。实测中正因写死此处而使 bge 场景下
#: 入库被拒，故改为参数化。
DEVELOPMENT_EMBEDDING_MODEL = "hash-test-embedding-v1"

_SOURCE = "dev-corpus-001"

_SOURCES: dict[str, dict[str, str]] = {
    "esterification": {
        "locator": "第六章 第三节 酯化反应",
        "topic": "酯化反应",
    },
    "saponification": {
        "locator": "第六章 第四节 油脂的水解",
        "topic": "酯化反应",
    },
    "nucleophilic_substitution": {
        "locator": "第五章 第三节 卤代烃的水解",
        "topic": "取代反应",
    },
    "benzene_structure": {
        "locator": "第三章 第二节 苯",
        "topic": "芳香烃",
    },
    "ethanol": {
        "locator": "第三章 第一节 乙醇",
        "topic": "醇",
    },
    "aldol_reaction_note": {
        "locator": "拓展阅读 醛的加成",
        "topic": "醛酮",
    },
}


def _make_source(key: str) -> KnowledgeSource:
    meta = _SOURCES[key]
    return KnowledgeSource(
        source_id=_SOURCE,
        title="高中有机化学要点（开发期测试语料）",
        publisher="项目组自编",
        edition="2026-10 开发版",
        curriculum_level="高中必修 + 选择性必修",
        topic=meta["topic"],
        locator=meta["locator"],
        license="internal-dev-only",
        reviewer="待化学教研组审核",
        reviewed_at="2026-10-04",
        status=VerificationStatus.APPROVED,
        # 醛的加成属高中范围内拓展，按 §4 须显式标注，不得混入基础答案
        scope=(
            ScopeLevel.HIGH_SCHOOL_EXTENDED
            if key == "aldol_reaction_note"
            else ScopeLevel.HIGH_SCHOOL_REQUIRED
        ),
        content_hash=f"devhash-{key}",
    )


_TEXTS: dict[str, str] = {
    "esterification": (
        "酯化反应是羧酸与醇在酸性条件下反应生成酯和水的反应。"
        "浓硫酸作催化剂并吸水，生成的酯通常有果香味。"
        "该反应属于取代反应，酸与醇分子分别脱去羟基和氢原子。"
    ),
    "saponification": (
        "油脂在碱性条件下水解生成高级脂肪酸盐和甘油，这个过程称为皂化反应。"
        "皂化反应是酯的水解反应，属于取代反应。"
    ),
    "nucleophilic_substitution": (
        "卤代烃在水溶液中发生水解反应，卤原子被羟基取代，生成醇。"
        "该反应属于取代反应，条件是加热并在水溶液中进行。"
    ),
    "benzene_structure": (
        "苯的分子式是 C6H6，六个碳原子构成平面正六边形，"
        "所有碳碳键长相等，属于芳香烃，化学性质比烯烃稳定。"
    ),
    "ethanol": (
        "乙醇的分子式是 C2H6O，结构简式为 CH3CH2OH，"
        "含羟基，属于醇类化合物，能与金属钠反应放出氢气。"
    ),
    "aldol_reaction_note": (
        "拓展内容：醛类化合物可发生加成反应。高中课程不作深入要求，"
        "仅在拓展阅读中提及。"
    ),
}


def build_development_chunks(
    embedding_model: str = DEVELOPMENT_EMBEDDING_MODEL,
) -> list[KnowledgeChunk]:
    """构造开发期测试片段。

    Args:
        embedding_model: 片段声明的嵌入模型标识。**必须与运行时 store 的
            配置一致**，否则入库会被拒绝（`knowledge-base.md` §3）。
            离线测试用默认的 hash 模型；真实检索须传实际模型的标识
            （实测 ``SentenceTransformerEmbedding.model_id`` 是**实例属性**，
            无类级常量，须在构造实例后读取）。

    Returns:
        全部为 ``APPROVED`` 状态、课程范围内的片段。
    """
    if not embedding_model.strip():
        raise ValueError("embedding_model 不得为空（§3 要求记录模型标识）")
    return [
        KnowledgeChunk(
            chunk_id=f"dev-{key}",
            text=text,
            source=_make_source(key),
            embedding_model=embedding_model,
        )
        for key, text in _TEXTS.items()
    ]


#: 端到端验证用的探针问题 -> 期望命中的 chunk key。
#: 供 `backend/tests/agent/` 的真实链路验证使用（须设置环境变量
#: `XUEZHI_RUN_E2E_TESTS=1`，因其依赖真实模型权重）。
E2E_PROBES: tuple[tuple[str, str], ...] = (
    ("酯化反应是什么", "esterification"),
    ("油脂在碱性条件下会发生什么反应", "saponification"),
    ("苯的分子式是什么", "benzene_structure"),
    ("乙醇的结构简式", "ethanol"),
)


__all__ = [
    "build_development_chunks",
    "DEVELOPMENT_EMBEDDING_MODEL",
    "E2E_PROBES",
]