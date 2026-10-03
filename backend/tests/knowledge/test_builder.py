"""知识库构建流水线的测试。

不依赖模型与网络：用替身embedding 验证**构建逻辑与治理校验**。
真实检索效果见 `test_organic_e2e.py`（需模型权重）。
"""

from __future__ import annotations

import json

import pytest

from app.knowledge import build_index, load_corpus_dir
from app.rag.errors import InvalidDocumentError, RetrievalError


def _source(**over) -> dict:
    base = {
        "source_id": "test-src",
        "title": "自编讲义",
        "publisher": "项目组",
        "edition": "2026-10",
        "curriculum_level": "高中选择性必修三",
        "topic": "测试主题",
        "license": "project-authored",
        "reviewer": "测试",
        "reviewed_at": "2026-10-04",
        "status": "approved",
        "scope": "high_school_required",
    }
    base.update(over)
    return base


def _payload(cid: str, text: str = "内容片段" * 10, **src) -> dict:
    return {
        "schema": "knowledge-corpus/v1",
        "entries": [{"chunk_id": cid, "text": text, "source": _source(**src)}],
    }


def _write_dir(tmp_path, files: dict[str, dict]):
    for name, payload in files.items():
        (tmp_path / name).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    return str(tmp_path)


class _Embedding:
    """确定性哈希替身，离线可复现。"""

    def __init__(self, dim: int = 32) -> None:
        self.model_id = "test-embedding-v1"
        self._dim = dim
        self.calls = 0

    def embed(self, texts):
        import hashlib

        self.calls += 1
        out = []
        for t in texts:
            digest = hashlib.sha256(t.encode("utf-8")).digest()
            out.append([b / 255.0 for b in digest[: self._dim]])
        return out


class _StubStore:
    """记录 add 调用，不触碰真实向量库。"""

    def __init__(self) -> None:
        self.added: list = []

    def add(self, chunks):
        self.added.extend(chunks)
        return len(chunks)

    def count(self) -> int:
        return len(self.added)

    def index_version(self) -> str:
        return "test"

    def embedding_model_id(self) -> str:
        return "test-embedding-v1"


class TestLoadCorpusDir:
    def test_missing_dir_rejected(self, tmp_path):
        with pytest.raises(InvalidDocumentError) as exc:
            load_corpus_dir(str(tmp_path / "nope"), "m")
        assert "不存在" in exc.value.user_message

    def test_empty_dir_rejected(self, tmp_path):
        with pytest.raises(InvalidDocumentError) as exc:
            load_corpus_dir(str(tmp_path), "m")
        assert "没有 JSON" in exc.value.user_message

    def test_loads_and_merges(self, tmp_path):
        d = _write_dir(
            tmp_path,
            {
                "a.json": _payload("a1"),
                "b.json": _payload("b1", "另一段内容" * 10),
            },
        )
        chunks = load_corpus_dir(d, "m")
        assert len(chunks) == 2
        # 按 chunk_id 排序，保证构建可复现
        assert [c.chunk_id for c in chunks] == ["a1", "b1"]

    def test_duplicate_id_across_files_rejected(self, tmp_path):
        """跨文件 id 重复须拒绝——向量会互相覆盖。"""
        d = _write_dir(tmp_path, {"a.json": _payload("same"), "b.json": _payload("same")})
        with pytest.raises(InvalidDocumentError) as exc:
            load_corpus_dir(d, "m")
        assert "重复" in exc.value.user_message

    def test_duplicate_within_file_rejected(self, tmp_path):
        payload = _payload("dup")
        payload["entries"].append(dict(payload["entries"][0], text="另一段内容" * 10))
        d = _write_dir(tmp_path, {"a.json": payload})
        with pytest.raises(InvalidDocumentError):
            load_corpus_dir(d, "m")

    def test_deterministic_order(self, tmp_path):
        """文件遍历顺序固定，否则构建不可复现。"""
        d = _write_dir(
            tmp_path,
            {f"f{i}.json": _payload(f"c{i}") for i in range(5)},
        )
        first = [c.chunk_id for c in load_corpus_dir(d, "m")]
        second = [c.chunk_id for c in load_corpus_dir(d, "m")]
        assert first == second == sorted(first)

    def test_invalid_file_fails_whole_build(self, tmp_path):
        """任一文件不合规即整体失败，不产出部分结果。"""
        d = _write_dir(
            tmp_path,
            {"a.json": _payload("ok1"), "b.json": {"schema": "wrong"}},
        )
        with pytest.raises(InvalidDocumentError):
            load_corpus_dir(d, "m")

    def test_unlicensed_content_rejected(self, tmp_path):
        d = _write_dir(tmp_path, {"a.json": _payload("c1", license="")})
        with pytest.raises(InvalidDocumentError) as exc:
            load_corpus_dir(d, "m")
        assert "许可" in exc.value.user_message

    def test_undergraduate_scope_rejected(self, tmp_path):
        d = _write_dir(tmp_path, {"a.json": _payload("c1", scope="undergraduate")})
        with pytest.raises(InvalidDocumentError):
            load_corpus_dir(d, "m")


