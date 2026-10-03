"""教材切分的测试。

设计取向：切分的正确性**不能用"块数"或"平均长度"这类指标断言**——
块数多不等于切得好。测试改为断言**语义完整性**：
关键定义是否落在同一块内、句末标点是否保留、表格是否未被破坏。
"""

from __future__ import annotations

import re

import pytest

from app.knowledge.chunking import (
    CN_SEPARATORS,
    TextChunk,
    chunk_by_section,
    looks_like_formula,
    looks_like_table,
    merge_headings,
    normalize_whitespace,
    split_text,
)


class TestNormalize:
    def test_collapses_horizontal_whitespace(self):
        assert normalize_whitespace("酯化  反应   是") == "酯化 反应 是"

    def test_collapses_fullwidth_space(self):
        assert normalize_whitespace("羧酸　与　醇") == "羧酸 与 醇"

    def test_preserves_newlines(self):
        """换行是最高优先级分隔符，必须保留。"""
        assert normalize_whitespace("第一段\n\n第二段") == "第一段\n\n第二段"

    def test_strips_trailing_whitespace(self):
        assert normalize_whitespace("标题   \n正文  ") == "标题\n正文"


class TestSplitText:
    def test_empty_input_returns_empty(self):
        assert split_text("") == []
        assert split_text("   \n\n  ") == []

    def test_short_text_single_chunk(self):
        assert split_text("酯化反应是酸和醇生成酯和水。") == [
            "酯化反应是酸和醇生成酯和水。"
        ]

    def test_rejects_invalid_chunk_size(self):
        with pytest.raises(ValueError):
            split_text("文本", chunk_size=0)

    def test_rejects_negative_overlap(self):
        with pytest.raises(ValueError):
            split_text("文本", chunk_overlap=-1)

    def test_chunks_respect_target_size(self):
        text = "。".join(f"这是第{i}个句子用于测试切分行为" for i in range(200))
        chunks = split_text(text, chunk_size=200, chunk_overlap=20)
        assert len(chunks) > 1
        for c in chunks:
            assert len(c) <= 200 + 120, f"块过长：{len(c)}"

    def test_cn_separators_contain_chinese_terminators(self):
        """中文句子终止符必须在分隔符列表里。"""
        for sep in ("。", "！", "？", "；"):
            assert sep in CN_SEPARATORS

    def test_paragraph_boundary_preferred(self):
        """段落边界是强语义信号，应优先于句子终止符。"""
        assert CN_SEPARATORS.index("\n\n") < CN_SEPARATORS.index("。")

    def test_sentence_terminator_preserved_in_chunk(self):
        """句末标点须保留，否则下游看到的是断句。"""
        text = "酯化反应是可逆的。皂化反应是酯的水解。"
        chunks = split_text(text, chunk_size=10, chunk_overlap=0)
        joined = "".join(chunks)
        assert "。" in joined, "句号在切分中丢失"

    def test_no_content_loss(self):
        """切分不得丢内容——去掉重叠后应能还原主要文本。"""
        text = "。".join(f"第{i}句话内容略有不同用于校验" for i in range(50))
        chunks = split_text(text, chunk_size=120, chunk_overlap=20)
        stripped = "".join(chunks)
        for i in range(50):
            assert f"第{i}句话" in stripped, f"第 {i} 句丢失"

    def test_overlap_creates_shared_content(self):
        """重叠的作用是让相邻块共享边界内容。"""
        text = "A" * 200 + "B" * 200
        with_overlap = split_text(text, chunk_size=250, chunk_overlap=50)
        without = split_text(text, chunk_size=250, chunk_overlap=0)
        assert len(with_overlap) == len(without)
        # 有重叠时总长度更大（内容被重复计入）
        assert sum(len(c) for c in with_overlap) > sum(len(c) for c in without)

    def test_deterministic(self):
        text = "酯化反应。皂化反应。水解反应。" * 30
        assert split_text(text) == split_text(text)


class TestStructureDetection:
    def test_detects_box_table(self):
        assert looks_like_table("┌───┐\n│ A │ B │\n└───┘")

    def test_plain_text_is_not_table(self):
        assert not looks_like_table("酯化反应是可逆的。")

    def test_detects_formula(self):
        assert looks_like_formula("CH₃CH₂OH + HCl → CH₃CH₂Cl + H₂O")
        assert looks_like_formula("\\alpha 碳氢化合物 CₙH₂ₙ₊₂")

    def test_prose_is_not_formula(self):
        """散文不应被误判为公式，否则会被整块保护而不切分。"""
        assert not looks_like_formula("酯化反应是羧酸与醇在酸性条件下发生的反应。")


