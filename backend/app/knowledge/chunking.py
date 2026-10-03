"""教材文本切分。

设计依据（全部来自实测与公开基准，非假设）：

**1. 切分策略：递归字符切分，512 字符 + 20% 重叠**

- Vectara研究（NAACL 2025, arXiv:2410.13070）测试 25 种切分配置 ×
  48 个embedding 模型，结论是**切分配置对检索质量的影响 ≥ embedding
  模型的选择**。同一语料下最优与最差策略的召回率差距可达 9%
  （Chroma 研究数据）。
- FloTorch 2026 基准（50 篇论文、90 万 token）：递归字符切分 512 token
  的端到端答案准确率 **69%**，是最大规模真实文档测试中的最佳值；
  固定大小 512 为 67%；**语义切分反而只有 54%**——
  因为它产出平均 43 token 的碎片，检索命中但上下文不足，模型答不出。
- 同一研究（FloTorch）强调：召回率与答案准确率必须分开看。
  "检索得到但答错"仍是坏切分。这直接支持本项目的做法：
  评估须由教师按 `knowledge-base.md` §6 做端到端核对，
  不能只看检索命中。

**2. 为什么按字符而非 token**

`RecursiveCharacterTextSlitter` 默认 `length_function=len`，即**字符数**。
中文场景下 512 字符约350-500 token，恰好落在 bge-small-zh 的
有效区间（实测维度 512、max_seq_length 512）。
若强行按 token 计数，须额外引入分词器并校准，
而收益在本项目语料规模下不可验证。**按字符是可测量的近似**，
不引入不可验证的环节。

**3. 中文句子的终止符**

中文的句子终止符是 `。！？；` 而非英文的 `.!?`。
若沿用 LangChain 默认的 ``["\\n\\n", "\\n", " ", ""]``，
切分点会落在中文句子中间。业界中文配置的通行做法是把
中文终止符纳入分隔符优先级列表。本模块据此自定义分隔符。

**4. 重叠的作用边界（重要，容易误解）**

重叠**只能缓解**边界处的语义割裂，**不能保证**跨块内容完整：
若一个关键实体出现在 2000 字段落中部，该段落被切成两块、
只重叠 100 字，则该实体只存在于其中一块。因此重叠率是
必要不充分条件，**真正的保障是让语义单元本身完整**
（见 `chunk_by_section`，优先按结构边界切）。

**5. 表格与结构化内容不做细切**

表格被切成多块后行列关系必然损坏。因此本模块：
- 检测到表格标记时，整块保留不切
- 公式（上下标、特殊符号密集）同样保护

**非目标**：不做语义切分（需逐句 embedding，成本高且
FloTorch 2026 实测端到端准确率反而更低），
不做 LLM 辅助切分（同上，且引入额外不可控因素）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

#: 默认块大小（字符数）。依据见模块 docstring §1、§2。
DEFAULT_CHUNK_SIZE = 512

#: 默认重叠（字符数）。512 的约 16%，落在业界推荐的 10%-25% 区间。
DEFAULT_CHUNK_OVERLAP = 80

#: 中文优先分隔符。顺序即优先级，越靠前越优先。
#:
#: 前两个是段落边界（最强语义信号）；随后是中文句子终止符；
#: 再后是分号与逗号（次级）；最后是单个字符（兜底）。
CN_SEPARATORS: tuple[str, ...] = (
    "\n\n",
    "\n",
    "。",
    "！",
    "？",
    "；",
    "，",
    " ",
    "",
)

#: 中文教材常见标题模式（用作 :func:`chunk_by_section` 的默认正则）。
#:
#: **为什么必须由库提供**：实测踩过——调用方自行传入只匹配
#: ``第N[章节]`` 的正则时，``一、反应的定义`` 这类三级标题**不被识别**，
#: 整节内容会并成一块，`knowledge-base.md` §3 要求的层级标签体系失效，
#: 检索结果也会丢失小节定位信息。要求每个调用方都写对正则不可靠。
#:
#: 结构上分两个命名组：
#:
#: - ``marker``：标题**编号前缀**（"第六章"、"一、"、"（一）"、"1.1"），
#:   层级深度由它推断（见 :func:`_infer_depth`）；
#: - ``title``：**整行标题文字**，供展示与层级路径使用。
#:
#: ⚠️ 实测踩过的坑：把 ``.*`` 放在 ``title`` 组的**交替分支之外**时，
#: 捕获到的 title 只有编号（"第六章"），标题文字"有机化学基础"会丢失——
#: 因为各交替分支的匹配范围不同，整行必须落在组内。
#:
#: 覆盖的层级：
#: - 第N章 / 第N节 / 第N单元
#: - N.N（多级数字编号）
#: - 一、二、…（中文序数）
#: - （一）（二）…（括号序数）
#: - N、N.（阿拉伯数字序数）
TEXTBOOK_HEADING = re.compile(
    r"^(?P<marker>"
    r"第[一二三四五六七八九十百零\d]+[章节单元课部篇]"
    r"|\d+(?:\.\d+)+"
    r"|[一二三四五六七八九十]+、"
    r"|（[一二三四五六七八九十]+）"
    r"|\d+[、.．]"
    r")\s*(?P<title>\S.*)?$"
)

#: 表格标记。命中则整块保护，不做细切（见模块 docstring §5）。
_TABLE_MARKERS: tuple[str, ...] = (
    "┌",
    "├",
    "└",
    "│",
    "┬",
    "┼",
    "─",
    "═",
)

#: 公式特征：上下标、箭头、希腊字母等密集出现的行。
#: 公式特征：上下标、箭头、希腊字母、LaTeX 命令。
#:
#: LaTeX 命令允许两种形态：``\alpha``（无参）与 ``\frac{a}{b}``（带参），
#: 因此量词 ``\{0,1\}`` 必须可选——实测踩过：强制要求紧跟 ``{`` 时，
#: ``\alpha 碳氢化合物`` 这样的串会漏判。
_FORMULA_HINT = re.compile(
    r"[₀-₉⁰-⁹]"  # 下标 0-9、上标 0-9
    r"|[α-ωΑ-Ω]"  # 希腊字母
    r"|[→←↔]"  # 反应箭头
    r"|\\[a-zA-Z]+"  # LaTeX 命令（可带参数或不带）
)

#: 连续空白（含全角空格），用于归一化。
_WHITESPACE = re.compile(r"[ \t　]+")


@dataclass(frozen=True, slots=True)
class TextChunk:
    """切分结果。

    Attributes:
        text: 片段正文。已归一化空白，但**不丢失换行**
            （换行是语义边界信号）。
        index: 在原文中的序号，从 0 起。
        heading_path: 所属标题层级路径，形如 ``("第六章 第三节", "酯化反应")``。
            检索结果据此给模型提供上下文坐标——
            否则单独一个片段脱离了"这是哪一节讲的"（§4 层级标签体系）。
    """

    text: str
    index: int
    heading_path: tuple[str, ...] = ()


def normalize_whitespace(text: str) -> str:
    """归一化空白，但不合并换行。

    换行必须保留：它是切分器的最高优先级分隔符。
    连续的水平空白（含全角空格）压缩为一个，并去掉行尾空白。
    """
    lines = [_WHITESPACE.sub(" ", line).rstrip() for line in text.split("\n")]
    return "\n".join(lines)


def looks_like_table(text: str) -> bool:
    """判断片段是否含表格结构。"""
    hits = sum(1 for m in _TABLE_MARKERS if m in text)
    return hits >= 2


def looks_like_formula(text: str) -> bool:
    """判断片段是否含密集公式特征。

    阈值取 3：单个箭头或上下标不足以判定，
    但化学方程式与结构简式的符号密度明显高于散文。
    """
    return len(_FORMULA_HINT.findall(text)) >= 3


def split_text(
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    separators: Sequence[str] = CN_SEPARATORS,
) -> list[str]:
    """递归字符切分。

    Args:
        text: 原文。
        chunk_size: 目标块大小（字符数）。须大于 0。
        chunk_overlap: 相邻块重叠字符数。会被夹在 ``[0, chunk_size-1]``。
        separators: 分隔符优先级列表。

    Returns:
        片段列表。空文本返回空列表。

    Raises:
        ValueError: ``chunk_size`` 非法，或 ``chunk_overlap`` 为负。
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")
    if chunk_overlap < 0:
        raise ValueError("chunk_overlap 不得为负")

    overlap = min(chunk_overlap, chunk_size - 1)
    normalized = normalize_whitespace(text)
    if not normalized.strip():
        return []

    segments = _recursive_split(normalized, chunk_size, tuple(separators))
    return _merge_segments(segments, chunk_size, overlap)


