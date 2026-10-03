"""检索质量评估。

对应 `knowledge-base.md` §6「检索排序与阈值须经标注问答集实测确定」。
本模块提供计算能力，**不提供及格线**——阈值须由化学教师依实测结果
与教学要求批准（`product-scope.md` §7），
代码里不写死任何数字。

指标定义参考 **ITU-T F.748.52 / 中国信通院《检索增强生成技术要求与
评估方法》**（2025-05 发布，检索能力维度）：

| 指标 | 含义 | 标准中的"优秀"参考 |
| --- | --- | --- |
| Recall@K | 前 K 个结果包含的相关文档比例 | ≥0.85 (K=5) |
| Precision@K | 前 K 个结果中相关文档的占比 | ≥0.80 (K=5) |
| MRR | 首个相关文档排名倒数的均值 | ≥0.80 |
| NDCG@K | 考虑相关性等级的排序质量 | ≥0.85 |
| Hit Rate@K | 前 K 个结果至少含1 个相关文档的查询占比 | ≥0.90 (K=5) |

⚠️ 上表**仅作参考记录，不作为本项目的验收线**。该标准的适用场景
（金融、医疗等严苛场景）与高中教学场景差异很大：教学问答的
"相关"判定本身带主观性，且**教学价值有时在于给出拓展内容而非
严格命中原句**。最终阈值须由项目负责人依实测批准（决策登记表 C5/C6）。

**为何必须实测而不能靠检索命中下结论**

FloTorch 2026 基准给出了关键证据：语义切分在*检索召回*上可达
91.9%，但*端到端答案准确率*只有 54%——因为产出的是平均 43 token 的
碎片，检索得到但上下文不足，模型答不出。

因此本模块区分两类评估：

- **检索层**（本模块）：Recall/MRR/NDCG 等，衡量"找到了没有、排第几"
- **端到端**（不在本模块）：答案是否正确、是否忠实于检索内容，
  须由教师人工核对或用 RAGAS 类工具

**只测检索层会得出过于乐观的结论**，这是本模块存在的意义。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence


@dataclass(frozen=True, slots=True)
class EvalCase:
    """一条标注评测样本。

    Attributes:
        query: 学生问题或检索查询。
        relevant_ids: 相关片段的 chunk_id 集合。
            **须由化学教师标注**（`knowledge-base.md` §6）。
        note: 标注说明。供争议复核，不参与计算。
    """

    query: str
    relevant_ids: frozenset[str]
    note: str = ""

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query 不得为空")
        if not self.relevant_ids:
            raise ValueError("relevant_ids 不得为空——否则该样本无法评估召回")


@dataclass(frozen=True, slots=True)
class RetrievalRun:
    """一次检索的实际返回。

    Attributes:
        query: 对应的查询，须与 :class:`EvalCase` 的 query 对应。
        retrieved_ids: 按相似度从高到低排列的 chunk_id。
    """

    query: str
    retrieved_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RetrievalMetrics:
    """一组评估的汇总结果。

    所有指标均为 ``None`` 表示无可评估样本——
    **不返回 0**。0 会被误读为"完全不命中"，而"没数据"是另一回事，
    两者混淆会导致误判（与 `KnowledgeStore.query` 的阈值处理同理）。
    """

    recall_at_k: float | None
    precision_at_k: float | None
    hit_rate_at_k: float | None
    mrr: float | None
    ndcg_at_k: float | None
    k: int
    case_count: int
    #: 逐条明细，供定位失败样本（`knowledge-base.md` §6 要求可复核）
    details: tuple[tuple[str, float], ...] = field(default=())

    def summary(self) -> str:
        """可读摘要。``None`` 显示为 ``N/A`` 而非 0。"""

        def fmt(v: float | None) -> str:
            return "N/A" if v is None else f"{v:.4f}"

        return (
            f"K={self.k} 样本数={self.case_count}\n"
            f"  Recall@{self.k}      {fmt(self.recall_at_k)}\n"
            f"  Precision@{self.k}   {fmt(self.precision_at_k)}\n"
            f"  Hit Rate@{self.k}    {fmt(self.hit_rate_at_k)}\n"
            f"  MRR                {fmt(self.mrr)}\n"
            f"  NDCG@{self.k}         {fmt(self.ndcg_at_k)}"
        )


def recall_at_k(case: EvalCase, run: RetrievalRun, k: int) -> float:
    """Recall@K：相关文档中被找回的比例。

    ``|retrieved ∩ relevant| / |relevant|``
    """
    if k <= 0:
        raise ValueError("k 必须大于 0")
    top = set(run.retrieved_ids[:k])
    return len(top & case.relevant_ids) / len(case.relevant_ids)


def precision_at_k(case: EvalCase, run: RetrievalRun, k: int) -> float:
    """Precision@K：返回结果中相关文档的占比。

    ``|retrieved ∩ relevant| / K``

    注意分母用 K 而非实际返回数：若只返回 3 条却按 3 计算，
    少返回反而会显得精准更高，是错误的激励方向。
    """
    if k <= 0:
        raise ValueError("k 必须大于 0")
    top = set(run.retrieved_ids[:k])
    return len(top & case.relevant_ids) / k


def hit_rate_at_k(case: EvalCase, run: RetrievalRun, k: int) -> float:
    """Hit Rate@K：是否至少命中一个相关文档（1 或 0）。"""
    if k <= 0:
        raise ValueError("k 必须大于 0")
    return 1.0 if set(run.retrieved_ids[:k]) & case.relevant_ids else 0.0


def reciprocal_rank(case: EvalCase, run: RetrievalRun, k: int) -> float:
    """RR：首个相关文档排名的倒数。命中前 K 则为 ``1/rank``，否则 0。"""
    if k <= 0:
        raise ValueError("k 必须大于 0")
    for rank, chunk_id in enumerate(run.retrieved_ids[:k], start=1):
        if chunk_id in case.relevant_ids:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(case: EvalCase, run: RetrievalRun, k: int) -> float:
    """NDCG@K：位置加权的排序质量。

    采用二元相关性（有/无相关），此时 DCG = Σ 1/log2(i+1)，
    IDCG = 理想情况（相关文档全部排在最前）的DCG。
    """
    if k <= 0:
        raise ValueError("k 必须大于 0")
    dcg = 0.0
    for rank, chunk_id in enumerate(run.retrieved_ids[:k], start=1):
        if chunk_id in case.relevant_ids:
            dcg += 1.0 / math.log2(rank + 1)

    n_rel = min(len(case.relevant_ids), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, n_rel + 1))
    return dcg / idcg if idcg > 0 else 0.0


def evaluate(
    cases: Sequence[EvalCase],
    runs: Sequence[RetrievalRun],
    *,
    k: int = 5,
) -> RetrievalMetrics:
    """评估一组检索结果。

    Args:
        cases: 标注样本。
        runs: 实际检索返回，数量须与``cases`` 一致。
        k: 截断位置。

    Returns:
        汇总指标。``cases`` 为空时所有指标为 ``None``。

    Raises:
        ValueError: ``cases`` 与 ``runs`` 数量不一致，或 ``k`` 非法。
    """
    if k <= 0:
        raise ValueError("k 必须大于 0")
    if len(cases) != len(runs):
        raise ValueError(
            f"cases 与 runs 数量不一致：{len(cases)} vs {len(runs)}"
        )
    if not cases:
        return RetrievalMetrics(
            recall_at_k=None,
            precision_at_k=None,
            hit_rate_at_k=None,
            mrr=None,
            ndcg_at_k=None,
            k=k,
            case_count=0,
        )

    runs_by_query = {r.query: r for r in runs}
    recall_sum = prec_sum = hit_sum = rr_sum = ndcg_sum = 0.0
    details: list[tuple[str, float]] = []

    for case in cases:
        run = runs_by_query.get(case.query)
        if run is None:
            # 缺失的运行视为完全未命中，而不是静默跳过——
            # 跳过会让报告好看，掩盖"检索链路漏了一条"的问题。
            details.append((case.query, 0.0))
            continue

        r_at_k = recall_at_k(case, run, k)
        recall_sum += r_at_k
        prec_sum += precision_at_k(case, run, k)
        hit_sum += hit_rate_at_k(case, run, k)
        rr_sum += reciprocal_rank(case, run, k)
        ndcg_sum += ndcg_at_k(case, run, k)
        details.append((case.query, r_at_k))

    n = len(cases)
    return RetrievalMetrics(
        recall_at_k=recall_sum / n,
        precision_at_k=prec_sum / n,
        hit_rate_at_k=hit_sum / n,
        mrr=rr_sum / n,
        ndcg_at_k=ndcg_sum / n,
        k=k,
        case_count=n,
        details=tuple(details),
    )


def run_store_evaluation(
    store: object,
    cases: Sequence[EvalCase],
    *,
    k: int = 5,
    threshold: float | None = None,
) -> RetrievalMetrics:
    """对真实 :class:`~app.rag.store.KnowledgeStore` 跑评估。

    **``threshold`` 缺省为 ``None``（不过滤）**。这不是"默认无阈值"，
    而是因为阈值本身正是待评估的对象——先用无过滤的基线数据
    看清召回上限，再据此讨论阈值取值（`knowledge-base.md` §5）。

    Args:
        store: 知识库存储。
        cases: 标注样本。
        k: 截断位置。
        threshold: 相似度阈值。``None`` 表示不按距离过滤。

    Returns:
        汇总指标。

    Raises:
        ValueError: ``cases`` 为空（无样本无法评估，不返回全 0 结果）。
    """
    if not cases:
        raise ValueError("标注样本为空，无法评估")

    runs: list[RetrievalRun] = []
    for case in cases:
        result = store.query(case.query, top_k=k, threshold=threshold)  # type: ignore[attr-defined]
        if result.has_results:
            runs.append(
                RetrievalRun(
                    query=case.query,
                    retrieved_ids=tuple(c.chunk_id for c in result.chunks),
                )
            )
        else:
            # 无命中也要记录，否则该样本会被 evaluate 当作"缺失运行"
            runs.append(RetrievalRun(query=case.query, retrieved_ids=()))

    return evaluate(cases, runs, k=k)


def suggest_threshold(
    metrics_by_threshold: dict[float, RetrievalMetrics],
) -> dict[str, object]:
    """从多组阈值实验结果中整理候选，供人工决策。

    ⚠️ **不自动选阈值**。`knowledge-base.md` §5 要求阈值由标注问答集
    实测确定，且须考虑教学场景的召回诉求与准确性诉求的权衡——
    这是教学判断，不是数值优化。本函数只做整理。

    Args:
        metrics_by_threshold: 阈值 → 对应的评估结果。

    Returns:
        含对比表与观察的字典。**不含推荐值**。
    """
    rows = []
    # 键可能含 None（表示"不按距离过滤"），不能直接 sorted——
    # 实测踩过：None 与 float 比较抛 TypeError。
    # 约定：None 排在最前（不过滤是基线，过滤是收紧）。
    def _sort_key(item: tuple[float | None, RetrievalMetrics]) -> tuple[int, float]:
        thr, _ = item
        return (0, 0.0) if thr is None else (1, thr)

    for key, m in sorted(metrics_by_threshold.items(), key=_sort_key):

        def fmt(v: float | None) -> str:
            return "N/A" if v is None else f"{v:.4f}"

        rows.append(
            {
                "threshold": key,
                "recall": m.recall_at_k,
                "precision": m.precision_at_k,
                "hit_rate": m.hit_rate_at_k,
                "mrr": m.mrr,
                "recall_str": fmt(m.recall_at_k),
                "precision_str": fmt(m.precision_at_k),
                "hit_rate_str": fmt(m.hit_rate_at_k),
                "mrr_str": fmt(m.mrr),
            }
        )

    observations: list[str] = []
    for row in rows:
        if row["recall"] is None:
            observations.append(f"阈值 {row['threshold']}：无评估数据")
            continue
        # 只陈述事实，不作优劣判断
        observations.append(
            f"阈值 {row['threshold']}："
            f"Recall@{rows[0]['threshold'] and ''}K={row['recall_str']}、"
            f"Precision={row['precision_str']}、"
            f"Hit Rate={row['hit_rate_str']}、"
            f"MRR={row['mrr_str']}"
        )

    return {
        "table": rows,
        "observations": observations,
        "notice": (
            "以上为实验记录，**不含推荐阈值**。"
            "阈值选择须结合教学要求与误召回代价，由项目负责人决定"
            "（knowledge-base.md §5、product-scope.md §7）。"
        ),
    }


__all__ = [
    "EvalCase",
    "RetrievalRun",
    "RetrievalMetrics",
    "evaluate",
    "hit_rate_at_k",
    "ndcg_at_k",
    "precision_at_k",
    "recall_at_k",
    "reciprocal_rank",
    "run_store_evaluation",
    "suggest_threshold",
]