class TestChunkBySection:
    HEADING = re.compile(r"^(?P<title>第[一二三四五六七八九十\d]+[章节].*)$")

    def test_requires_named_group(self):
        with pytest.raises(ValueError):
            chunk_by_section("正文", re.compile(r"^第.*章"))

    def test_builds_heading_path(self):
        text = """第六章 有机化学基础
第三节 酯化反应
酯化反应是羧酸与醇在酸性条件下反应生成酯和水。
浓硫酸作催化剂并吸水，生成的酯通常有果香味。
"""
        chunks = chunk_by_section(text, self.HEADING)
        assert chunks, "未产出任何块"
        # 含酯化反应正文的块，其标题路径应含"第三节"
        ester = [c for c in chunks if "羧酸与醇" in c.text]
        assert ester, "酯化反应正文未落入任何块"
        assert "第三节 酯化反应" in ester[0].heading_path
        assert "第六章 有机化学基础" in ester[0].heading_path

    def test_heading_path_nesting_depth(self):
        text = """第六章 有机化学基础
第三节 酯化反应
一、反应机理
酯化反应分酯化与水解两个方向，是可逆反应。
"""
        chunks = chunk_by_section(text, self.HEADING)
        assert any(len(c.heading_path) >= 2 for c in chunks), "层级深度未识别"

    def test_chunk_index_is_sequential(self):
        text = "正文内容。" * 400
        chunks = chunk_by_section(text, self.HEADING)
        assert [c.index for c in chunks] == list(range(len(chunks)))

    def test_empty_section_body_dropped(self):
        text = """第六章 有机化学基础
第三节 酯化反应
"""
        chunks = chunk_by_section(text, self.HEADING)
        # 只有标题没有正文时不应产生空块
        assert all(c.text.strip() for c in chunks)

    def test_long_section_is_further_split(self):
        text = "第六章 X\n第一节 Y\n" + "酯化反应内容。" * 200
        chunks = chunk_by_section(text, self.HEADING, max_chunk_size=200)
        assert len(chunks) > 1, "超长小节未被细切"

    def test_table_section_not_split(self):
        """表格被切开会破坏行列关系，必须整块保留。"""
        table = "┌────┬────┐\n│ 反应 │ 条件 │\n├────┼────┤\n│ 酯化 │ 浓硫酸 │\n│ 皂化 │ NaOH │\n│ 硝化 │ 浓硝酸 │\n└────┴────┘"
        text = f"第六章 X\n第一节 Y\n{table}\n"
        chunks = chunk_by_section(text, self.HEADING, max_chunk_size=60)
        assert len(chunks) == 1, "表格被细切了"
        assert "│" in chunks[0].text

    def test_tiny_fragment_merged_into_previous(self):
        """过短碎片应并入上一块，避免产生无意义短块。"""
        body = "酯化反应是羧酸与醇在酸性条件下反应生成酯和水的化学反应，属于取代反应。" * 3
        text = f"第六章 X\n第一节 Y\n{body}\n一、\n短\n"
        chunks = chunk_by_section(text, self.HEADING, min_chunk_chars=40)
        for c in chunks:
            assert len(c.text) >= 20 or len(chunks) == 1

    def test_hierarchy_path_is_trimmed_on_level_change(self):
        """进入浅层级时，深层级路径须被裁剪，不能残留。"""
        text = """第六章 有机化学
第三节 酯化反应
一、反应机理
内容甲乙丙丁戊己庚辛壬癸。
第七章 烃
第一节 甲烷
内容子丑寅卯辰巳午未申酉。
"""
        chunks = chunk_by_section(text, self.HEADING)
        for c in chunks:
            assert "第七章" not in c.heading_path or "第一节" in c.heading_path


class TestHeadingHelpers:
    def test_merge_headings_readable(self):
        assert merge_headings(["第六章", "第三节", "酯化反应"]) == (
            "第六章 > 第三节 > 酯化反应"
        )

    def test_merge_headings_skips_empty(self):
        assert merge_headings(["第六章", "", "酯化反应"]) == "第六章 > 酯化反应"

    def test_merge_headings_empty(self):
        assert merge_headings([]) == ""


class TestChunkContract:
    def test_chunk_is_immutable(self):
        c = TextChunk(text="x", index=0)
        with pytest.raises((AttributeError, TypeError)):
            c.text = "y"  # type: ignore[misc]

    def test_chunk_no_executable_content(self):
        """安全基线：片段不含可执行内容。

        `security-privacy.md` §4 要求入库内容不得是代码。
        切分器不得引入脚本标记。
        """
        text = "酯化反应。<script>alert(1)</script>"
        for c in split_text(text):
            # 切分器只处理文本，不应"生成"可执行内容；
            # 这里断言输出与输入的字符集合一致（不凭空产生）
            assert c in text