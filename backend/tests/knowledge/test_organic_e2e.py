"""真实检索实测：高中有机化学语料（41 条）。

用真实 bge-small-zh + chromadb，检验自编讲义的检索效果。
这是链路验证，**不是质量结论**——正式评估须由化学教师
用标注问答集做（`knowledge-base.md` §6 / H18）。

运行：
```bash
docker run --rm -e XUEZHI_RUN_MODEL_TESTS=1 xuezhi-chem-test \
  python -m pytest backend/tests/knowledge/test_organic_e2e.py -v -s
```
"""

from __future__ import annotations

import json
import os

import pytest

from app.knowledge import build_index, load_corpus_dir
from app.rag.errors import InvalidDocumentError

requires_e2e = pytest.mark.skipif(
    os.environ.get("XUEZHI_RUN_MODEL_TESTS") != "1",
    reason="需 XUEZHI_RUN_MODEL_TESTS=1（依赖模型权重下载）",
)

CORPUS_DIR = "/work/data/knowledge/organic"
#: 评估结果输出路径。**容器内写文件会随容器销毁而丢失**，
#: 因此支持用环境变量指向宿主机挂载目录（见文件末尾的复现命令）。
OUTPUT = os.environ.get(
    "XUEZHI_EVAL_OUTPUT", "/work/data/eval/organic-retrieval.json"
)

#: 检索探针。``expected`` 为期望命中的 chunk_id 关键词（任一命中即算）。
#: 标注由项目组依据课标整理，**未经化学教师审核**，仅用于链路验证。
PROBES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # 烃类
    ("烷烃有什么化学性质", ("org-alkane",)),
    ("乙烯和乙炔有什么加成反应", ("org-alkene", "org-alkyne")),
    ("苯的结构有什么特点", ("org-arene-structure",)),
    ("苯的硝化反应条件是什么", ("org-arene-substitution",)),
    ("苯为什么不能使溴水褪色", ("org-arene-extraction-trap",)),
    ("甲苯和苯的化学性质有什么区别", ("org-arene-homologue",)),
    ("烷烃的取代反应怎么进行", ("org-alkane-reaction",)),
    ("不饱和度怎么算", ("org-unsaturation", "org-structure-unsaturation")),
    # 烃的衍生物
    ("卤代烃的水解和消去反应有什么不同", ("org-haloalkane",)),
    ("乙醇有哪些化学性质", ("org-ethanol-properties",)),
    ("醇的催化氧化怎么判断产物", ("org-alcohol-oxidation",)),
    ("苯酚的酸性有多强", ("org-phenol",)),
    ("醇和酚的区别是什么", ("org-alcohol-phenol-difference",)),
    ("银镜反应怎么检验醛基", ("org-aldehyde", "org-exp-silver-mirror")),
    ("醛和酮有什么区别", ("org-ketone", "org-aldehyde")),
    ("乙酸的酸性有多强", ("org-carboxylic-acid",)),
    ("酯化反应的机理是什么", ("org-esterification",)),
    ("酯的水解在酸碱条件下产物有什么不同", ("org-ester-hydrolysis",)),
    ("皂化反应是什么", ("org-fat-oil", "org-ester-hydrolysis")),
    ("高中有机反应有哪些类型", ("org-reaction-types",)),
    # 实验
    ("乙烯实验室怎么制取", ("org-exp-ethene",)),
    ("硝基苯怎么制取", ("org-exp-nitrobenzene",)),
    ("有机物怎么分离提纯", ("org-exp-separation",)),
    ("怎么鉴别羧酸和苯酚", ("org-exp-identification",)),
    # 高分子与生物大分子
    ("淀粉和纤维素有什么区别", ("org-carbohydrate",)),
    ("蛋白质为什么变性", ("org-amino-acid-protein",)),
    ("加聚反应和缩聚反应的区别", ("org-synthetic-polymer",)),
    ("有机合成路线怎么设计", ("org-synthesis-strategy",)),
    ("绿色化学的核心是什么", ("org-green-chemistry",)),
    # 综合
    ("烃及其衍生物怎么相互转化", ("org-conversion",)),
    ("官能团有哪些特征反应", ("org-functional-group",)),
    ("有机物怎么命名", ("org-structure-naming", "org-functional-group")),
    ("什么是同分异构体", ("org-structure-isomerism", "org-unsaturation")),
)


