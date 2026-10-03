"""语料加载与准入的测试。

重点覆盖**合规闸门**：无授权信息、许可不明、未批准、超纲
这些情况必须拒绝入库，而不是静默放行。

真实教材内容**不进入测试**——测试只用项目自编文本
（版权边界见 `docs/knowledge-base.md` §2 与 `app.knowledge.corpus` 模块文档）。
"""

from __future__ import annotations

import json

import pytest

from app.knowledge.corpus import (
    ALLOWED_LICENSES,
    CORPUS_SCHEMA,
    check_license,
    compute_content_hash,
    corpus_stats,
    load_corpus,
    to_chunks,
)
from app.rag.errors import InvalidDocumentError
from app.rag.models import ScopeLevel, VerificationStatus

MODEL_ID = "BAAI/bge-small-zh"


def _entry_from_source(source_raw: dict, chunk_id: str = "c1", text: str = "内容"):
    """用公开 API 从来源字典构造记录。

    实测踩过：直接调用私有 ``_parse_source`` 会与其签名耦合
    （漏传 ``chunk_id`` 导致 TypeError）。改走``load_corpus``——
    与生产路径一致，且不依赖内部函数签名。
    """
    import json
    import tempfile
    from pathlib import Path

    payload = {
        "schema": CORPUS_SCHEMA,
        "entries": [{"chunk_id": chunk_id, "text": text, "source": source_raw}],
    }
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "c.json"
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return load_corpus(str(p))[0]



def _write(tmp_path, payload) -> str:
    p = tmp_path / "corpus.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _source(**over) -> dict:
    base = {
        "source_id": "dev-001",
        "title": "高中有机化学要点（项目自编）",
        "publisher": "项目组",
        "edition": "2026-10",
        "curriculum_level": "高中必修",
        "topic": "酯化反应",
        "locator": "第六章第三节",
        "license": "project-authored",
        "reviewer": "化学教研组",
        "reviewed_at": "2026-10-04",
        "status": "approved",
        "scope": "high_school_required",
    }
    base.update(over)
    return base


def _corpus(entries: list[dict], schema: str = CORPUS_SCHEMA) -> dict:
    return {"schema": schema, "entries": entries}


def _entry(chunk_id: str = "c1", text: str = "酯化反应是酸和醇生成酯和水。", **src) -> dict:
    return {"chunk_id": chunk_id, "text": text, "source": _source(**src)}


