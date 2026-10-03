"""中文嵌入模型适配测试。

运行方式（容器内）：

    docker run --rm xuezhi-chem-test python -m pytest backend/tests/rag/test_embeddings.py -v

测试分两类，**不混淆**：

- **不依赖模型下载**的：规格常量、延迟加载、错误处理、依赖缺失。
  这些在任何环境都能跑。
- **需要下载模型**的（标记 ``requires_model``）：中文检索质量实测。
  默认跳过，须显式开启——因为 HuggingFace 直连在本机不通（实测 HTTP 000），
  且下载需较大流量与时间。

    docker run --rm -e XUEZHI_RUN_MODEL_TESTS=1 xuezhi-chem-test \\
        python -m pytest backend/tests/rag/test_embeddings.py -k RequiresModel -v
"""

from __future__ import annotations

import os

import pytest

from app.rag.embeddings import (
    BGE_SMALL_ZH,
    BGE_SMALL_ZH_DIM,
    BGE_SMALL_ZH_MAX_SEQ,
    SentenceTransformerEmbedding,
    probe_query_prefix,
    resolve_local_model_path,
)
from app.rag.errors import EmbeddingUnavailableError


# ----------------------------------------------------------------------
# 规格常量（实测自官方元数据 2026-10-04）
# ----------------------------------------------------------------------
class TestSpecConstants:
    def test_model_id(self):
        """模型标识即写入索引 metadata 的值，变更即须重建索引。"""
        assert BGE_SMALL_ZH == "BAAI/bge-small-zh"

    def test_dimension_is_512(self):
        """实测 config.json 的 hidden_size=512。"""
        assert BGE_SMALL_ZH_DIM == 512

    def test_max_seq_length_is_512(self):
        """实测 sentence_bert_config.json 的 max_seq_length=512。"""
        assert BGE_SMALL_ZH_MAX_SEQ == 512

    def test_query_prefix_non_empty(self):
        """BGE 官方建议查询侧加前缀以提升准确率。"""
        p = probe_query_prefix()
        assert isinstance(p, str) and p.strip()

    def test_model_id_matches_default(self):
        """实例的 model_id 必须等于模型名——§3 要求索引绑定模型标识。"""
        e = SentenceTransformerEmbedding()
        assert e.model_id == e.model_name == BGE_SMALL_ZH


# ----------------------------------------------------------------------
# 延迟加载行为
# ----------------------------------------------------------------------
class TestLazyLoading:
    def test_construction_does_not_load(self):
        """构造对象不应触发下载。

        理由：断网环境（deployment-operations.md §5）下 import 或构造阶段
        就失败，会让整个服务无法启动，而不只是检索降级。
        """
        e = SentenceTransformerEmbedding()
        assert e._model is None  # 未加载

    def test_model_id_available_without_loading(self):
        """未加载时也能给出模型标识（用于日志与索引元数据）。"""
        assert SentenceTransformerEmbedding().model_id == BGE_SMALL_ZH

    def test_empty_input_returns_empty_without_loading(self):
        """空输入直接返回空列表，不触发模型加载。"""
        e = SentenceTransformerEmbedding()
        assert e.embed([]) == []
        assert e._model is None

    def test_repeated_calls_reuse_model(self):
        """模型应缓存，不重复加载。"""
        e = SentenceTransformerEmbedding()
        # 手动注入替身，避免真实下载
        e._model = object()
        assert e._ensure_loaded() is e._model
        assert e._ensure_loaded() is e._model

    def test_trust_remote_code_disabled(self):
        """不得执行模型仓库中的自定义代码（供应链风险）。

        security-privacy.md §5：只从可信渠道获取权重并核验完整性。
        bge 系列不需要 remote code，开启只会扩大风险面。
        """
        import inspect

        src = inspect.getsource(SentenceTransformerEmbedding._ensure_loaded)
        assert "trust_remote_code=False" in src


# ----------------------------------------------------------------------
# 错误处理
# ----------------------------------------------------------------------
class TestErrorHandling:
    def test_load_failure_wrapped(self, monkeypatch):
        """模型加载失败须转为受控错误，不泄漏原始异常。"""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "sentence_transformers":
                raise ImportError("No module named 'sentence_transformers'")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        e = SentenceTransformerEmbedding()
        with pytest.raises(EmbeddingUnavailableError) as exc:
            e.embed(["测试"])
        assert exc.value.retryable is True
        assert "sentence-transformers" in exc.value.user_message

    def test_local_only_without_cache_fails_cleanly(self):
        """离线模式且无本地权重时须明确报错，不静默回退到下载。"""
        e = SentenceTransformerEmbedding(
            local_files_only=True,
            cache_folder="/nonexistent-cache-dir-for-test",
        )
        with pytest.raises(EmbeddingUnavailableError):
            e.embed(["测试"])


# ----------------------------------------------------------------------
# 本地路径解析
# ----------------------------------------------------------------------
class TestLocalPath:
    def test_explicit_path_wins(self, monkeypatch):
        monkeypatch.setenv("XUEZHI_EMBEDDING_PATH", "/from/env")
        assert resolve_local_model_path("/explicit") == "/explicit"

    def test_env_used_when_no_explicit(self, monkeypatch):
        monkeypatch.setenv("XUEZHI_EMBEDDING_PATH", "/from/env")
        assert resolve_local_model_path() == "/from/env"

    def test_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("XUEZHI_EMBEDDING_PATH", raising=False)
        assert resolve_local_model_path() is None

    def test_empty_env_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv("XUEZHI_EMBEDDING_PATH", "")
        assert resolve_local_model_path() in (None, "")


