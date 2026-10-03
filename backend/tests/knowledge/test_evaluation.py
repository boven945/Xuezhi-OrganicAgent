"""检索质量评估的测试。

关键取向：**不预设合格线**。测试验证计算是否正确，
不验证"指标够不够高"——后者须由化学教师依实测批准
（`product-scope.md` §7）。
"""

from __future__ import annotations

import pytest

from app.knowledge.evaluation import (
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

MODEL_ID = "BAAI/bge-small-zh"


def _case(query: str = "酯化反应", relevant: tuple[str, ...] = ("c1",)) -> EvalCase:
    return EvalCase(query=query, relevant_ids=frozenset(relevant))


def _run(query: str = "酯化反应", retrieved: tuple[str, ...] = ("c1",)) -> RetrievalRun:
    return RetrievalRun(query=query, retrieved_ids=retrieved)


class TestEvalCase:
    def test_rejects_empty_query(self):
        with pytest.raises(ValueError):
            EvalCase(query="  ", relevant_ids=frozenset({"c1"}))

    def test_rejects_empty_relevant(self):
        """没有标注相关文档的样本无法评估召回，必须拒绝。"""
        with pytest.raises(ValueError):
            EvalCase(query="酯化反应", relevant_ids=frozenset())

    def test_note_does_not_affect_evaluation(self):
        a = EvalCase(query="q", relevant_ids=frozenset({"c1"}), note="备注")
        b = EvalCase(query="q", relevant_ids=frozenset({"c1"}))
        assert a.relevant_ids == b.relevant_ids


class TestRecall:
    def test_perfect_recall(self):
        assert recall_at_k(_case(), _run(retrieved=("c1", "x", "y")), k=3) == 1.0

    def test_partial_recall(self):
        case = _case(relevant=("c1", "c2"))
        run = _run(retrieved=("c1", "x"))
        assert recall_at_k(case, run, k=3) == 0.5

    def test_zero_recall(self):
        assert recall_at_k(_case(), _run(retrieved=("x", "y")), k=2) == 0.0

    def test_only_top_k_counted(self):
        """K 之外的结果不计入召回。"""
        run = _run(retrieved=("x", "y", "z", "c1"))
        assert recall_at_k(_case(), run, k=2) == 0.0

    def test_rejects_invalid_k(self):
        for bad in (0, -1):
            with pytest.raises(ValueError):
                recall_at_k(_case(), _run(), k=bad)
            with pytest.raises(ValueError):
                precision_at_k(_case(), _run(), k=bad)
            with pytest.raises(ValueError):
                hit_rate_at_k(_case(), _run(), k=bad)
            with pytest.raises(ValueError):
                reciprocal_rank(_case(), _run(), k=bad)
            with pytest.raises(ValueError):
                ndcg_at_k(_case(), _run(), k=bad)


class TestPrecision:
    def test_perfect(self):
        assert precision_at_k(_case(), _run(retrieved=("c1", "x")), k=2) == 0.5

    def test_denominator_is_k_not_returned_count(self):
        """分母必须是 K 而非实际返回数。

        否则少返回反而显得更精准，是错误的激励方向。
        实测确认：只返回 1 条命中时，precision@3 应为 1/3 而非 1.0。
        """
        run = _run(retrieved=("c1",))
        assert precision_at_k(_case(), run, k=3) == pytest.approx(1 / 3)


class TestHitRate:
    def test_hit(self):
        assert hit_rate_at_k(_case(), _run(retrieved=("x", "c1")), k=2) == 1.0

    def test_miss(self):
        assert hit_rate_at_k(_case(), _run(retrieved=("x", "y")), k=2) == 0.0


class TestReciprocalRank:
    def test_first_position(self):
        assert reciprocal_rank(_case(), _run(retrieved=("c1", "x")), k=3) == 1.0

    def test_second_position(self):
        assert reciprocal_rank(_case(), _run(retrieved=("x", "c1")), k=3) == 0.5

    def test_third_position(self):
        assert reciprocal_rank(_case(), _run(retrieved=("x", "y", "c1")), k=3) == pytest.approx(1 / 3)

    def test_beyond_k_is_zero(self):
        """超出 K 未命中记0，而非按全列表算分。"""
        assert reciprocal_rank(_case(), _run(retrieved=("x", "y", "y", "c1")), k=2) == 0.0

    def test_takes_first_relevant_not_any(self):
        """取**首个**相关文档的位置，不取最好的那个。"""
        run = _run(retrieved=("x", "c1", "c2"))
        assert reciprocal_rank(_case(relevant=("c1", "c2")), run, k=3) == 0.5


class TestNdcg:
    def test_perfect_ranking(self):
        assert ndcg_at_k(_case(), _run(retrieved=("c1",)), k=3) == pytest.approx(1.0)

    def test_poor_ranking_lower_than_good(self):
        case = _case(relevant=("c1", "c2"))
        good = ndcg_at_k(case, _run(retrieved=("c1", "c2", "x")), k=3)
        poor = ndcg_at_k(case, _run(retrieved=("x", "y", "c1")), k=3)
        assert good > poor

    def test_no_relevant_returned(self):
        assert ndcg_at_k(_case(), _run(retrieved=("x", "y")), k=2) == 0.0

    def test_within_k_only(self):
        assert ndcg_at_k(_case(), _run(retrieved=("x", "y", "c1")), k=2) == 0.0


class TestEvaluate:
    def test_aggregates_correctly(self):
        cases = [
            _case("q1", ("c1",)),
            _case("q2", ("c2",)),
        ]
        runs = [
            _run("q1", ("c1",)),  # 全命中
            _run("q2", ("x",)),  # 未命中
        ]
        m = evaluate(cases, runs, k=3)
        assert m.recall_at_k == 0.5
        assert m.hit_rate_at_k == 0.5
        assert m.mrr == 0.5
        assert m.case_count == 2

    def test_empty_cases_returns_none_not_zero(self):
        """无样本时返回 None 而非 0。

        0 会被误读为"完全不命中"，"没数据"是另一回事——
        两者混淆会导致误判（与 KnowledgeStore.query 的阈值处理同理）。
        """
        m = evaluate([], [], k=5)
        assert m.recall_at_k is None
        assert m.mrr is None
        assert m.case_count == 0
        assert "N/A" in m.summary()

    def test_length_mismatch_rejected(self):
        with pytest.raises(ValueError) as exc:
            evaluate([_case("q1")], [], k=3)
        assert "数量不一致" in str(exc.value)

    def test_missing_run_counted_as_miss(self):
        """缺失的运行视为未命中，不静默跳过。

        跳过会让报告好看，掩盖"检索链路漏了一条"的问题。
        """
        cases = [_case("q1"), _case("q2")]
        runs = [_run("q1", ("c1",))]  # 数量不足会被拒
        with pytest.raises(ValueError):
            evaluate(cases, runs, k=3)

    def test_no_hit_run_counted_as_zero(self):
        """无命中的运行参与计算（不会被当成缺失跳过）。"""
        cases = [_case("q1"), _case("q2")]
        runs = [_run("q1", ("c1",)), _run("q2", ())]
        m = evaluate(cases, runs, k=3)
        assert m.hit_rate_at_k == 0.5

    def test_details_recorded_for_review(self):
        """逐条明细须保留，供定位失败样本（§6 要求可复核）。"""
        cases = [_case("q1", ("c1",)), _case("q2", ("c2",))]
        runs = [_run("q1", ("c1",)), _run("q2", ("x",))]
        m = evaluate(cases, runs, k=3)
        assert len(m.details) == 2
        assert m.details[0][1] == 1.0
        assert m.details[1][1] == 0.0

    def test_summary_human_readable(self):
        m = evaluate([_case()], [_run()], k=3)
        text = m.summary()
        assert "Recall@3" in text
        assert "MRR" in text


class _Chunk:
    """替身片段：只需 chunk_id 供评估器读取。"""

    def __init__(self, chunk_id: str) -> None:
        self.chunk_id = chunk_id


class _Result:
    def __init__(self, has_results: bool, chunks: list) -> None:
        self.has_results = has_results
        self.chunks = chunks


class _Store:
    """替身知识库：按查询返回预设的 chunk_id 列表。"""

    def __init__(self, mapping: dict) -> None:
        self.mapping = mapping
        self.calls: list[dict] = []

    def query(self, question, *, top_k=5, threshold=None, topic=None):
        self.calls.append({"q": question, "k": top_k, "thr": threshold})
        ids = self.mapping.get(question, ())
        chunks = [_Chunk(i) for i in ids]
        return _Result(bool(chunks), chunks)


class TestRunStoreEvaluation:
    def test_runs_all_cases(self):
        store = _Store({"酯化反应": ("c1",), "苯的结构": ("c2",)})
        cases = [_case("酯化反应", ("c1",)), _case("苯的结构", ("c2",))]
        m = run_store_evaluation(store, cases, k=5)
        assert m.hit_rate_at_k == 1.0
        assert len(store.calls) == 2

    def test_no_hit_recorded_as_empty_run(self):
        """无命中须记录为空运行，否则会被当成"缺失"而误导。"""
        store = _Store({"酯化反应": ()})
        m = run_store_evaluation(store, [_case("酯化反应", ("c1",))], k=5)
        assert m.hit_rate_at_k == 0.0

    def test_default_threshold_is_none(self):
        """缺省不过滤——阈值本身正是待评估对象。"""
        store = _Store({"酯化反应": ("c1",)})
        run_store_evaluation(store, [_case("酯化反应", ("c1",))], k=5)
        assert store.calls[0]["thr"] is None

    def test_empty_cases_rejected(self):
        with pytest.raises(ValueError) as exc:
            run_store_evaluation(_Store({}), [], k=5)
        assert "标注样本为空" in str(exc.value)

    def test_top_k_passed_through(self):
        store = _Store({"酯化反应": ("c1",)})
        run_store_evaluation(store, [_case("酯化反应", ("c1",))], k=3)
        assert store.calls[0]["k"] == 3


class TestSuggestThreshold:
    def _m(self, recall, precision=0.5, hit=0.9, mrr=0.7) -> RetrievalMetrics:
        return RetrievalMetrics(
            recall_at_k=recall,
            precision_at_k=precision,
            hit_rate_at_k=hit,
            mrr=mrr,
            ndcg_at_k=0.6,
            k=5,
            case_count=10,
        )

    def test_produces_comparison_table(self):
        result = suggest_threshold({0.3: self._m(0.9), 0.5: self._m(0.7)})
        assert len(result["table"]) == 2
        assert [r["threshold"] for r in result["table"]] == [0.3, 0.5]

    def test_does_not_recommend_threshold(self):
        """只做整理，不给推荐值——阈值是教学判断。"""
        result = suggest_threshold({0.3: self._m(0.9), 0.5: self._m(0.7)})
        assert "recommend" not in result
        assert "推荐阈值" in result["notice"]
        assert "不含推荐" in result["notice"]

    def test_none_metrics_shown_as_na_not_zero(self):
        result = suggest_threshold({0.3: self._m(None)})
        assert result["table"][0]["recall_str"] == "N/A"

    def test_observations_are_factual(self):
        result = suggest_threshold({0.3: self._m(0.9)})
        obs = result["observations"][0]
        assert "0.9000" in obs
        # 不应出现主观判断词
        for word in ("建议", "推荐", "最优", "应该"):
            assert word not in obs


class TestMetricsContract:
    def test_metrics_immutable(self):
        m = RetrievalMetrics(
            recall_at_k=0.5,
            precision_at_k=0.4,
            hit_rate_at_k=0.9,
            mrr=0.7,
            ndcg_at_k=0.6,
            k=5,
            case_count=10,
        )
        with pytest.raises((AttributeError, TypeError)):
            m.recall_at_k = 0.9  # type: ignore[misc]

    def test_run_immutable(self):
        r = _run()
        with pytest.raises((AttributeError, TypeError)):
            r.retrieved_ids = ("x",)  # type: ignore[misc]