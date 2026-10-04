"""知识检索工具的测试。

测试不依赖模型与网络：用替身 store 验证工具层的**参数治理与失败语义**。
真实召回质量属 `knowledge-base.md` §6，须由化学教师用标注问答集评估，
不在单元测试中断言。

真实 bge-small-zh 检索质量见 `docs/embedding-model-verification.md`。
"""

from __future__ import annotations

import json

import pytest

from _env import chem_block_reason, has_chem
from app.agent import (
    ToolDispatcher,
    ToolRegistry,
    build_chem_tools,
    build_knowledge_tools,
)
from app.agent.errors import ToolArgumentError
from app.agent.knowledge_tools import MAX_TOP_K
from app.rag.models import (
    KnowledgeChunk,
    KnowledgeSource,
    RetrievalResult,
    ScopeLevel,
    VerificationStatus,
)
from app.rag.store import HashEmbedding

EMBEDDING_ID = "hash-test-embedding-v1"


def _source(**over) -> KnowledgeSource:
    base = dict(
        source_id="src-001",
        title="普通高中教科书 化学 必修第二册",
        publisher="人民教育出版社",
        edition="2019年版",
        curriculum_level="高中必修",
        topic="酯化反应",
        locator="第六章 第三节",
        license="教学使用",
        reviewer="化学教研组",
        reviewed_at="2026-10-04",
        status=VerificationStatus.APPROVED,
        scope=ScopeLevel.HIGH_SCHOOL_REQUIRED,
        content_hash="abc123",
    )
    base.update(over)
    return KnowledgeSource(**base)


def _chunk(text: str = "酯化反应是羧酸与醇在酸性条件下反应生成酯和水的化学反应。") -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id="c1",
        text=text,
        source=_source(),
        embedding_model=EMBEDDING_ID,
    )


