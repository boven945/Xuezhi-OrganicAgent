"""Agent 流式与结构化来源的测试。

覆盖两个改造（2026-10-04）：
- **I3token 级流式**：:meth:`AgentLoop.stream` 逐步产出事件；
- **I2 结构化来源**：:func:`_extract_sources` 从工具返回里提取来源。

全部用替身，不发真实请求——真实链路的流式行为须在演示机实测
（见 docs/interface-contract-verification.md §4）。
"""

from __future__ import annotations

import json

import pytest

from app.agent.dispatcher import (
    AgentLoop,
    ToolDispatcher,
    _extract_sources,
    _merge_chunks,
)
from app.agent.errors import AgentStepLimitError
from app.agent.tools import ToolRegistry, ToolResult


# ----------------------------------------------------------------------
# 替身
# ----------------------------------------------------------------------


class _FakeChunk:
    """模拟 LangChain 的 ``AIMessageChunk``。

    实现三件事：``text`` / ``tool_calls`` 属性，以及 ``+`` 合并。
    第三项不可省——**初版没实现它**，导致 :func:`_merge_chunks`
    报 ``TypeError: unsupported operand type(s)``，
    看起来像生产代码的 bug，实则是替身不合约。

    真实 ``AIMessageChunk`` 的 ``+`` 会拼接 content 并按 index
    归并 tool_call 分片（已实测），这里照此实现最小版本。
    """

    def __init__(self, text: str = "", tool_calls: list | None = None) -> None:
        self.text = text
        self.tool_calls = list(tool_calls or [])
        #: 真实 ``AIMessage`` 同时有 ``content`` 与 ``text``（前者是
        #: 底层字段，后者是便捷属性）。初版只给 ``text``，结果降级到
        #: ``invoke`` 路径后 :meth:`AgentLoop._extract_text` 读到的是
        #: 不存在的 ``.content``，报"模型返回内容为空"——
        #: **看起来像生产 bug，实则替身不合约**。
        self.content = text

    def __add__(self, other: "_FakeChunk") -> "_FakeChunk":
        merged = _FakeChunk(self.text + other.text)
        # 真实实现按 index 归并；此处用"先到的 id 保留"近似
        seen: set[str] = {c.get("id", "") for c in self.tool_calls if c.get("id")}
        for call in other.tool_calls:
            cid = call.get("id", "")
            if not cid or cid not in seen:
                merged.tool_calls.append(call)
        return merged

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"_FakeChunk(text={self.text!r}, tools={len(self.tool_calls)})"


class _FakeClient:
    """替身模型客户端。

    脚本每项是 ``{"chunks": [...]}`` 或 ``{"msg": obj}``——
    **由被测代码自己选择走哪条路**，替身不强制。

    初版曾把脚本写成 ``("msg", x)`` 并在 ``stream_with_tools`` 里断言
    必须是 chunks，结果暴露出：实现会**先试流式再降级**，
    而替身不该惩罚这种降级尝试。故改为两种都接受。

    另有 ``stream_error`` 开关用于显式触发流式失败。
    """

    def __init__(
        self,
        script: list[dict],
        *,
        supports_streaming: bool = True,
        stream_error: bool = False,
    ) -> None:
        self._script = list(script)
        self.supports_streaming = supports_streaming
        self._stream_error = stream_error
        self.modes: list[str] = []
        #: 轮次计数**只按 invoke 递增**。
        #: 不能用 len(self.modes)：一次降级会先stream 再 invoke，
        #: 那样会把同一轮算成两轮，脚本索引错位（实测踩过）。
        self._turn = 0

    def _at(self) -> dict:
        return self._script[min(self._turn, len(self._script)) - 1]

    def invoke_with_tools(self, messages, tools):
        self.modes.append("invoke")
        self._turn += 1
        entry = self._at()
        if "msg" not in entry:
            raise AssertionError(f"该轮没有 msg：{entry}")
        return entry["msg"]

    def stream_with_tools(self, messages, tools):
        self.modes.append("stream")
        if self._stream_error:
            raise RuntimeError("模拟流式失败")
        entry = self._at()
        if "chunks" not in entry:
            # 该轮只有同步消息。抛专用异常而非断言——实现会先试流式
            # 再降级，替身不该把这种降级尝试判为失败。
            raise _NoChunks()
        for chunk in entry["chunks"]:
            yield chunk


class _NoChunks(Exception):
    """替身信号：该轮没有流式分片。

    刻意继承 ``Exception`` 而**不是** ``NotImplementedError``：
    前者应走实现里的"流式失败→降级同步"路径；
    后者若也继承它，实现里单独捕获 ``NotImplementedError``
    就会把"该轮没分片"误判为"客户端不支持流式"——语义不同。
    """


