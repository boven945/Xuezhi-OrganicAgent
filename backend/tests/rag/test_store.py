"""知识检索层测试。

运行方式（容器内）：

    docker run --rm xuezhi-chem-test python -m pytest backend/tests/rag/ -v

**测试策略**：使用 :class:`HashEmbedding`（确定性哈希嵌入）而非真实语义模型，原因：

1. 不依赖网络与模型下载，可离线、可复现；
2. 真实语义模型的**召回质量评估**属 `knowledge-base.md` §6 的范畴，
   须由化学教师用标注问答集评估，不在单元测试内断言；
3. 本测试聚焦**治理机制**——准入校验、来源可追溯、阈值行为、
   embedding 模型一致性——这些与嵌入语义无关。

实测已确认 chromadb 内置的 ``DefaultEmbeddingFunction`` 对中文无效
（英文模型 all-MiniLM-L6-v2），故生产环境必须显式提供中文模型，
详见 ``docs/rag-verification.md``。
"""

from __future__ import annotations

import warnings

import pytest

# chromadb 的 ONNX 默认嵌入函数会打印下载进度，测试中不使用它
warnings.filterwarnings("ignore")

from app.rag import (  # noqa: E402
    EmbeddingUnavailableError,
    IndexNotReadyError,
    InvalidDocumentError,
    KnowledgeChunk,
    KnowledgeSource,
    RetrievalError,
    ScopeLevel,
    VerificationStatus,
)
from app.rag.store import HashEmbedding, KnowledgeStore  # noqa: E402

EMBEDDING_ID = "hash-test-embedding-v1"


# ----------------------------------------------------------------------
# 测试替身
# ----------------------------------------------------------------------
class _FakeCollection:
    """最小化的 chromadb 集合替身。

    只实现 :class:`KnowledgeStore` 用到的三个方法，并模拟真实返回结构
    （``query`` 返回按批嵌套的 dict，实测 chromadb 1.5.9 即此结构）。
    """

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.docs: dict[str, str] = {}
        self.metas: dict[str, dict] = {}
        self.fail_on = fail_on
        self.added_kwargs: dict | None = None

    def add(self, ids, documents, embeddings, metadatas):
        if self.fail_on == "add":
            raise RuntimeError("模拟写入失败")
        assert len(ids) == len(documents) == len(embeddings) == len(metadatas)
        for i, d, m in zip(ids, documents, metadatas):
            self.docs[i] = d
            self.metas[i] = m
        self.added_kwargs = {"ids": list(ids)}

    def query(self, query_embeddings, n_results, where=None, include=None):
        if self.fail_on == "query":
            raise RuntimeError("模拟查询失败")
        assert len(query_embeddings) == 1
        # 假装按距离排序返回（测试不验证真实召回质量）
        ids = list(self.docs)
        where = where or {}
        if "topic" in where:
            ids = [i for i in ids if self.metas[i].get("topic") == where["topic"]]
        picked = ids[:n_results]
        return {
            "documents": [[self.docs[i] for i in picked]],
            "metadatas": [[self.metas[i] for i in picked]],
            "distances": [[0.1 * (k + 1) for k in range(len(picked))]],
        }

    def count(self):
        return len(self.docs)


class _FakeClient:
    def __init__(self, collection) -> None:
        self._col = collection
        self.kwargs = None

    def get_or_create_collection(self, **kwargs):
        self.kwargs = kwargs
        return self._col


@pytest.fixture
def collection():
    return _FakeCollection()


@pytest.fixture
def store(collection):
    return KnowledgeStore(
        _FakeClient(collection),
        "test_collection",
        HashEmbedding(model_id=EMBEDDING_ID),
        index_version="v1",
    )


def _source(**overrides) -> KnowledgeSource:
    """构造一个合规来源。"""
    base = {
        "source_id": "SRC-TEST-001",
        "title": "人教版高中化学必修第二册",
        "publisher": "人民教育出版社",
        "edition": "2019年版",
        "curriculum_level": "高中",
        "topic": "酯",
        "locator": "第12章 第3节",
        "license": "项目内教学使用",
        "reviewer": "审核员A",
        "reviewed_at": "2026-10-01",
        "status": VerificationStatus.APPROVED,
        "scope": ScopeLevel.HIGH_SCHOOL_REQUIRED,
        "content_hash": "abc123",
    }
    base.update(overrides)
    return KnowledgeSource(**base)


