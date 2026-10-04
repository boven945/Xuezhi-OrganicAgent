"""文档格式的回归保护。

## 为什么需要这些检查

2026-10-04 这一天，**行首表格空格错误犯了两次**：
1. 改`docs/decision-register.md` 时自写 `'|'+'|'.join(cells)+'|'`，
   把行首 `| ` 变成 `||`，导致 `grep "^| I2 |"` 匹配不到，
   一度以为"决策项不见了"；
2. 修第1 次时又用了自写拼接，同样问题。

**记进留痕文档不够**——第二次照犯。所以这里机械化。

这类问题不会让测试失败，只会让文档静默损坏：
`grep` 找不到、渲染错乱、编号查重失效。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"
README = DOCS_DIR.parent / "README.md"


def _md_files() -> list[Path]:
    files = sorted(DOCS_DIR.glob("*.md"))
    if README.exists():
        files.append(README)
    return files


@pytest.mark.parametrize("path", _md_files(), ids=lambda p: p.name)
class TestMarkdownFormat:
    """所有 Markdown 的通用格式约束。"""

    def test_no_replacement_char(self, path: Path) -> None:
        """不得含 U+FFFD 替换字符。

        实测踩过：批量改文档后文件被写成 GBK 乱码，
        肉眼在终端不一定看得出，但 `grep` 匹配会全部失效。
        """
        text = path.read_text(encoding="utf-8")
        assert "\ufffd" not in text, (
            f"{path.name} 含乱码字符 U+FFFD，行号："
            f"{[i + 1 for i, ln in enumerate(text.splitlines()) if '\ufffd' in ln][:5]}"
        )

    def test_no_double_pipe_at_line_start(self, path: Path) -> None:
        """表格行不得以 ``||`` 开头。

        ``| a | b |`` 写坏成 ``|| a | b |`` 时Markdown 仍能渲染，
        但 ``grep "^| a |"`` 会匹配不到——这正是实际踩过的坑。
        """
        text = path.read_text(encoding="utf-8")
        bad = [
            i + 1
            for i, ln in enumerate(text.splitlines())
            if ln.startswith("||")
        ]
        assert not bad, f"{path.name} 第 {bad} 行以 '||' 开头（行首空格丢失）"

    def test_no_trailing_whitespace_in_table_rows(self, path: Path) -> None:
        """表格行不得有行尾空白——会干扰精确匹配。"""
        text = path.read_text(encoding="utf-8")
        bad = [
            i + 1
            for i, ln in enumerate(text.splitlines())
            if ln.rstrip() != ln and ln.lstrip().startswith("|")
        ]
        assert not bad, f"{path.name} 第 {bad} 行有行尾空白"


class TestDecisionRegister:
    """决策登记表的结构约束。"""

    @property
    def path(self) -> Path:
        return DOCS_DIR / "decision-register.md"

    def _rows(self) -> tuple[list[str], int]:
        text = self.path.read_text(encoding="utf-8")
        lines = text.split("\n")
        split = next(
            i for i, ln in enumerate(lines) if ln.startswith("## 决策记录")
        )
        return lines[:split], split

    def test_split_marker_exists(self) -> None:
        """必须有"## 决策记录"分节标记——解析脚本依赖它。"""
        text = self.path.read_text(encoding="utf-8")
        assert "## 决策记录" in text, "缺少分节标记 '## 决策记录'"

    def test_no_duplicate_ids(self) -> None:
        """待决策表内编号不得重复。

        实测踩过：按条件批量替换导致同一行被处理两次（D3 重复两条）。
        """
        rows, _ = self._rows()
        seen: dict[str, int] = {}
        duplicates: dict[str, list[int]] = {}
        for idx, line in enumerate(rows, 1):
            if not line.startswith("| ") or "---" in line or "编号" in line:
                continue
            tag = line.split("|")[1].strip().split("-")[0]
            if not tag or tag == "编号":
                continue
            seen[tag] = idx
            duplicates.setdefault(tag, []).append(idx)
        dup = {k: v for k, v in duplicates.items() if len(v) > 1}
        assert not dup, f"编号重复：{dup}"

    def test_rows_have_consistent_column_count(self) -> None:
        """同一表内各行的列数应一致（允许历史行少一列）。

        本表**实际存在两种列数**（实测）：
        - 5 列 44 行：早期登记项，无「阻塞对象」列；
        - 6 列 18 行：含「阻塞对象」列。

        这是历史遗留而非错误，故允许两种。但**超过两种即视为拼接错误**——
        本轮曾因自写`|'+'|'.join(...)` 插入多余格，使某行多出一列。
        """
        rows, _ = self._rows()
        counts: dict[int, int] = {}
        for line in rows:
            if not line.startswith("| ") or "---" in line or "编号" in line:
                continue
            n = len(line.split("|")[1:-1])
            counts[n] = counts.get(n, 0) + 1
        assert len(counts) <= 2, (
            f"列数种类过多，疑似拼接时插入了多余格：{counts}"
        )

    def test_status_column_position_is_correct(self) -> None:
        """**最后一格须是状态值**（非空、非正文）。

        这条比列数检查更直接：列数错位时，最后一格会变成
        空白或正文片段，状态值跑到错误位置。
        本轮 I2/I3/I5 三行曾出现该问题（状态列显示为空）。

        判定方式：末格**很短**（状态是简短标签，不会是长句）
        且**不含 Markdown 语法与句号**——正文常有 ``**强调**``
        或以句号结尾，据此区分。
        """
        rows, _ = self._rows()
        bad: list[str] = []
        for idx, line in enumerate(rows, 1):
            if not line.startswith("| ") or "---" in line or "编号" in line:
                continue
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if not cells:
                continue
            last = cells[-1]
            # 状态是短标签：不超过 12 字、不含 ** 与句号
            looks_like_prose = (
                len(last) > 12 or "**" in last or last.endswith("。")
            )
            if not last or looks_like_prose:
                bad.append(f"L{idx} 末格={last[:24]!r} ({cells[0][:8]})")
        assert not bad, f"状态列位置异常：{bad}"

    def test_status_cell_is_not_empty(self) -> None:
        """状态格不得为空。

        空状态会让"哪些还没做"这件事无法回答——
        本轮曾因列错位导致 I2/I3/I5 的状态显示为空。
        """
        rows, _ = self._rows()
        empty: list[str] = []
        for idx, line in enumerate(rows, 1):
            if not line.startswith("| ") or "---" in line or "编号" in line:
                continue
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if cells and not cells[-1]:
                empty.append(f"L{idx} ({cells[0][:8]})")
        assert not empty, f"状态列为空：{empty}"


class TestDeadLinks:
    """文档间链接有效性。"""

    def test_relative_md_links_exist(self) -> None:
        """形如 ``(other.md)`` 的相对链接须指向真实文件。"""
        missing: list[str] = []
        for path in _md_files():
            text = path.read_text(encoding="utf-8")
            for target in set(re.findall(r"\(([a-z0-9][a-z0-9-]*\.md)\)", text)):
                # 相对于 docs/ 或仓库根
                if not (DOCS_DIR / target).exists() and not (
                    DOCS_DIR.parent / target
                ).exists():
                    missing.append(f"{path.name} -> {target}")
        assert not missing, f"死链：{missing}"

    def test_docs_index_lists_every_doc(self) -> None:
        """``docs/README.md`` 应索引全部文档（漏挂载等于文档不存在）。"""
        index = (DOCS_DIR / "README.md").read_text(encoding="utf-8")
        unlisted = [
            p.name
            for p in sorted(DOCS_DIR.glob("*.md"))
            if p.name != "README.md" and p.name not in index
        ]
        assert not unlisted, f"未挂载到 docs/README.md：{unlisted}"


__all__ = [
    "TestDecisionRegister",
    "TestDeadLinks",
    "TestMarkdownFormat",
]