class _FakeStore:
    """替身 store：记录调用参数，按脚本返回结果或抛出异常。"""

    def __init__(self, result=None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[dict] = []

    def query(self, question: str, *, top_k: int = 5, threshold=None, topic=None):
        self.calls.append(
            {"question": question, "top_k": top_k, "threshold": threshold, "topic": topic}
        )
        if self._error is not None:
            raise self._error
        return self._result

    def index_version(self) -> str:
        return "kb-2026.10"

    def embedding_model_id(self) -> str:
        return EMBEDDING_ID


def _registry(store, **kw) -> ToolRegistry:
    return ToolRegistry(build_knowledge_tools(store, **kw))


class TestToolSchema:
    def test_tool_registered(self):
        reg = _registry(_FakeStore())
        assert "search_knowledge" in reg
        assert len(reg) == 1

    def test_threshold_not_exposed_to_model(self):
        """阈值不得暴露为工具参数。

        `knowledge-base.md` §5 要求阈值由标注问答集实测确定。
        若交给模型自选，等于绕过阈值治理。
        """
        schema = _registry(_FakeStore()).to_openai_tools()[0]
        props = schema["function"]["parameters"]["properties"]
        assert "threshold" not in props, "阈值不得暴露给模型"
        assert set(props) == {"query", "top_k"}

    def test_description_declares_passages_are_data(self):
        """工具描述须声明片段是数据不是指令（防提示注入）。"""
        desc = _registry(_FakeStore()).to_openai_tools()[0]["function"]["description"]
        assert "不是指令" in desc
        assert "引用" in desc or "来源" in desc

    def test_schema_is_serializable(self):
        payload = json.dumps(_registry(_FakeStore()).to_openai_tools(), ensure_ascii=False)
        assert "search_knowledge" in payload


class TestParameters:
    def test_query_required(self):
        """缺必填参数须被拒绝。

        注意：``ToolDispatcher.execute`` **不抛异常**，而是返回 ``ok=False``
        的结构化结果（`architecture.md` §5），因此断言在返回值上做。
        """
        result = ToolDispatcher(_registry(_FakeStore())).execute("search_knowledge", {})
        assert not result.ok
        assert result.error_code == "tool_argument_invalid"
        assert "query" in (result.error_message or "")

    def test_top_k_omitted_uses_default(self):
        store = _FakeStore(RetrievalResult(has_results=False, reason="no_match"))
        ToolDispatcher(_registry(store)).execute("search_knowledge", {"query": "酯化反应"})
        assert store.calls[0]["top_k"] == 4

    def test_top_k_clamped_to_max(self):
        store = _FakeStore(RetrievalResult(has_results=False, reason="no_match"))
        ToolDispatcher(_registry(store)).execute(
            "search_knowledge", {"query": "酯化", "top_k": 9999}
        )
        assert store.calls[0]["top_k"] == MAX_TOP_K

    def test_top_k_zero_or_negative_becomes_one(self):
        for bad in (0, -5):
            store = _FakeStore(RetrievalResult(has_results=False, reason="no_match"))
            ToolDispatcher(_registry(store)).execute(
                "search_knowledge", {"query": "酯化", "top_k": bad}
            )
            assert store.calls[0]["top_k"] == 1, f"top_k={bad} 未被夹到下限"

    def test_non_integer_top_k_rejected_before_handler(self):
        """非整数 top_k 在参数校验阶段即被拒，不会进入检索。

        实测发现：``validate_arguments`` 的类型检查先于 handler 执行，
        因此比"回退到默认值"更严格——模型传错类型不会触发无谓检索。
        """
        store = _FakeStore(RetrievalResult(has_results=False, reason="no_match"))
        result = ToolDispatcher(_registry(store)).execute(
            "search_knowledge", {"query": "酯化", "top_k": "很多"}
        )
        assert not result.ok
        assert result.error_code == "tool_argument_invalid"
        assert store.calls == [], "参数非法时不应执行检索"

    def test_bool_top_k_rejected(self):
        """bool 是 int 的子类，须被显式拒绝（否则 True 会被当成 1）。"""
        store = _FakeStore(RetrievalResult(has_results=False, reason="no_match"))
        result = ToolDispatcher(_registry(store)).execute(
            "search_knowledge", {"query": "酯化", "top_k": True}
        )
        assert not result.ok
        assert store.calls == []

    def test_handler_coerces_non_integer_defensively(self):
        """handler 自身对非整数容错——纵深防御，不依赖上游校验。

        直接调用 handler（绕过调度层）时也不应崩溃。
        """
        store = _FakeStore(RetrievalResult(has_results=False, reason="no_match"))
        tool = _registry(store).get("search_knowledge")
        tool.handler(query="酯化", top_k="很多")
        assert store.calls[0]["top_k"] == 4

    def test_validate_arguments_directly_raises(self):
        """底层校验函数本身抛 ToolArgumentError（供非调度层调用方使用）。"""
        from app.agent import validate_arguments

        tool = _registry(_FakeStore()).get("search_knowledge")
        with pytest.raises(ToolArgumentError):
            validate_arguments(tool, {})

    def test_unknown_parameter_rejected(self):
        """未声明参数直接拒绝，避免模型传参被静默忽略。"""
        reg = _registry(_FakeStore())
        result = ToolDispatcher(reg).execute(
            "search_knowledge", {"query": "酯化", "threshold": 0.1}
        )
        assert not result.ok
        assert "threshold" in (result.error_message or "")

    def test_configured_threshold_is_passed_to_store(self):
        """构造时注入的阈值须真正传到 store。"""
        store = _FakeStore(RetrievalResult(has_results=False, reason="no_match"))
        ToolDispatcher(_registry(store, threshold=0.35)).execute(
            "search_knowledge", {"query": "酯化"}
        )
        assert store.calls[0]["threshold"] == 0.35

    def test_out_of_range_threshold_rejected_at_construction(self):
        with pytest.raises(ValueError):
            build_knowledge_tools(_FakeStore(), threshold=2.5)

    def test_invalid_default_top_k_rejected(self):
        with pytest.raises(ValueError):
            build_knowledge_tools(_FakeStore(), default_top_k=0)


class TestPayload:
    def test_found_true_carries_source_fields(self):
        """命中结果须携带可追溯来源，供模型标注引用。"""
        result = RetrievalResult(has_results=True, chunks=(_chunk(),))
        store = _FakeStore(result)
        tool_result = ToolDispatcher(_registry(store)).execute(
            "search_knowledge", {"query": "酯化反应"}
        )
        assert tool_result.ok
        data = json.loads(tool_result.content)
        assert data["found"] is True
        passage = data["passages"][0]
        for key in ("source_id", "title", "edition", "locator", "text"):
            assert key in passage, f"结果缺少来源字段 {key}"
        assert passage["source_id"] == "src-001"

    def test_found_false_explicitly_tells_model(self):
        """未命中须明确告知，让模型不编造出处（§5）。"""
        store = _FakeStore(RetrievalResult(has_results=False, reason="no_match"))
        tool_result = ToolDispatcher(_registry(store)).execute(
            "search_knowledge", {"query": "量子化学"}
        )
        assert tool_result.ok
        data = json.loads(tool_result.content)
        assert data["found"] is False
        assert "未找到" in data["message"]

    def test_retrieval_meta_appended(self):
        store = _FakeStore(RetrievalResult(has_results=True, chunks=(_chunk(),)))
        tool_result = ToolDispatcher(_registry(store, threshold=0.3)).execute(
            "search_knowledge", {"query": "酯化"}
        )
        meta = json.loads(tool_result.content)["retrieval_meta"]
        assert meta["index_version"] == "kb-2026.10"
        assert meta["embedding_model"] == EMBEDDING_ID
        assert meta["threshold_applied"] is True

    def test_threshold_applied_false_when_none(self):
        store = _FakeStore(RetrievalResult(has_results=False, reason="no_match"))
        tool_result = ToolDispatcher(_registry(store)).execute(
            "search_knowledge", {"query": "酯化"}
        )
        assert json.loads(tool_result.content)["retrieval_meta"]["threshold_applied"] is False


class TestFailureSemantics:
    def test_embedding_failure_reported_as_tool_error(self):
        """嵌入不可用必须报错，**不得伪装成"未找到"**（§5）。"""
        from app.rag.errors import EmbeddingUnavailableError

        store = _FakeStore(error=EmbeddingUnavailableError("嵌入模型不可用，知识库检索已降级。"))
        result = ToolDispatcher(_registry(store)).execute(
            "search_knowledge", {"query": "酯化"}
        )
        assert not result.ok
        assert result.error_code == "rag_embedding_unavailable"
        payload = json.loads(result.to_model_payload())
        assert payload["error"] == "rag_embedding_unavailable"
        # 关键：失败与"无结果"必须可区分
        assert "found" not in payload

    def test_retrieval_failure_reported_as_tool_error(self):
        from app.rag.errors import RetrievalError

        store = _FakeStore(error=RetrievalError("知识库检索失败。"))
        result = ToolDispatcher(_registry(store)).execute(
            "search_knowledge", {"query": "酯化"}
        )
        assert not result.ok
        assert result.error_code == "rag_retrieval_failed"

    def test_unexpected_exception_does_not_leak_detail(self):
        """未预期异常不得把内部细节透给模型。"""
        store = _FakeStore(error=RuntimeError("内部路径 /secret/xyz 泄漏风险"))
        result = ToolDispatcher(_registry(store)).execute(
            "search_knowledge", {"query": "酯化"}
        )
        assert not result.ok
        assert result.error_code == "tool_execution_failed"
        assert "/secret/xyz" not in (result.error_message or "")
        assert "/secret/xyz" not in result.to_model_payload()

    def test_meta_read_failure_does_not_break_retrieval(self):
        """元信息取不到不应让检索整体失败。"""

        class _BrokenMeta(_FakeStore):
            def index_version(self):
                raise RuntimeError("元数据损坏")

        store = _BrokenMeta(RetrievalResult(has_results=True, chunks=(_chunk(),)))
        result = ToolDispatcher(_registry(store)).execute(
            "search_knowledge", {"query": "酯化"}
        )
        assert result.ok
        assert json.loads(result.content)["retrieval_meta"]["index_version"] == "unknown"


@pytest.mark.skipif(
    not has_chem(),
    reason=chem_block_reason() or "RDKit 不可用",
)
class TestComposition:
    """化学工具与检索工具的组合。

    整类跳过：本类三个用例都以 ``build_chem_tools()`` 前提，
    RDKit 不可用时全部无法构造（实测本机被 WDAC 拦截）。
    """
    def test_composes_with_chem_tools(self):
        """RAG 与化学工具应能在同一白名单中共存。"""
        reg = ToolRegistry(build_chem_tools() + build_knowledge_tools(_FakeStore()))
        names = set(reg.names())
        assert names == {"parse_smiles", "describe_molecule", "search_knowledge"}

    def test_combined_registry_executes_both_kinds(self):
        """真实 chromadb + 替身检索工具的集成。"""
        import chromadb

        client = chromadb.PersistentClient(
            path="/tmp/xuezhi_kb_test",
            settings=chromadb.config.Settings(anonymized_telemetry=False),
        )
        collection = client.get_or_create_collection(
            name="agent_tool_test",
            configuration={"hnsw": {"space": "cosine"}},
        )
        from app.rag.store import KnowledgeStore

        store = KnowledgeStore(client, "agent_tool_test", HashEmbedding())
        store.add([_chunk()])

        reg = ToolRegistry(build_chem_tools() + build_knowledge_tools(store))
        d = ToolDispatcher(reg)

        chem = d.execute("parse_smiles", {"smiles": "CCO"})
        assert chem.ok
        assert json.loads(chem.content)["properties"]["molecular_formula"] == "C2H6O"

        kg = d.execute("search_knowledge", {"query": "酯化反应的定义"})
        assert kg.ok
        assert json.loads(kg.content)["found"] is True

    def test_knowledge_tool_is_optional(self):
        """不接入知识库时，系统仍可用化学工具运行。"""
        reg = ToolRegistry(build_chem_tools())
        assert "search_knowledge" not in reg
        result = ToolDispatcher(reg).execute("parse_smiles", {"smiles": "CCO"})
        assert result.ok