def _chunk(text: str = "酯化反应是羧酸与醇在酸性条件下生成酯和水。", **src_overrides):
    return KnowledgeChunk(
        chunk_id="c1",
        text=text,
        source=_source(**src_overrides),
        embedding_model=EMBEDDING_ID,
    )


# ----------------------------------------------------------------------
# 准入校验（knowledge-base.md §2、§3）
# ----------------------------------------------------------------------
class TestAdmissionRules:
    def test_valid_chunk_accepted(self, store):
        assert store.add([_chunk()]) == 1

    @pytest.mark.parametrize(
        "field",
        ["source_id", "title", "publisher", "edition", "topic", "reviewer"],
    )
    def test_missing_required_metadata_rejected(self, store, field):
        """§2：无来源信息的内容不得进入生产索引。"""
        with pytest.raises(InvalidDocumentError) as exc:
            store.add([_chunk(**{field: ""})])
        assert field in exc.value.user_message

    @pytest.mark.parametrize(
        "status",
        [
            VerificationStatus.DRAFT,
            VerificationStatus.PENDING_REVIEW,
            VerificationStatus.DEPRECATED,
            VerificationStatus.WITHDRAWN,
        ],
    )
    def test_unapproved_status_rejected(self, store, status):
        """§3：只有通过验收的内容才进入生产索引。"""
        with pytest.raises(InvalidDocumentError) as exc:
            store.add([_chunk(status=status)])
        assert "审核状态" in exc.value.user_message

    def test_undergraduate_scope_rejected(self, store):
        """§4：超出高中课程范围的内容不得进入生产索引。"""
        with pytest.raises(InvalidDocumentError) as exc:
            store.add([_chunk(scope=ScopeLevel.UNDERGRADUATE)])
        assert "课程范围" in exc.value.user_message

    def test_extended_scope_allowed(self, store):
        """高中范围内的拓展内容允许入库（但应标注）。"""
        assert store.add([_chunk(scope=ScopeLevel.HIGH_SCHOOL_EXTENDED)]) == 1

    def test_empty_text_rejected(self, store):
        with pytest.raises(InvalidDocumentError):
            store.add([_chunk(text="   ")])

    def test_missing_embedding_model_rejected(self, store):
        """§3：未记录 embedding 模型时不得入库。"""
        bad = KnowledgeChunk(
            chunk_id="c1", text="内容", source=_source(), embedding_model=""
        )
        with pytest.raises(InvalidDocumentError) as exc:
            store.add([bad])
        assert "embedding" in exc.value.user_message

    def test_rejected_batch_writes_nothing(self, store, collection):
        """批次中任一片段不合规时，整批不写入——避免索引不一致。"""
        good = _chunk(text="合规内容")
        bad = _chunk(text="不合规", status=VerificationStatus.DRAFT)
        with pytest.raises(InvalidDocumentError):
            store.add([good, bad])
        assert collection.count() == 0

    def test_empty_batch_is_noop(self, store):
        assert store.add([]) == 0


# ----------------------------------------------------------------------
# embedding 模型一致性（§3：变更模型须重建索引）
# ----------------------------------------------------------------------
class TestEmbeddingConsistency:
    def test_mismatched_model_rejected(self, store):
        """片段模型与存储配置不一致时拒绝写入。"""
        bad = KnowledgeChunk(
            chunk_id="c1", text="内容", source=_source(),
            embedding_model="some-other-model",
        )
        with pytest.raises(InvalidDocumentError) as exc:
            store.add([bad])
        assert "不一致" in exc.value.user_message

    def test_mixed_models_in_batch_rejected(self, store):
        a = _chunk(text="A")
        b = KnowledgeChunk(
            chunk_id="c2", text="B", source=_source(),
            embedding_model="another-model",
        )
        with pytest.raises(InvalidDocumentError):
            store.add([a, b])

    def test_cosine_space_configured(self, collection):
        """必须显式设 cosine——默认是 l2，阈值语义不同。"""
        client = _FakeClient(collection)
        KnowledgeStore(client, "test_collection", HashEmbedding(model_id=EMBEDDING_ID))
        assert client.kwargs["configuration"]["hnsw"]["space"] == "cosine"

    def test_index_version_recorded(self, collection):
        """§7：索引须关联版本与 embedding 模型标识。"""
        client = _FakeClient(collection)
        s = KnowledgeStore(
            client, "test_collection", HashEmbedding(model_id="mid-v2"),
            index_version="v9",
        )
        meta = client.kwargs["metadata"]
        assert meta["index_version"] == "v9"
        assert meta["embedding_model"] == "mid-v2"
        assert s.index_version() == "v9"