def _recursive_split(text: str, limit: int, seps: tuple[str, ...]) -> list[str]:
    """按分隔符优先级递归切分。

    逐级尝试：段落分隔符 → 句子终止符 → …… → 单字符兜底。
    对超长段落才下探到下一级分隔符，正常段落不会被硬切。
    """
    if len(text) <= limit:
        return [text] if text.strip() else []

    # 空字符串是兜底分隔符，递归到底就该逐字符切
    for i, sep in enumerate(seps):
        if sep == "":
            return [text[j : j + limit] for j in range(0, len(text), limit)]

        if sep not in text:
            continue

        parts = _recursive_split(text.replace(sep, sep + "\x00"), limit, seps[i + 1 :])
        # 用哨兵还原分隔符：保留它作为前一块的结尾，
        # 这样"句号"不会消失，下游看到的仍是完整句子
        return [p.replace("\x00", "") for p in parts]

    return [text]


def _merge_segments(
    segments: Sequence[str], chunk_size: int, overlap: int
) -> list[str]:
    """把细粒度片段合并成目标大小的块，并施加重叠。"""
    chunks: list[str] = []
    current = ""
    carry = ""  # 上一块的尾部，用于制造重叠

    for seg in segments:
        candidate = current + seg
        if len(candidate) <= chunk_size:
            current = candidate
            continue

        # 超出目标大小，先收尾当前块
        if current.strip():
            chunks.append(current.strip())

        # 下一块的开头 = 上一块尾部 overlap 个字符 + 当前段
        tail = chunks[-1][-overlap:] if (overlap and chunks) else ""
        current = (tail + seg) if tail else seg

    if current.strip():
        chunks.append(current.strip())

    return chunks


