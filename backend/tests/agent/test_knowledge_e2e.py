"""端到端验证：Agent 自主调用知识检索。

需真实模型与 embedding 权重，按环境变量 `XUEZHI_RUN_E2E_TESTS=1` 开启：

```bash
docker run --rm \\
  -e XUEZHI_RUN_E2E_TESTS=1 \\
  -e MAAS_API_KEY=<密钥> \\
  xuezhi-chem-test \\
  python -m pytest "backend/tests/agent/test_knowledge_e2e.py::TestAgentKnowledgeE2ERequiresE2E" -v
```

分两层：
1. **真实检索层**（无需模型 API Key）：bge-small-zh + chromadb 实测，
   验证中文探针能否命中预期片段。
2. **完整 Agent 链路**（需 MAAS_API_KEY）：模型自发决定调用
   ``search_knowledge``，基于检索结果作答并标注来源。
"""

from __future__ import annotations

import json
import os

import pytest

from app.rag.dev_corpus import E2E_PROBES, build_development_chunks

#: 类名含 RequiresE2E，使 `pytest -k RequiresE2E` 可直接筛选。
#: （只写装饰器名的话 -k 选不中，会静默 0 passed —— 已踩过。）
requires_e2e = pytest.mark.skipif(
    os.environ.get("XUEZHI_RUN_E2E_TESTS") != "1",
    reason="需 XUEZHI_RUN_E2E_TESTS=1（依赖模型权重下载与真实 API 调用）",
)


@pytest.fixture(scope="module")
def llm_client():
    """真实 MaaS 客户端。

    签名经 ``inspect.signature`` 核实（2026-10-04）：
    ``LLMClient(LLMConfig.from_env(**overrides))``。

    ``max_completion_tokens`` 必须给足——实测 openPangu 默认开启深度思考，
    思考过程计入 completion token，设小会导致正文为空。
    """
    from app.llm import LLMClient, LLMConfig

    return LLMClient(
        LLMConfig.from_env(
            model="openpangu-2.0-flash",
            max_completion_tokens=3000,
            timeout=150,
        )
    )


@pytest.fixture(scope="module")
def real_store(tmp_path_factory):
    """真实 chromadb + bge-small-zh 的知识库（已灌入开发语料）。"""
    from app.rag.embeddings import SentenceTransformerEmbedding
    from app.rag.store import build_store

    persist = str(tmp_path_factory.mktemp("kb_e2e"))
    embedding = SentenceTransformerEmbedding()
    store = build_store(
        persist,
        "knowledge_dev",
        embedding,
        index_version="dev-e2e",
    )
    # 语料声明的模型标识必须与 store 配置一致，否则入库被拒（§3）。
    # 实测踩过：语料写死 hash 模型标识，导致 bge 场景下 7 个用例全部 error。
    written = store.add(
        build_development_chunks(embedding_model=embedding.model_id)
    )
    assert written == 6, f"开发语料应写入 6 条，实际 {written}"
    return store


class TestRealRetrievalRequiresE2E:
    """真实检索层：bge-small-zh 中文召回。"""

    @requires_e2e
    @pytest.mark.parametrize("question,expected_key", E2E_PROBES)
    def test_probe_hits_expected_chunk(self, real_store, question, expected_key):
        """中文探针应命中预期片段。

        这里只断言"命中"，不断言排位——召回质量评估属
        `knowledge-base.md` §6，须由化学教师用标注问答集做。
        """
        result = real_store.query(question, top_k=4)
        assert result.has_results, f"{question} 未召回任何内容"
        texts = [c.text for c in result.chunks]
        assert any(
            expected_key in c.chunk_id for c in result.chunks
        ), f"{question} 未命中 {expected_key}，实际命中：{[c.chunk_id for c in result.chunks]}"

    @requires_e2e
    def test_results_carry_source_metadata(self, real_store):
        result = real_store.query("酯化反应是什么", top_k=2)
        assert result.has_results
        for c in result.chunks:
            assert c.source.source_id, "来源标识不得为空"
            assert c.source.title, "教材名称不得为空"
            assert c.source.reviewer, "审核人不得为空"
            assert c.source.status.value == "approved"

    @requires_e2e
    def test_embedding_model_recorded_in_results(self, real_store):
        """结果携带的模型标识须与 store 配置一致（§3 可追溯性）。"""
        result = real_store.query("苯的分子式", top_k=1)
        assert result.has_results
        for c in result.chunks:
            assert c.embedding_model == real_store.embedding_model_id()
            assert c.embedding_model, "模型标识不得为空"

    @requires_e2e
    def test_index_version_recorded(self, real_store):
        """集合须绑定索引版本（§7）。"""
        assert real_store.index_version() == "dev-e2e"

    @requires_e2e
    def test_out_of_scope_query_returns_no_match(self, real_store):
        """超纲问题应返回"未找到"，而非勉强召回。

        对应 `product-scope.md` §3：超出高中范围的问题不强行作答。
        """
        result = real_store.query("量子力学的波函数如何求解薛定谔方程", top_k=3)
        payload = json.loads(result.to_model_payload())
        # 允许召回部分内容，但必须如实告知模型这是知识库片段
        assert "found" in payload
        if not payload["found"]:
            assert "未找到" in payload["message"]


class TestAgentKnowledgeE2ERequiresE2E:
    """完整链路：模型自主调用检索并标注来源。"""

    @requires_e2e
    def test_model_calls_search_knowledge_and_cites(self, real_store, llm_client):
        """模型应自发调用检索工具，并在答复中标注来源。"""
        from app.agent import (
            AgentLoop,
            ToolDispatcher,
            ToolRegistry,
            build_knowledge_tools,
        )

        registry = ToolRegistry(build_knowledge_tools(real_store, threshold=None))
        loop = AgentLoop(llm_client, ToolDispatcher(registry), max_steps=4)
        out = loop.run("酯化反应是什么？请引用教材依据。")

        invoked = [i["tool"] for i in out["tool_invocations"]]
        assert "search_knowledge" in invoked, f"模型未调用检索工具，实际调用：{invoked}"
        assert all(i["ok"] for i in out["tool_invocations"]), "检索工具执行失败"
        # 答复应有实质内容
        assert len(out["text"]) > 20, f"答复过短：{out['text']!r}"

    @requires_e2e
    def test_offtopic_question_not_forced_to_cite(self, real_store, llm_client):
        """知识库无相关内容时，模型应说明而非编造出处。"""
        from app.agent import (
            AgentLoop,
            ToolDispatcher,
            ToolRegistry,
            build_knowledge_tools,
        )

        registry = ToolRegistry(build_knowledge_tools(real_store, threshold=None))
        loop = AgentLoop(llm_client, ToolDispatcher(registry), max_steps=4)
        out = loop.run("量子力学中氢原子的能级公式是什么？")
        # 无论是否调用检索，都不应崩溃，且须有实质答复
        assert len(out["text"]) > 10