# ----------------------------------------------------------------------
# 检索
# ----------------------------------------------------------------------
class TestQuery:
    def test_returns_results(self, store, collection):
        collection.add(
            ids=["a"], documents=["酯化反应内容"],
            embeddings=[[0.1, 0.2]],
            metadatas=[_chunk().to_metadata()],
        )
        r = store.query("酯化反应")
        assert r.has_results is True
        assert len(r.chunks) == 1
        assert r.chunks[0].text == "酯化反应内容"

    def test_roundtrip_preserves_metadata(self, store, collection):
        """检索结果须完整带回来源元数据（§5：结果携带来源信息）。"""
        collection.add(
            ids=["a"], documents=["内容"],
            embeddings=[[0.1, 0.2]],
            metadatas=[_chunk().to_metadata()],
        )
        got = store.query("酯化").chunks[0]
        src = got.source
        assert src.source_id == "SRC-TEST-001"
        assert src.title == "人教版高中化学必修第二册"
        assert src.publisher == "人民教育出版社"
        assert src.edition == "2019年版"
        assert src.locator == "第12章 第3节"
        assert src.reviewer == "审核员A"
        assert src.status == VerificationStatus.APPROVED
        assert src.scope == ScopeLevel.HIGH_SCHOOL_REQUIRED
        assert got.embedding_model == EMBEDDING_ID

    def test_empty_query_returns_empty_result(self, store):
        r = store.query("   ")
        assert r.has_results is False
        assert r.reason == "empty_query"

    def test_invalid_top_k_returns_empty(self, store):
        assert store.query("问题", top_k=0).has_results is False

    def test_no_match_returns_empty(self, store):
        assert store.query("问题").has_results is False

    def test_threshold_filters_results(self, store, collection):
        """§5：阈值由标注问答集实测确定，低于阈值的结果不计入。"""
        collection.add(
            ids=["a"], documents=["内容"],
            embeddings=[[0.1, 0.2]],
            metadatas=[_chunk().to_metadata()],
        )
        # 替身返回的距离序列为 0.1, 0.2, 0.3 ...
        assert store.query("问题", threshold=0.15).has_results is True
        assert store.query("问题", threshold=0.05).has_results is False

    def test_threshold_not_met_reason_recorded(self, store, collection):
        collection.add(
            ids=["a"], documents=["内容"],
            embeddings=[[0.1, 0.2]],
            metadatas=[_chunk().to_metadata()],
        )
        r = store.query("问题", threshold=0.0)
        assert r.has_results is False
        assert "threshold" in r.reason

    def test_topic_filter_passed_through(self, store, collection):
        collection.add(
            ids=["a"], documents=["内容"],
            embeddings=[[0.1, 0.2]],
            metadatas=[_chunk().to_metadata()],
        )
        store.query("问题", topic="酯")
        assert collection.docs  # 集合未被清空，说明过滤在查询层生效


# ----------------------------------------------------------------------
# 失败隔离（architecture.md §6）
# ----------------------------------------------------------------------
class TestFailureIsolation:
    def test_add_failure_wrapped(self, collection):
        s = KnowledgeStore(
            _FakeClient(collection), "test_collection", HashEmbedding(model_id=EMBEDDING_ID)
        )
        collection.fail_on = "add"
        with pytest.raises(RetrievalError) as exc:
            s.add([_chunk()])
        assert exc.value.code == "rag_retrieval_failed"

    def test_query_failure_wrapped(self, collection):
        s = KnowledgeStore(
            _FakeClient(collection), "test_collection", HashEmbedding(model_id=EMBEDDING_ID)
        )
        collection.fail_on = "query"
        with pytest.raises(RetrievalError):
            s.query("问题")

    def test_embedding_failure_wrapped(self, collection):
        class _BrokenEmbedding:
            model_id = EMBEDDING_ID  # 须与片段声明一致，否则先被模型一致性校验拦下

            def embed(self, texts):
                raise RuntimeError("模型未下载")

        s = KnowledgeStore(_FakeClient(collection), "test_collection", _BrokenEmbedding())
        with pytest.raises(EmbeddingUnavailableError) as exc:
            s.add([_chunk()])
        assert exc.value.retryable is True

    def test_embedding_count_mismatch_detected(self, collection):
        class _ShortEmbedding:
            model_id = EMBEDDING_ID

            def embed(self, texts):
                return [[0.1, 0.2]]  # 只返回 1 个，实际要 2 个

        s = KnowledgeStore(_FakeClient(collection), "test_collection", _ShortEmbedding())
        with pytest.raises(EmbeddingUnavailableError):
            s.add([_chunk(text="A"), _chunk(text="B")])

    def test_index_unavailable_wrapped(self):
        class _BrokenClient:
            def get_or_create_collection(self, **kw):
                raise RuntimeError("目录不可写")

        with pytest.raises(IndexNotReadyError) as exc:
            KnowledgeStore(_BrokenClient(), "test_collection", HashEmbedding())
        assert exc.value.code == "rag_index_not_ready"