class _Msg:
    """模拟 ``AIMessage``。"""

    def __init__(self, text: str = "", tool_calls: list | None = None) -> None:
        self.content = text
        self.tool_calls = tool_calls or []


def _call(name: str = "search_knowledge", args: dict | None = None) -> dict:
    return {"name": name, "id": "call_1", "args": args or {"query": "酯化反应"}}


class _StubDispatcher(ToolDispatcher):
    """返回预设结果的调度器，避免真实工具执行。"""

    def __init__(self, result: ToolResult | None = None) -> None:
        super().__init__(ToolRegistry())
        self._result = result or ToolResult(ok=True, content="{}")
        self.executed: list[str] = []

    def execute(self, name, raw_arguments):  # type: ignore[override]
        self.executed.append(name)
        return self._result


# ----------------------------------------------------------------------
# _merge_chunks
# ----------------------------------------------------------------------


class TestMergeChunks:
    """流式 chunk 合并。"""

    def test_merges_real_langchain_chunks(self) -> None:
        """用**真实的** ``AIMessageChunk`` 验证合并行为。

        依据实测：``AIMessageChunk`` 支持 ``+``，会自动按 index
        归并 ``tool_call_chunks`` 并在完整后解析 ``args``。
        """
        from langchain_core.messages import AIMessageChunk

        a = AIMessageChunk(content="苯", tool_call_chunks=[
            {"name": "search_knowledge", "args": '{"que', "id": "c1", "index": 0, "type": "tool_call"},
        ])
        b = AIMessageChunk(content="酚", tool_call_chunks=[
            {"name": None, "args": 'ry":"x"}', "id": None, "index": 0, "type": None},
        ])
        merged = _merge_chunks([a, b])
        assert merged.content == "苯酚"
        assert merged.tool_calls[0]["name"] == "search_knowledge"
        assert merged.tool_calls[0]["args"] == {"query": "x"}

    def test_empty_raises(self) -> None:
        """空列表属编程错误，必须报错而非返回 None。"""
        with pytest.raises(ValueError):
            _merge_chunks([])

    def test_single_chunk_passthrough(self) -> None:
        """单块时也走一遍合并（等价于返回自身）。"""
        from langchain_core.messages import AIMessageChunk

        c = AIMessageChunk(content="完整")
        assert _merge_chunks([c]).content == "完整"


# ----------------------------------------------------------------------
# _extract_sources
# ----------------------------------------------------------------------


class TestExtractSources:
    """从知识库工具返回中提取来源。"""

    def test_extracts_from_real_payload(self) -> None:
        """用**实测得到的真实 JSON** 验证。

        该JSON 形状由容器内实跑 ``search_knowledge`` 取得
        （顶层 found/note/passages/retrieval_meta）。
        """
        payload = json.dumps(
            {
                "found": True,
                "note": "以下为知识库检索到的原文片段",
                "passages": [
                    {
                        "source_id": "src-organic-001",
                        "title": "有机化学自编讲义",
                        "edition": "project-authored",
                        "locator": "第三章 烃的衍生物",
                        "scope": "high_school_required",
                        "text": "苯酚具有弱酸性。",
                    }
                ],
                "retrieval_meta": {
                    "index_version": "unversioned",
                    "embedding_model": "bge-small-zh",
                    "threshold_applied": False,
                },
            },
            ensure_ascii=False,
        )
        sources = _extract_sources(payload)
        assert len(sources) == 1
        assert sources[0]["source_id"] == "src-organic-001"
        assert sources[0]["locator"] == "第三章 烃的衍生物"
        assert sources[0]["scope"] == "high_school_required"

    def test_text_field_is_excluded(self) -> None:
        """``text`` 不得进入来源——原文已随模型答复传递，重复会翻倍体积。"""
        payload = json.dumps(
            {
                "found": True,
                "passages": [
                    {
                        "source_id": "s1",
                        "title": "t",
                        "edition": "e",
                        "locator": "l",
                        "scope": "s",
                        "text": "很长的一段原文……",
                    }
                ],
            },
            ensure_ascii=False,
        )
        assert "text" not in _extract_sources(payload)[0]

    def test_found_false_returns_empty(self) -> None:
        """``found=false`` 时 passages 不存在，须返回空而非报错。"""
        assert _extract_sources(json.dumps({"found": False, "message": "未找到"})) == []

    def test_invalid_json_returns_empty(self) -> None:
        """解析失败静默返回空——来源是附加信息，不该让问答失败。"""
        assert _extract_sources("这不是 JSON") == []
        assert _extract_sources("") == []
        assert _extract_sources("[1,2,3]") == []

    def test_missing_source_id_is_skipped(self) -> None:
        """缺 source_id 的条目无法溯源，须跳过而非输出半成品。"""
        payload = json.dumps(
            {
                "found": True,
                "passages": [
                    {"title": "无 id", "text": "x"},
                    {"source_id": "ok", "title": "t"},
                ],
            }
        )
        sources = _extract_sources(payload)
        assert [s["source_id"] for s in sources] == ["ok"]

    def test_dedupes_same_source_and_locator(self) -> None:
        """同一来源同一位置多次命中只记一次。"""
        entry = {"source_id": "s1", "title": "t", "locator": "第一章"}
        payload = json.dumps({"found": True, "passages": [entry, dict(entry)]})
        assert len(_extract_sources(payload)) == 1

    def test_different_locator_kept(self) -> None:
        """同来源不同位置是不同条目，**不能**去重掉。"""
        payload = json.dumps(
            {
                "found": True,
                "passages": [
                    {"source_id": "s1", "title": "t", "locator": "第一章"},
                    {"source_id": "s1", "title": "t", "locator": "第二章"},
                ],
            }
        )
        assert len(_extract_sources(payload)) == 2

    def test_fields_are_truncated(self) -> None:
        """超长字段须截断，避免异常来源撑爆响应。"""
        payload = json.dumps(
            {"found": True, "passages": [
                {"source_id": "s" * 300, "title": "t" * 900, "locator": "l" * 400},
            ]}
        )
        source = _extract_sources(payload)[0]
        assert len(source["source_id"]) == 128
        assert len(source["title"]) == 512
        assert len(source["locator"]) == 256

    def test_error_payload_yields_nothing(self) -> None:
        """工具失败时 content 是错误 JSON，**不得**从中提出来源。

        这是安全边界：错误 JSON 里没有 passages，提出来是空的；
        但若将来错误结构变化，这里须仍是空。
        """
        payload = json.dumps({"error": "rag_embedding_unavailable", "message": "x"})
        assert _extract_sources(payload) == []