class TestLoadCorpus:
    def test_loads_valid_file(self, tmp_path):
        path = _write(tmp_path, _corpus([_entry()]))
        entries = load_corpus(path)
        assert len(entries) == 1
        assert entries[0].chunk_id == "c1"
        assert entries[0].source.status is VerificationStatus.APPROVED

    def test_missing_file_rejected(self, tmp_path):
        with pytest.raises(InvalidDocumentError) as exc:
            load_corpus(str(tmp_path / "nope.json"))
        assert "不存在" in exc.value.user_message

    def test_invalid_json_rejected(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        with pytest.raises(InvalidDocumentError):
            load_corpus(str(p))

    def test_schema_mismatch_rejected(self, tmp_path):
        """schema 不匹配须显式失败，不能"尽力解析"。"""
        path = _write(tmp_path, _corpus([_entry()], schema="old/v0"))
        with pytest.raises(InvalidDocumentError) as exc:
            load_corpus(path)
        assert "schema" in exc.value.user_message

    def test_missing_entries_rejected(self, tmp_path):
        path = _write(tmp_path, {"schema": CORPUS_SCHEMA})
        with pytest.raises(InvalidDocumentError):
            load_corpus(path)

    def test_missing_chunk_id_rejected(self, tmp_path):
        bad = {"text": "内容", "source": _source()}
        path = _write(tmp_path, _corpus([bad]))
        with pytest.raises(InvalidDocumentError) as exc:
            load_corpus(path)
        assert "chunk_id" in exc.value.user_message

    def test_empty_text_rejected(self, tmp_path):
        path = _write(tmp_path, _corpus([_entry(text="   ")]))
        with pytest.raises(InvalidDocumentError) as exc:
            load_corpus(path)
        assert "为空" in exc.value.user_message

    def test_illegal_status_rejected(self, tmp_path):
        """未知 status 不得静默回退到 draft。

        静默回退会把"待审"变成"已批准"，属治理失效。
        """
        path = _write(tmp_path, _corpus([_entry(status="approved_typo")]))
        with pytest.raises(InvalidDocumentError) as exc:
            load_corpus(path)
        assert "status" in exc.value.user_message

    def test_illegal_scope_rejected(self, tmp_path):
        path = _write(tmp_path, _corpus([_entry(scope="大学内容")]))
        with pytest.raises(InvalidDocumentError) as exc:
            load_corpus(path)
        assert "scope" in exc.value.user_message

    def test_content_hash_computed_when_absent(self, tmp_path):
        path = _write(tmp_path, _corpus([_entry()]))
        entries = load_corpus(path)
        assert entries[0].source.content_hash, "缺省时应自动计算内容哈希"


class TestLicenseGate:
    """版权合规闸门。这是本模块最硬的一道。"""

    def test_missing_license_rejected(self):
        entry = _entry_from_source({**_source(), "license": ""})
        with pytest.raises(InvalidDocumentError) as exc:
            check_license(entry)
        assert "许可" in exc.value.user_message

    def test_unknown_license_rejected(self):
        entry = _entry_from_source({**_source(), "license": "网上找的"})
        with pytest.raises(InvalidDocumentError) as exc:
            check_license(entry)
        assert "白名单" in exc.value.user_message

    def test_license_whitelist_has_no_unknown(self):
        """白名单不得包含 unknown 之类的兜底值。"""
        assert "unknown" not in ALLOWED_LICENSES
        assert "unclear" not in ALLOWED_LICENSES

    def test_all_allowed_licenses_are_specific(self):
        for lic in ALLOWED_LICENSES:
            assert lic and lic.strip()
            assert "unknown" not in lic.lower()

    def test_authorized_license_accepted(self):
        entry = _entry_from_source({**_source(), "license": "licensed-official"})
        check_license(entry)  # 不应抛异常

    def test_via_to_chunks_blocks_unlicensed(self, tmp_path):
        path = _write(tmp_path, _corpus([_entry(license="")]))
        entries = load_corpus(path)
        with pytest.raises(InvalidDocumentError) as exc:
            to_chunks(entries, MODEL_ID)
        assert "许可" in exc.value.user_message


class TestToChunks:
    def test_valid_corpus_converted(self, tmp_path):
        path = _write(tmp_path, _corpus([_entry(), _entry("c2", "第二条内容。" * 5)]))
        chunks = to_chunks(load_corpus(path), MODEL_ID)
        assert len(chunks) == 2
        assert all(c.embedding_model == MODEL_ID for c in chunks)

    def test_missing_embedding_model_rejected(self, tmp_path):
        path = _write(tmp_path, _corpus([_entry()]))
        with pytest.raises(InvalidDocumentError) as exc:
            to_chunks(load_corpus(path), "  ")
        assert "embedding" in exc.value.user_message

    def test_empty_corpus_rejected(self):
        with pytest.raises(InvalidDocumentError):
            to_chunks([], MODEL_ID)

    def test_missing_required_source_field_rejected(self, tmp_path):
        """来源字段缺失须拒绝——无来源等于伪造引用（§2）。"""
        path = _write(tmp_path, _corpus([_entry(publisher="")]))
        with pytest.raises(InvalidDocumentError) as exc:
            to_chunks(load_corpus(path), MODEL_ID)
        assert "publisher" in exc.value.user_message

    def test_missing_reviewer_rejected(self, tmp_path):
        """审核人缺失须拒绝——无人审核的内容不得入库。"""
        path = _write(tmp_path, _corpus([_entry(reviewer="")]))
        with pytest.raises(InvalidDocumentError) as exc:
            to_chunks(load_corpus(path), MODEL_ID)
        assert "reviewer" in exc.value.user_message

    def test_draft_rejected_by_default(self, tmp_path):
        path = _write(tmp_path, _corpus([_entry(status="draft")]))
        with pytest.raises(InvalidDocumentError) as exc:
            to_chunks(load_corpus(path), MODEL_ID)
        assert "draft" in exc.value.user_message

    def test_pending_review_rejected(self, tmp_path):
        path = _write(tmp_path, _corpus([_entry(status="pending_review")]))
        with pytest.raises(InvalidDocumentError):
            to_chunks(load_corpus(path), MODEL_ID)

    def test_withdrawn_rejected(self, tmp_path):
        path = _write(tmp_path, _corpus([_entry(status="withdrawn")]))
        with pytest.raises(InvalidDocumentError):
            to_chunks(load_corpus(path), MODEL_ID)

    def test_undergraduate_scope_rejected(self, tmp_path):
        """超纲内容须拒绝（§4）。"""
        path = _write(tmp_path, _corpus([_entry(scope="undergraduate")]))
        with pytest.raises(InvalidDocumentError) as exc:
            to_chunks(load_corpus(path), MODEL_ID)
        assert "undergraduate" in exc.value.user_message

    def test_extended_scope_allowed_but_marked(self, tmp_path):
        """高中范围内拓展允许入库，但须保留标记（§4）。"""
        path = _write(
            tmp_path, _corpus([_entry(scope="high_school_extended")])
        )
        chunks = to_chunks(load_corpus(path), MODEL_ID)
        assert chunks[0].source.scope is ScopeLevel.HIGH_SCHOOL_EXTENDED

    def test_draft_allowed_when_opted_out(self, tmp_path):
        """草稿索引可显式放行，但该索引不得用于生产。"""
        path = _write(tmp_path, _corpus([_entry(status="draft")]))
        chunks = to_chunks(load_corpus(path), MODEL_ID, require_approved=False)
        assert len(chunks) == 1

    def test_duplicate_chunk_id_rejected(self, tmp_path):
        """重复 id 会导致向量覆盖、来源可追溯性失效。"""
        path = _write(tmp_path, _corpus([_entry("dup"), _entry("dup", "另一段内容。" * 8)]))
        with pytest.raises(InvalidDocumentError) as exc:
            to_chunks(load_corpus(path), MODEL_ID)
        assert "重复" in exc.value.user_message

    def test_partial_failure_writes_nothing(self, tmp_path):
        """任一不合规即整体拒绝，不产出片段。"""
        path = _write(
            tmp_path,
            _corpus([_entry("good"), _entry("bad", status="draft")]),
        )
        with pytest.raises(InvalidDocumentError):
            to_chunks(load_corpus(path), MODEL_ID)
        # 不应有任何部分结果被使用

    def test_all_chunks_share_one_model(self, tmp_path):
        path = _write(tmp_path, _corpus([_entry("a"), _entry("b", "另一段。" * 8)]))
        chunks = to_chunks(load_corpus(path), MODEL_ID)
        assert len({c.embedding_model for c in chunks}) == 1


class TestContentHash:
    def test_whitespace_insensitive(self):
        """空白差异不应改变哈希——排版调整不应被误判为内容变更。"""
        assert compute_content_hash("酯化反应 是") == compute_content_hash("酯化反应  是")

    def test_different_content_different_hash(self):
        assert compute_content_hash("酯化反应") != compute_content_hash("皂化反应")

    def test_stable(self):
        assert compute_content_hash("苯的分子式") == compute_content_hash("苯的分子式")


class TestCorpusStats:
    def test_reports_composition(self, tmp_path):
        path = _write(
            tmp_path,
            _corpus(
                [
                    _entry("a", "内容甲。" * 10),
                    _entry("b", "内容乙。" * 20, topic="皂化反应"),
                    _entry("c", "内容丙。" * 10, scope="high_school_extended"),
                ]
            ),
        )
        stats = corpus_stats(to_chunks(load_corpus(path), MODEL_ID))
        assert stats["chunk_count"] == 3
        assert stats["source_count"] == 1
        assert stats["topic_count"] == 2
        assert stats["by_scope"]["high_school_extended"] == 1
        assert stats["embedding_models"] == [MODEL_ID]

    def test_empty_stats_no_crash(self):
        stats = corpus_stats([])
        assert stats["chunk_count"] == 0
        assert stats["avg_chars"] == 0


class TestDevCorpusFile:
    """验证仓库内的开发语料文件本身合规。

    这是一道回归保护：若有人改动该文件违反准入要求，测试须失败。
    """

    def test_dev_corpus_file_loads_and_validates(self):
        import pathlib

        p = pathlib.Path("/work/data/knowledge/dev-corpus.json")
        if not p.is_file():
            pytest.skip("开发语料文件不存在（仅在完整仓库中存在）")
        entries = load_corpus(str(p))
        chunks = to_chunks(entries, "BAAI/bge-small-zh")
        assert len(chunks) >= 6
        assert all(c.source.license in ALLOWED_LICENSES for c in chunks)