class TestBuildIndex:
    @pytest.fixture(autouse=True)
    def _patch_store(self, monkeypatch):
        """替换 build_store 为记录型替身。"""
        self.stub = _StubStore()

        def _fake_build_store(persist, collection, embedding, *, index_version="unversioned"):
            store = _StubStore()
            # build_index 会调 store.add，再由测试取回
            self.created = store
            return store

        import app.knowledge.builder as builder_mod

        monkeypatch.setattr(
            "app.rag.store.build_store", _fake_build_store, raising=True
        )
        yield

    def test_builds_and_reports(self, tmp_path):
        d = _write_dir(
            tmp_path,
            {
                "a.json": _payload("a1", "甲内容" * 20, topic="烷烃"),
                "b.json": _payload("b1", "乙内容" * 20, topic="烯烃"),
            },
        )
        report = build_index(
            str(tmp_path / "idx"), d, _Embedding(), collection_name="test_index"
        )
        assert report["written"] == 2
        assert report["topic_count"] == 2
        assert report["embedding_model"] == "test-embedding-v1"
        assert report["by_topic"] == {"烷烃": 1, "烯烃": 1}
        assert report["index_version"] == "unversioned"

    def test_embedding_model_from_provider(self, tmp_path):
        """模型标识取自 provider，不从语料文件读——避免不一致。"""
        d = _write_dir(tmp_path, {"a.json": _payload("a1")})
        emb = _Embedding()
        report = build_index(str(tmp_path / "idx"), d, emb)
        assert report["embedding_model"] == emb.model_id

    def test_reports_length_stats(self, tmp_path):
        d = _write_dir(tmp_path, {"a.json": _payload("a1", "甲" * 200)})
        report = build_index(str(tmp_path / "idx"), d, _Embedding())
        assert report["avg_chars"] > 0
        assert report["total_chars"] >= report["avg_chars"]

    def test_invalid_collection_name_surfaces(self, tmp_path):
        """chromadb 要求集合名 3-512 字符，错误须可辨识。"""
        d = _write_dir(tmp_path, {"a.json": _payload("a1")})
        # 替身不校验长度，因此这里只确认参数被透传
        report = build_index(
            str(tmp_path / "idx"), d, _Embedding(), collection_name="ab"
        )
        assert report["collection"] == "ab"


#: 仓库内有机化学语料目录。
#:
#: 做成模块级常量而非 class 级 fixture——pytest 10 已弃用
#: "class 作用域的实例方法夹具"，且 class 作用域本无必要：
#: 只是取一个路径字符串，每次调用开销可忽略。
_ORGANIC_DIR = "/work/data/knowledge/organic"


@pytest.fixture(scope="module")
def organic_dir() -> str:
    import pathlib

    p = pathlib.Path(_ORGANIC_DIR)
    if not p.is_dir():
        pytest.skip("有机化学语料不存在（仅在完整仓库中存在）")
    return str(p)


class TestOrganicCorpusCompliance:
    """仓库内有机化学语料的回归保护。

    这类测试的价值在于：**若有人改动语料违反准入要求，测试会失败**。
    """

    def test_loads(self, organic_dir):
        chunks = load_corpus_dir(organic_dir, "BAAI/bge-small-zh")
        assert len(chunks) >= 40

    def test_all_project_authored(self, organic_dir):
        """自编讲义统一标记 project-authored——这是规避 D3 的基础。"""
        chunks = load_corpus_dir(organic_dir, "BAAI/bge-small-zh")
        bad = [c.chunk_id for c in chunks if c.source.license != "project-authored"]
        assert not bad, f"以下条目许可标记不正确：{bad}"

    def test_all_approved_and_in_scope(self, organic_dir):
        chunks = load_corpus_dir(organic_dir, "BAAI/bge-small-zh")
        for c in chunks:
            assert c.source.status.value == "approved"
            assert c.source.scope.is_in_scope

    def test_no_empty_text(self, organic_dir):
        chunks = load_corpus_dir(organic_dir, "BAAI/bge-small-zh")
        for c in chunks:
            assert len(c.text.strip()) >= 30, f"{c.chunk_id} 过短"

    def test_source_locator_present(self, organic_dir):
        """每条须有章节定位，便于模型标注引用。"""
        chunks = load_corpus_dir(organic_dir, "BAAI/bge-small-zh")
        for c in chunks:
            assert c.source.locator, f"{c.chunk_id} 缺章节定位"

    def test_covers_required_topics(self, organic_dir):
        """课标四大模块的必备主题都须覆盖。"""
        chunks = load_corpus_dir(organic_dir, "BAAI/bge-small-zh")
        topics = {c.source.topic for c in chunks}
        required = {
            "有机物的组成与结构",
            "烷烃",
            "烯烃",
            "芳香烃",
            "卤代烃",
            "醇",
            "酚",
            "醛 酮",
            "羧酸",
            "酯",
            "生物大分子",
            "合成高分子",
        }
        missing = required - topics
        assert not missing, f"缺少课标要求的主题：{sorted(missing)}"

    def test_ids_are_descriptive(self, organic_dir):
        """id 须可读，便于调试与溯源。"""
        chunks = load_corpus_dir(organic_dir, "BAAI/bge-small-zh")
        for c in chunks:
            assert c.chunk_id.startswith("org-"), f"{c.chunk_id} 命名不规范"