# ----------------------------------------------------------------------
# AgentLoop.stream
# ----------------------------------------------------------------------


class TestAgentStream:
    """流式事件序列。"""

    def test_yields_stage_then_done_without_tools(self) -> None:
        client = _FakeClient([{"msg": _Msg("苯酚有弱酸性。")}])
        loop = AgentLoop(client, _StubDispatcher())
        events = list(loop.stream("苯酚的酸性"))
        assert events[0]["type"] == AgentLoop.EVENT_STAGE
        assert events[-1]["type"] == AgentLoop.EVENT_DONE
        assert events[-1]["text"] == "苯酚有弱酸性。"

    def test_tool_event_emitted(self) -> None:
        """真实发生一次工具调用时应发 tool 事件。"""
        client = _FakeClient(
            [
                {"msg": _Msg("", [_call()])},
                {"msg": _Msg("酯化是酸催化反应。")},
            ]
        )
        loop = AgentLoop(client, _StubDispatcher())
        events = list(loop.stream("酯化反应"))
        tools = [e for e in events if e["type"] == AgentLoop.EVENT_TOOL]
        assert len(tools) == 1
        assert tools[0]["tool"] == "search_knowledge"
        assert tools[0]["ok"] is True

    def test_sources_event_only_on_success(self) -> None:
        """工具成功且有来源时才发 source 事件。"""
        payload = json.dumps(
            {"found": True, "passages": [
                {"source_id": "s1", "title": "讲义", "locator": "第三章"},
            ]},
            ensure_ascii=False,
        )
        client = _FakeClient(
            [
                {"msg": _Msg("", [_call()])},
                {"msg": _Msg("答案")},
            ]
        )
        loop = AgentLoop(client, _StubDispatcher(ToolResult(ok=True, content=payload)))
        events = list(loop.stream("q"))
        assert any(e["type"] == AgentLoop.EVENT_SOURCE for e in events)
        done = events[-1]
        assert done["sources"][0]["source_id"] == "s1"

    def test_no_source_event_on_tool_failure(self) -> None:
        """工具失败时**不发** source 事件——不能从错误里造来源。"""
        client = _FakeClient(
            [
                {"msg": _Msg("", [_call()])},
                {"msg": _Msg("答案")},
            ]
        )
        loop = AgentLoop(
            client,
            _StubDispatcher(ToolResult(ok=False, error_code="rag_index_not_ready")),
        )
        events = list(loop.stream("q"))
        assert not any(e["type"] == AgentLoop.EVENT_SOURCE for e in events)
        assert events[-1]["sources"] == []

    def test_delta_emitted_for_text_only_turn(self) -> None:
        """不调用工具的那轮才发 delta。"""
        client = _FakeClient([{"chunks": [_FakeChunk("苯"), _FakeChunk("酚")]}])
        loop = AgentLoop(client, _StubDispatcher())
        events = list(loop.stream("q"))
        deltas = [e["text"] for e in events if e["type"] == AgentLoop.EVENT_DELTA]
        assert deltas == ["苯", "酚"]

    def test_no_delta_for_tool_call_turn(self) -> None:
        """**中间轮不产 delta**——那时的文本是思考过程，给学生会误导。"""
        client = _FakeClient(
            [
                {"chunks": [_FakeChunk("让我先查一下", [_call()])]},
                {"chunks": [_FakeChunk("答案")]},
            ]
        )
        loop = AgentLoop(client, _StubDispatcher())
        events = list(loop.stream("q"))
        deltas = [e["text"] for e in events if e["type"] == AgentLoop.EVENT_DELTA]
        # 只有第二轮的"答案"该出现
        assert deltas == ["答案"], f"不应产出中间轮文本，实得 {deltas}"

    def test_streaming_failure_falls_back_to_sync(self) -> None:
        """流式抛异常时**降级为同步**，不让整个请求失败。

        教学场景下「慢但有答案」远好于「快但报错」
        （``architecture.md`` §6）。
        """

        class _Boom:
            supports_streaming = True

            def __init__(self) -> None:
                self.modes: list[str] = []

            def stream_with_tools(self, messages, tools):
                self.modes.append("stream")
                raise RuntimeError("模拟流式失败")
                yield  # pragma: no cover - 使其为生成器

            def invoke_with_tools(self, messages, tools):
                self.modes.append("invoke")
                return _Msg("降级后的答案")

        client = _Boom()
        loop = AgentLoop(client, _StubDispatcher())
        events = list(loop.stream("q"))
        assert events[-1]["text"] == "降级后的答案"
        assert client.modes == ["stream", "invoke"], "应先试流式再降级"

    def test_unsupported_streaming_uses_sync(self) -> None:
        """客户端声明不支持流式时直接走同步。"""

        class _NoStream:
            supports_streaming = False

            def __init__(self) -> None:
                self.modes: list[str] = []

            def stream_with_tools(self, messages, tools):  # pragma: no cover
                self.modes.append("stream")
                raise AssertionError("不该被调用")
                yield

            def invoke_with_tools(self, messages, tools):
                self.modes.append("invoke")
                return _Msg("同步答案")

        client = _NoStream()
        loop = AgentLoop(client, _StubDispatcher())
        events = list(loop.stream("q"))
        assert events[-1]["text"] == "同步答案"
        assert client.modes == ["invoke"], "声明不支持就不该试流式"

    def test_step_limit_raises(self) -> None:
        """超过最大轮数应抛 ``AgentStepLimitError``。"""
        script = [{"msg": _Msg("", [_call()])} for _ in range(5)]
        client = _FakeClient(script)
        loop = AgentLoop(client, _StubDispatcher(), max_steps=3)
        with pytest.raises(AgentStepLimitError):
            list(loop.stream("q"))


