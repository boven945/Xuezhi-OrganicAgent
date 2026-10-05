"""数字人「化学老师」人设的测试。

## 重点测什么

1. **人设不能覆盖功能契约**——这是最关键的一条。
   `system_prompt` 参数是**覆盖**语义，
   若直接传���设就会丢掉「不编造答案」等底线。
2. **system 消息原先根本没被发出**（实测发现的缺陷）。
3. 关闭人设时行为与引入前完全一致。

## 为什么这些必须锁住

人设是「表达方式」，功能契约是「正确性要求」。
两者一旦混写，改人设就会顺手改掉底线——
而这种改动不报错，只是答案悄悄变得不可信了。
"""

from __future__ import annotations

import pytest

from app.llm.client import BASE_SYSTEM_PROMPT, LLMClient, compose_system_prompt
from app.llm.persona import (
    CLOSING_HINTS,
    EMPHASIS_HINTS,
    OPENING_HINTS,
    SPOKEN_HINTS,
    TEACHER_SYSTEM_PROMPT,
    build_teacher_prompt,
)


class TestComposeSystemPrompt:
    """功能契约与人设的拼接。"""

    def test_无人设时只返回功能契约(self) -> None:
        assert compose_system_prompt(None) == BASE_SYSTEM_PROMPT

    def test_空串人设等同于无人设(self) -> None:
        # "   " 是真值，故必须显式 strip 后判空。
        # 用 `or` 处理不当时会漏掉这个 case。
        assert compose_system_prompt("") == BASE_SYSTEM_PROMPT
        assert compose_system_prompt("   \n ") == BASE_SYSTEM_PROMPT

    def test_有人设时契约仍在(self) -> None:
        """**最关键的一条**：人设不得吃掉功能契约。"""
        merged = compose_system_prompt(TEACHER_SYSTEM_PROMPT)
        assert merged.startswith(BASE_SYSTEM_PROMPT)
        # 契约里的底线条款逐条仍在
        assert "不要编造答案" in merged
        assert "区分「知识库检索到的事实」" in merged
        assert "只在确有来源时给出引用" in merged
        # 人设也在
        assert "有多年经验的高中有机化学教师" in merged

    def test_契约在前人设在后(self) -> None:
        """顺序有讲究：提示词靠前权重更高，契约是硬要求应占先。"""
        merged = compose_system_prompt(TEACHER_SYSTEM_PROMPT)
        assert merged.index("不要编造答案") < merged.index("有多年经验的高中有机化学教师")


class TestWithSystem:
    """``_with_system``——修「system 从未发出」的那个缺陷。"""

    def test_没有system时会补上(self) -> None:
        msgs = [{"role": "user", "content": "乙醇的官能团"}]
        out = LLMClient._with_system(msgs)
        assert out[0]["role"] == "system"
        assert out[0]["content"] == BASE_SYSTEM_PROMPT
        assert out[1:] == msgs

    def test_已有system时就地替换而非追加(self) -> None:
        # 两条 system 会让模型行为不确定，故只保留一条
        msgs = [
            {"role": "system", "content": "旧人设"},
            {"role": "user", "content": "问题"},
        ]
        out = LLMClient._with_system(msgs)
        assert sum(1 for m in out if m["role"] == "system") == 1
        assert out[0]["content"] == BASE_SYSTEM_PROMPT

    def test_多条system会被去重(self) -> None:
        msgs = [
            {"role": "system", "content": "A"},
            {"role": "user", "content": "Q"},
            {"role": "system", "content": "B"},
        ]
        out = LLMClient._with_system(msgs)
        assert sum(1 for m in out if m["role"] == "system") == 1

    def test_带persona时system含人设(self) -> None:
        out = LLMClient._with_system(
            [{"role": "user", "content": "Q"}], persona=TEACHER_SYSTEM_PROMPT
        )
        assert "有多年经验的高中有机化学教师" in out[0]["content"]
        assert "不要编造答案" in out[0]["content"]

    def test_不改动传入的列表(self) -> None:
        # 副作用会污染调用方的历史
        msgs = [{"role": "user", "content": "Q"}]
        LLMClient._with_system(msgs)
        assert len(msgs) == 1

    def test_保留工具消息顺序(self) -> None:
        # tool 消息必须紧跟其对应的 assistant，否则协议不合法
        msgs = [
            {"role": "user", "content": "Q"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "结果", "tool_call_id": "1"},
        ]
        out = LLMClient._with_system(msgs)
        assert [m["role"] for m in out] == ["system", "user", "assistant", "tool"]


class TestBuildTeacherPrompt:
    """人设提示词的组织。"""

    def test_包含当前课题(self) -> None:
        assert "当前课题：乙醇的官能团" in build_teacher_prompt("乙醇的官能团")

    def test_课题原样传入不做规则抽取(self) -> None:
        # 规则抽取容易丢掉限定词，而限定词丢失会让讲解跑偏
        q = "苯酚和乙醇的官能团有什么区别"
        assert f"当前课题：{q}" in build_teacher_prompt(q)

    def test_不含化学知识(self) -> None:
        """人设只管表达方式，化学知识应由检索与 RDKit 提供。

        写进提示词会造成「模型自带知识绕过检索」，
        破坏本项目的可溯源设计。
        """
        assert "羟基" not in TEACHER_SYSTEM_PROMPT
        assert "CH2OH" not in TEACHER_SYSTEM_PROMPT


class TestSpokenHints:
    """语音口头禅。

    这些是**要被TTS 念出来的**，所以短是硬要求。
    """

    @pytest.mark.parametrize(
        "hints",
        [OPENING_HINTS, EMPHASIS_HINTS, CLOSING_HINTS, SPOKEN_HINTS],
    )
    def test_每条都很短(self, hints: tuple[str, ...]) -> None:
        for h in hints:
            assert len(h) <= 20, f"过长会被念得啰嗦：{h}"

    def test_语音口头禅比通用更短(self) -> None:
        # SPOKEN_HINTS 用于 TTS，须比文本提示更克制
        assert max(len(x) for x in SPOKEN_HINTS) <= max(
            len(x) for x in CLOSING_HINTS
        )