@pytest.fixture(scope="module")
def organic_store(tmp_path_factory):
    """真实索引 + store 实例。"""
    from app.rag.embeddings import SentenceTransformerEmbedding
    from app.rag.store import build_store

    embedding = SentenceTransformerEmbedding()
    persist = str(tmp_path_factory.mktemp("organic_store"))
    report = build_index(
        persist,
        CORPUS_DIR,
        embedding,
        collection_name="organic_main",
        index_version="organic-2026.10",
    )
    store = build_store(
        persist, "organic_main", embedding, index_version="organic-2026.10"
    )
    return store, report


class TestCorpusComplianceRequiresE2E:
    """语料合规性——版权闸门是本模块的核心约束。"""

    @requires_e2e
    def test_all_entries_pass_admission(self):
        chunks = load_corpus_dir(CORPUS_DIR, "BAAI/bge-small-zh")
        assert len(chunks) >= 40, f"语料仅 {len(chunks)} 条，覆盖不足"

    @requires_e2e
    def test_no_unlicensed_content(self):
        """全部内容必须有明确许可，且不得是未确认状态。"""
        chunks = load_corpus_dir(CORPUS_DIR, "BAAI/bge-small-zh")
        for c in chunks:
            assert c.source.license == "project-authored", (
                f"{c.chunk_id} 许可为 {c.source.license!r}，"
                "自编讲义应统一标记 project-authored"
            )

    @requires_e2e
    def test_all_in_scope(self):
        """全部内容须在高中课程范围内。"""
        chunks = load_corpus_dir(CORPUS_DIR, "BAAI/bge-small-zh")
        for c in chunks:
            assert c.source.scope.is_in_scope

    @requires_e2e
    def test_single_embedding_model(self):
        """跨文件必须同一 embedding 模型（§3）。"""
        chunks = load_corpus_dir(CORPUS_DIR, "BAAI/bge-small-zh")
        assert len({c.embedding_model for c in chunks}) == 1

    @requires_e2e
    def test_no_duplicate_ids(self):
        """跨文件 id 重复须被拒绝（由 load_corpus_dir 保证）。"""
        chunks = load_corpus_dir(CORPUS_DIR, "BAAI/bge-small-zh")
        ids = [c.chunk_id for c in chunks]
        assert len(ids) == len(set(ids))

    @requires_e2e
    def test_topic_coverage(self, organic_store):
        """课标四大模块都须有覆盖。"""
        _, report = organic_store
        topics = report["by_topic"]
        required = ["有机物的组成与结构", "烷烃", "烯烃", "芳香烃", "卤代烃", "醇", "酚", "醛 酮", "羧酸", "酯", "生物大分子", "合成高分子"]
        for t in required:
            assert t in topics, f"缺少主题：{t}"

    def test_duplicate_across_files_rejected(self, tmp_path):
        """构造跨文件重复 id，验证确实被拒绝。"""
        import json

        source = {
            "source_id": "dup-test",
            "title": "重复测试",
            "publisher": "项目组",
            "edition": "t",
            "curriculum_level": "高中必修",
            "topic": "测试",
            "license": "project-authored",
            "reviewer": "t",
            "reviewed_at": "2026-10-04",
            "status": "approved",
            "scope": "high_school_required",
        }
        payload = {
            "schema": "knowledge-corpus/v1",
            "entries": [{"chunk_id": "same-id", "text": "内容甲", "source": source}],
        }
        payload2 = {
            "schema": "knowledge-corpus/v1",
            "entries": [{"chunk_id": "same-id", "text": "内容乙", "source": source}],
        }
        (tmp_path / "a.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        (tmp_path / "b.json").write_text(
            json.dumps(payload2, ensure_ascii=False), encoding="utf-8"
        )
        with pytest.raises(InvalidDocumentError) as exc:
            load_corpus_dir(str(tmp_path), "BAAI/bge-small-zh")
        assert "重复" in exc.value.user_message