class TestRunConsumesStream:
    """``run`` 是 ``stream`` 的消费者——两者行为须一致。"""

    def test_run_returns_same_fields_as_done_event(self) -> None:
        payload = json.dumps(
            {"found": True, "passages": [
                {"source_id": "s1", "title": "讲义", "locator": "第三章"},
            ]},
            ensure_ascii=False,
        )
        client = _FakeClient(
            [
                {"msg": _Msg("", [_call()])},
                {"msg": _Msg("酯化是酸催化反应。")},
            ]
        )
        loop = AgentLoop(client, _StubDispatcher(ToolResult(ok=True, content=payload)))
        result = loop.run("酯化反应")
        assert result["text"] == "酯化是酸催化反应。"
        assert result["steps"] == 2
        assert result["tool_invocations"][0]["tool"] == "search_knowledge"
        # I2：sources 现在有值了
        assert result["sources"][0]["source_id"] == "s1"

    def test_run_sources_empty_when_no_retrieval(self) -> None:
        """没调检索工具时 sources 为空列表（而非缺失键）。"""
        client = _FakeClient([{"msg": _Msg("直接回答")}])
        loop = AgentLoop(client, _StubDispatcher())
        result = loop.run("q")
        assert result["sources"] == []
        assert "sources" in result


__all__ = [
    "TestAgentStream",
    "TestExtractSources",
    "TestMergeChunks",
    "TestRunConsumesStream",
]