def chunk_by_section(
    text: str,
    heading_pattern: re.Pattern[str] | None = None,
    *,
    max_chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    min_chunk_chars: int = 40,
) -> list[TextChunk]:
    """按标题层级切分，**结构优先于长度**。

    这是本模块的首选入口。理由（见模块 docstring §4）：
    重叠只能缓解边界割裂，真正的保障是让语义单元完整。
    教材有明确的"章 → 节 → 小节"结构，若先按结构切，
    多数小节天然就落在 512 字符内，无需再细切。

    Args:
        text: 原文。
        heading_pattern: 匹配标题行的正则，**必须含一个命名组** ``title``。
            ``None`` 时使用 :data:`TEXTBOOK_HEADING`（覆盖中文教材的常见层级）。
            **建议优先用默认值**——自行构造正则时容易漏掉层级模式，
            导致小节标题不被识别（见 ``TEXTBOOK_HEADING`` 的说明）。
        max_chunk_size: 单块字符上限。超长小节会被递归细切。
        overlap: 细切时的重叠字符数。
        min_chunk_chars: 小于该长度的块会被并入上一块，避免碎片。

    Returns:
        :class:`TextChunk` 序列，带标题路径。

    Raises:
        ValueError: ``heading_pattern`` 无命名组 ``title``。
    """
    pattern = heading_pattern or TEXTBOOK_HEADING
    if "title" not in pattern.groupindex:
        raise ValueError("heading_pattern 必须含命名组 title")

    sections = _split_sections(text, pattern)
    chunks: list[TextChunk] = []

    for heading_path, body in sections:
        body = body.strip()
        if not body:
            continue

        # 结构保护**优先于长度判断**。
        # 实测踩过：把该判断写在 `len(body) <= max_chunk_size` 分支内时，
        # 超长表格（133 字符 > 60）会绕过保护被细切，行列关系彻底破坏——
        # 这正是 §5 要防止的情况，却因位置写错而完全失效。
        if looks_like_table(body) or looks_like_formula(body):
            pieces = [body]
        elif len(body) <= max_chunk_size:
            pieces = [body]
        else:
            pieces = split_text(
                body, chunk_size=max_chunk_size, chunk_overlap=overlap
            )

        for piece in pieces:
            piece = piece.strip()
            # 碎片合并**只在同一标题路径内**进行。
            # 实测踩过：先前无条件并入上一块，导致"一、反应的定义"下
            # 仅 20 字的正文被并到上一小节，多个层级被压平成一块——
            # 这恰好破坏了 §4 的层级标签体系。
            # 跨层级合并会让片段的 heading_path 与实际内容不符。
            if len(piece) < min_chunk_chars and chunks and (
                chunks[-1].heading_path == heading_path
            ):
                prev = chunks[-1]
                merged = f"{prev.text}\n{piece}"
                chunks[-1] = TextChunk(
                    text=merged, index=prev.index, heading_path=prev.heading_path
                )
                continue
            chunks.append(
                TextChunk(text=piece, index=len(chunks), heading_path=heading_path)
            )

    return chunks