class TestRetrievalRequiresE2E:
    """真实检索效果。"""

    @requires_e2e
    def test_index_built(self, organic_store):
        store, report = organic_store
        assert report["written"] >= 40
        assert store.count() >= 40

    @requires_e2e
    def test_probes_and_export(self, organic_store):
        store, report = organic_store

        # 逐条跑并记录实际命中
        details = []
        hit_count = 0
        for query, expected in PROBES:
            r = store.query(query, top_k=3)
            ids = [c.chunk_id for c in r.chunks] if r.has_results else []
            hit = any(any(e in i for e in expected) for i in ids)
            top1 = ids[0] if ids else "(无)"
            ok = any(e in top1 for e in expected)
            if hit:
                hit_count += 1
            details.append(
                {
                    "query": query,
                    "top1": top1,
                    "top1_correct": ok,
                    "top3": ids,
                    "hit_any": hit,
                }
            )
            print(f"  {'OK ' if ok else ('HIT' if hit else 'MISS')} {query[:22]:24} -> {top1}")

        print(f"\n首位正确 {sum(1 for d in details if d['top1_correct'])}/{len(details)}"
              f"  命中任意 {hit_count}/{len(details)}")

        # 多阈值对比——语料从 6 条增至 41 条，阈值效果应开始显现
        by_threshold = {}
        for thr in (None, 0.4, 0.5, 0.6, 0.7):
            hits = 0
            correct = 0
            for query, expected in PROBES:
                r = store.query(query, top_k=3, threshold=thr)
                ids = [c.chunk_id for c in r.chunks] if r.has_results else []
                if any(any(e in i for e in expected) for i in ids):
                    hits += 1
                if ids and any(e in ids[0] for e in expected):
                    correct += 1
            by_threshold[thr] = hits / len(PROBES)
        print("\n阈值 vs 命中率:")
        for thr, rate in by_threshold.items():
            print(f"  thr={thr}: 命中率={rate:.4f}")

        import os as _os

        _os.makedirs(_os.path.dirname(OUTPUT) or ".", exist_ok=True)
        with open(OUTPUT, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "note": (
                        "自编讲义的检索链路验证。探针标注由项目组整理，"
                        "未经化学教师审核，不构成质量结论。"
                    ),
                    "chunk_count": report["written"],
                    "topic_count": report["topic_count"],
                    "probe_count": len(PROBES),
                    "top1_correct": sum(1 for d in details if d["top1_correct"]),
                    "hit_any": hit_count,
                    "hit_rate_by_threshold": {
                        str(k): v for k, v in by_threshold.items()
                    },
                    "details": details,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        assert hit_count >= len(PROBES) * 0.8, (
            f"命中率 {hit_count}/{len(PROBES)} 偏低，需检查语料或模型"
        )

    @requires_e2e
    def test_offtopic_returns_no_result(self, organic_store):
        """超纲问题应无命中。"""
        store, _ = organic_store
        r = store.query("如何申请国家自然科学基金课题", top_k=3)
        payload = json.loads(r.to_model_payload())
        if not payload["found"]:
            assert "未找到" in payload["message"]

    @requires_e2e
    def test_results_carry_source(self, organic_store):
        """结果须携带完整来源，便于模型标注引用。"""
        store, _ = organic_store
        r = store.query("酯化反应的条件是什么", top_k=2)
        assert r.has_results
        for c in r.chunks:
            assert c.source.source_id
            assert c.source.title == "高中有机化学自编讲义"
            assert c.source.locator
            assert c.source.reviewer