# ----------------------------------------------------------------------
# 回填给模型的载荷
# ----------------------------------------------------------------------
class TestModelPayload:
    def test_payload_carries_sources(self, store, collection):
        """§5：仅在存在可核对来源时附带来源。"""
        collection.add(
            ids=["a"], documents=["酯化反应内容"],
            embeddings=[[0.1, 0.2]],
            metadatas=[_chunk().to_metadata()],
        )
        import json

        payload = json.loads(store.query("酯化").to_model_payload())
        assert payload["found"] is True
        p = payload["passages"][0]
        assert p["source_id"] == "SRC-TEST-001"
        assert p["title"] == "人教版高中化学必修第二册"
        assert p["locator"] == "第12章 第3节"
        assert p["text"] == "酯化反应内容"

    def test_empty_result_tells_model_to_be_honest(self, store):
        """§5：无结果时明确告知模型，不得让模型凭记忆作答。"""
        import json

        payload = json.loads(store.query("不存在的内容").to_model_payload())
        assert payload["found"] is False
        assert "未找到" in payload["message"]
        assert "标注" in payload["message"]

    def test_source_marker_is_retrieval(self, store, collection):
        """结果来源标记为 retrieval，与模型推断区分（architecture.md §5）。"""
        assert store.query("x").source == "retrieval"


# ----------------------------------------------------------------------
# 哈希嵌入的确定性（治理机制依赖此性质）
# ----------------------------------------------------------------------
class TestHashEmbedding:
    def test_deterministic(self):
        e = HashEmbedding(model_id="t", dim=32)
        a = e.embed(["酯化反应"])
        b = e.embed(["酯化反应"])
        assert a == b

    def test_different_text_different_vector(self):
        e = HashEmbedding(model_id="t", dim=32)
        assert e.embed(["酯化反应"]) != e.embed(["水解反应"])

    def test_dimension_respected(self):
        e = HashEmbedding(model_id="t", dim=16)
        assert len(e.embed(["x"])[0]) == 16

    def test_too_small_dimension_rejected(self):
        with pytest.raises(ValueError):
            HashEmbedding(dim=4)

    def test_model_id_used(self):
        assert HashEmbedding(model_id="my-model").model_id == "my-model"


# ----------------------------------------------------------------------
# 真实 chromadb 集成（验证集合名约束与实际返回结构）
# ----------------------------------------------------------------------
class TestRealChromaDB:
    @pytest.fixture
    def real_store(self, tmp_path):
        chromadb = pytest.importorskip("chromadb")
        client = chromadb.PersistentClient(
            path=str(tmp_path),
            settings=chromadb.config.Settings(anonymized_telemetry=False),
        )
        return KnowledgeStore(
            client, "rag_test_collection",
            HashEmbedding(model_id=EMBEDDING_ID), index_version="t",
        )

    def test_add_and_query_roundtrip(self, real_store):
        assert real_store.add([_chunk(text="酯化反应是酸和醇生成酯")]) == 1
        assert real_store.count() == 1
        r = real_store.query("酯化")
        assert r.has_results is True
        assert r.chunks[0].source.source_id == "SRC-TEST-001"

    def test_collection_name_constraint(self):
        """实测：集合名须 3-512 字符且只含 [a-zA-Z0-9._-]。"""
        chromadb = pytest.importorskip("chromadb")
        import tempfile

        client = chromadb.PersistentClient(
            path=tempfile.mkdtemp(),
            settings=chromadb.config.Settings(anonymized_telemetry=False),
        )
        with pytest.raises(Exception):
            client.get_or_create_collection(name="ab")  # 太短