def _split_sections(
    text: str, heading_pattern: re.Pattern[str]
) -> list[tuple[tuple[str, ...], str]]:
    """按标题行切分，返回 (标题路径, 正文) 序列。

    标题层级用**出现顺序下的缩进/编号深度**推断：
    ``第六章 第三节`` 这类编号即深度，不依赖 Markdown 井号。
    教材扫描件通常没有 Markdown 结构，编号是最可靠的信号。
    """
    lines = text.split("\n")
    sections: list[tuple[tuple[str, ...], str]] = []
    path: list[str] = []
    buffer: list[str] = []

    for line in lines:
        match = heading_pattern.match(line.strip())
        if match:
            if buffer and any(x.strip() for x in buffer):
                sections.append((tuple(path), "\n".join(buffer)))
            buffer = []
            # marker 是编号前缀（判层级用），title 是标题文字（展示用）。
            # 两者拼回完整标题，缺一不可——实测踩过：只用 title 时
            # "有机化学基础"会丢；只用 marker 时标题又太笼统。
            marker = (match.groupdict().get("marker") or "").strip()
            text_part = (match.groupdict().get("title") or "").strip()
            title = f"{marker} {text_part}".strip() if text_part else marker
            depth = _infer_depth(marker or title)
            # 深度变化时裁剪路径：进入第 2 级说明第 1 级已结束
            path = path[: max(0, depth - 1)]
            while len(path) < depth - 1:
                path.append("")
            path.append(title)
        else:
            buffer.append(line)

    if buffer and any(x.strip() for x in buffer):
        sections.append((tuple(path), "\n".join(buffer)))

    return [(tuple(p for p in path if p), body) for path, body in sections]


def _infer_depth(title: str) -> int:
    """从标题编号推断层级深度。

    识别模式与返回的深度：

    ==========================  =====
    模式                深度
    ==========================  =====
    ``第六章``/ ``第一篇``        1
    ``第三节`` / ``第一单元``      2
    ``一、``/ ``1.1``            3
    ``（一）`` / ``1.1.1``        4
    ==========================  =====

    **中文序数（``一、``）判为第 3 级而非第 2 级**。
    实测踩过：先前把它与 ``第N节`` 同判为 2，导致 ``第三节 酯化反应``
    被随后出现的 ``一、反应的定义`` 覆盖，层级路径丢失了"节"这一层——
    教材的"节 → 小标题"是两级结构，不可混为一级。

    识别不出时返回 1（当作最顶层，避免误判导致路径错乱）。
    """
    if re.match(r"^第[一二三四五六七八九十百零\d]+[章篇部]", title):
        return 1
    if re.match(r"^第[一二三四五六七八九十百零\d]+[节单元课]", title):
        return 2
    # 多级数字编号：1.1 -> 3，1.1.1 -> 4
    m = re.match(r"^(\d+(?:\.\d+)+)", title)
    if m:
        return 2 + m.group(1).count(".")
    # 中文序数：节下的小标题
    if re.match(r"^[一二三四五六七八九十]+、", title):
        return 3
    # 括号序数：小标题之下
    if re.match(r"^（[一二三四五六七八九十]+）", title):
        return 4
    if re.match(r"^\d+[、.．]", title):
        return 3
    return 1


def merge_headings(headings: Iterable[str]) -> str:
    """把标题路径拼成可读字符串，用于展示与日志。

    示例：``"第六章 > 第三节 > 酯化反应"``
    """
    parts = [h for h in headings if h]
    return " > ".join(parts)


__all__ = [
    "TextChunk",
    "CN_SEPARATORS",
    "TEXTBOOK_HEADING",
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_CHUNK_OVERLAP",
    "chunk_by_section",
    "looks_like_formula",
    "looks_like_table",
    "merge_headings",
    "normalize_whitespace",
    "split_text",
]