# ----------------------------------------------------------------------
# 中文检索质量实测（需下载模型，默认跳过）
# ----------------------------------------------------------------------
requires_model = pytest.mark.skipif(
    os.environ.get("XUEZHI_RUN_MODEL_TESTS") != "1",
    reason="需下载 BAAI/bge-small-zh（约 400MB）。设 XUEZHI_RUN_MODEL_TESTS=1 开启。"
    "HuggingFace 直连在本机不通（实测 HTTP 000），须配置代理。",
)


# 类名中含 "RequiresModel"，使 `pytest -k RequiresModel` 可直接筛选
@requires_model
class TestChineseRetrievalQualityRequiresModel:
    """验证中文检索排序正确。

    这是 H17 阻塞项的核心验证：chromadb 内置英文模型排序完全颠倒
    （见 docs/rag-verification.md §2.1），换用 bge-small-zh 后必须纠正。
    """

    # 高中有机化学典型语料，覆盖 H17 发现时用的对照样本
    CORPUS = [
        "酯化反应是羧酸与醇在酸性条件下反应生成酯和水的化学反应。",
        "乙醇的分子式是C2H6O，结构简式为CH3CH2OH。",
        "今天天气很好，适合出门散步。",
        "苯环是最简单的芳香烃，六个碳原子形成平面正六边形结构。",
        "水解反应是盐与水作用生成酸和碱的反应。",
    ]

    @staticmethod
    def _cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0

    def test_dimension_matches_spec(self):
        e = SentenceTransformerEmbedding()
        assert e.warmup() == BGE_SMALL_ZH_DIM

    def test_chinese_esterification_ranks_first(self):
        """核心用例：查"酯化反应"时，酯化反应片段必须排第一。

        英文模型下该用例排序完全颠倒（目标片段距离 0.8358，
        无关片段 0.4943），因此这是 H17 的判定基准。
        """
        e = SentenceTransformerEmbedding()
        target = "酯化反应是羧酸与醇在酸性条件下反应生成酯和水的化学反应。"
        vecs = e.embed([target] + self.CORPUS)
        q = vecs[0]
        scored = [(self._cosine(q, v), i) for i, v in enumerate(vecs[1:])]
        scored.sort(reverse=True)
        best_idx = scored[0][1]
        assert best_idx == 0, (
            f"酯化反应片段应排第一，实际排第 {best_idx + 1}；"
            f"得分={[(round(s, 4), self.CORPUS[i][:12]) for s, i in scored]}"
        )

    def test_unrelated_query_ranks_irrelevant_last(self):
        """无关查询应把不相关片段排到最后。"""
        e = SentenceTransformerEmbedding()
        vecs = e.embed(["酯化反应"] + self.CORPUS)
        q = vecs[0]
        scored = [(self._cosine(q, v), i) for i, v in enumerate(vecs[1:])]
        scored.sort(reverse=True)
        assert scored[-1][1] == 2, "「今天天气很好」应排在最后"

    def test_benzene_query_ranks_correctly(self):
        """第二个化学主题同样验证排序。"""
        e = SentenceTransformerEmbedding()
        vecs = e.embed(["苯的结构"] + self.CORPUS)
        q = vecs[0]
        scored = [(self._cosine(q, v), i) for i, v in enumerate(vecs[1:])]
        scored.sort(reverse=True)
        assert scored[0][1] == 3, "苯环片段应排第一"

    def test_vectors_normalized(self):
        """bge 含 2_Normalize 模块，输出应为单位向量。

        这保证 cosine 与内积等价，检索行为可预期。
        """
        e = SentenceTransformerEmbedding()
        v = e.embed(["酯化反应"])[0]
        norm = sum(x * x for x in v) ** 0.5
        assert abs(norm - 1.0) < 1e-3, f"向量未归一化，模长={norm}"

    def test_deterministic(self):
        """同文本两次嵌入须完全一致（索引可复现要求）。"""
        e = SentenceTransformerEmbedding()
        assert e.embed(["酯化反应"]) == e.embed(["酯化反应"])

    def test_query_prefix_improves_or_keeps(self):
        """查询侧加 bge 官方前缀后，酯化反应仍应排第一。

        前缀会改变向量，故入库侧不加、查询侧加——
        本测试确认该约定不会破坏检索效果。
        """
        e = SentenceTransformerEmbedding()
        vecs = e.embed([probe_query_prefix() + "酯化反应"] + self.CORPUS)
        q = vecs[0]
        scored = [(self._cosine(q, v), i) for i, v in enumerate(vecs[1:])]
        scored.sort(reverse=True)
        assert scored[0][1] == 0, "加查询前缀后酯化反应仍应排第一"

    def test_embed_returns_python_floats(self):
        """须返回原生 float 列表，不能是 numpy 标量（JSON 序列化需要）。"""
        e = SentenceTransformerEmbedding()
        v = e.embed(["测试"])[0]
        assert all(type(x) is float for x in v)
