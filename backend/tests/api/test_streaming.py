"""SSE 流式接口的契约测试。

流式的关键风险是**格式不符契约**导致前端静默收不到事件，
因此这里逐条校验事件名、数据字段与线格式（实测行为）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.deps import ServiceRegistry, ServiceSettings
from app.api.routes import get_registry


@pytest.fixture()
def settings() -> ServiceSettings:
    return ServiceSettings(rate_limit_rps=1000.0, rate_limit_burst=1000)


def _events(raw: str) -> list[tuple[str, dict]]:
    import json

    out: list[tuple[str, dict]] = []
    name = "message"
    for line in raw.split("\n"):
        if line.startswith("event: "):
            name = line[7:].strip()
        elif line.startswith("data: "):
            out.append((name, json.loads(line[6:])))
    return out


def _client_with(settings: ServiceSettings, loop) -> TestClient:
    registry = ServiceRegistry(settings)
    registry._agent_loop = loop  # noqa: SLF001
    app = create_app(settings)
    app.dependency_overrides[get_registry] = lambda: registry
    return TestClient(app)


class _FakeLoop:
    """替身 AgentLoop。

    **必须同时提供 ``run`` 与 ``stream``**——这是 2026-10-04 的
    真实教训：API 层改造前只调``run()``，替身就只有 ``run``；
    改造后 SSE 走``stream()``，替身缺这个方法直接
    ``AttributeError``，6 项测试当场失败。
    替身须跟随生产接口的**全部**入口，不能只实现被测的那条。

    Attributes:
        deltas: 逐块文本，模拟 token 增量。
        sources: 检索来源。
    """

    def __init__(
        self,
        *,
        text="苯酚具有弱酸性。",
        invocations=None,
        deltas=None,
        sources=None,
    ) -> None:
        self.text = text
        self.invocations = invocations or []
        self.deltas = deltas if deltas is not None else [text]
        self.sources = sources or []

    def run(self, question: str) -> dict:
        return {
            "text": self.text,
            "steps": 2,
            "tool_invocations": self.invocations,
            "sources": self.sources,
        }

    def stream(self, question: str):
        """产出与真实 ``AgentLoop.stream`` 同构的事件序列。"""
        yield {"type": "stage", "stage": "thinking", "step": 1}
        for index, item in enumerate(self.invocations):
            yield {
                "type": "tool",
                "tool": item.get("tool", ""),
                "ok": item.get("ok", True),
                "error_code": item.get("error_code"),
            }
            if item.get("tool") == "search_knowledge" and item.get("ok", True):
                if self.sources:
                    yield {"type": "source", "sources": self.sources}
        for piece in self.deltas:
            yield {"type": "delta", "text": piece}
        yield {
            "type": "done",
            "text": self.text,
            "steps": 2,
            "tool_invocations": self.invocations,
            "sources": self.sources,
        }


class TestStreamFormat:
    """线格式与事件序列。"""

    def test_content_type_is_event_stream(self, settings) -> None:
        with _client_with(settings, _FakeLoop()) as c:
            r = c.post("/api/v1/ask/stream", json={"question": "苯酚的酸性"})
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")

    def test_first_event_is_meta(self, settings) -> None:
        """首事件必须是 meta——前端靠它立即渲染加载态。

        若首个事件迟迟不来（模型要先跑起来），用户会看到空白页，
        这是流式最容易踩的坑。
        """
        with _client_with(settings, _FakeLoop()) as c:
            r = c.post("/api/v1/ask/stream", json={"question": "x"})
            events = _events(r.text)
            assert events[0][0] == "meta"
            assert events[0][1]["request_id"]
            assert events[0][1]["schema_version"] == "1.0"

    def test_stage_before_result(self, settings) -> None:
        with _client_with(settings, _FakeLoop()) as c:
            events = _events(
                c.post("/api/v1/ask/stream", json={"question": "x"}).text
            )
            names = [n for n, _ in events]
            assert names[0] == "meta"
            assert names[-1] == "done"
            assert "result" in names
            assert names.index("result") < names.index("done")

    def test_result_carries_full_answer(self, settings) -> None:
        """result 事件字段须与同步接口一致，前端可复用渲染逻辑。"""
        with _client_with(settings, _FakeLoop(text="酯化反应是酸催化下羧酸与醇的反应。")) as c:
            events = _events(c.post("/api/v1/ask/stream", json={"question": "酯化"}).text)
            result = next(d for n, d in events if n == "result")
            assert result["explanation"] == "酯化反应是酸催化下羧酸与醇的反应。"
            assert result["sources"] == []
            assert result["steps"] == 2
            assert result["schema_version"] == "1.0"

    def test_reports_actual_tool_invocations_only(self, settings) -> None:
        """只回传**真实发生**的工具调用，不编造。

        ``tool`` 是**独立事件类型**（不是 ``stage`` 的子情形）——
        初版把它嵌在 stage 分支里，工具事件被静默丢弃，
        此测试即当时的回归保护。
        """
        loop = _FakeLoop(
            invocations=[
                {"tool": "search_knowledge", "ok": True, "error_code": None},
                {"tool": "parse_smiles", "ok": False, "error_code": "chem_invalid_structure"},
            ]
        )
        with _client_with(settings, loop) as c:
            events = _events(c.post("/api/v1/ask/stream", json={"question": "x"}).text)
            tools = [d for n, d in events if n == "tool"]
            assert len(tools) == 2, "两个工具调用都应出现在流里"
            assert tools[0]["tool"] == "search_knowledge"
            assert tools[0]["ok"] is True
            assert tools[1]["ok"] is False
            assert tools[1]["index"] == 1

    def test_delta_events_are_emitted(self, settings) -> None:
        """逐token 增量须转发为 delta 事件（决策项 I3）。"""
        loop = _FakeLoop(text="苯酚具有弱酸性。", deltas=["苯酚", "具有", "弱酸性。"])
        with _client_with(settings, loop) as c:
            events = _events(c.post("/api/v1/ask/stream", json={"question": "x"}).text)
            deltas = [d["text"] for n, d in events if n == "delta"]
            assert deltas == ["苯酚", "具有", "弱酸性。"]
            # 最终 result 仍是完整文本
            result = next(d for n, d in events if n == "result")
            assert result["explanation"] == "苯酚具有弱酸性。"

    def test_source_event_is_emitted(self, settings) -> None:
        """检索来源须在流中出现（决策项 I2）。"""
        loop = _FakeLoop(
            invocations=[{"tool": "search_knowledge", "ok": True, "error_code": None}],
            sources=[
                {
                    "source_id": "src-001",
                    "title": "有机化学自编讲义",
                    "edition": "project-authored",
                    "locator": "第三章 烃的衍生物",
                    "scope": "high_school_required",
                }
            ],
        )
        with _client_with(settings, loop) as c:
            events = _events(c.post("/api/v1/ask/stream", json={"question": "x"}).text)
            src_events = [d for n, d in events if n == "source"]
            assert len(src_events) == 1
            assert src_events[0]["sources"][0]["source_id"] == "src-001"
            # result 里也要带上sources（前端渲染最终答案时需要）
            result = next(d for n, d in events if n == "result")
            assert result["sources"][0]["locator"] == "第三章 烃的衍生物"

    def test_chinese_is_escaped_in_wire_format(self, settings) -> None:
        """中文在 SSE 线格式中是 \\uXXXX 转义（实测行为）。

        此测试**锁定该行为**而非要求改变它：前端必须 JSON.parse
        才能还原，直接用 event.data 会显示转义串。
        该约束已写入 streaming.py docstring 与接口文档。
        """
        with _client_with(settings, _FakeLoop(text="苯酚酸性")) as c:
            raw = c.post("/api/v1/ask/stream", json={"question": "x"}).text
            assert "\\u" in raw, "实测中文被转义；若此断言失败说明 FastAPI 改了行为"
            #但解析后能还原
            result = next(d for n, d in _events(raw) if n == "result")
            assert result["explanation"] == "苯酚酸性"

    def test_done_event_closes_stream(self, settings) -> None:
        """流须以 done 事件 + 空行结尾。

        SSE 协议要求事件块以空行分隔；缺了最后一个空行，
        部分客户端会认为最后一条消息未完整送达而丢弃。
        """
        with _client_with(settings, _FakeLoop()) as c:
            raw = c.post("/api/v1/ask/stream", json={"question": "x"}).text
            assert raw.endswith("\n\n"), "SSE 须以空行结束事件块"
            last = [ln for ln in raw.strip().split("\n") if ln.startswith("event: ")][-1]
            assert last == "event: done"


class TestStreamErrorHandling:
    """错误走 SSE 事件而非状态码。"""

    def test_agent_failure_becomes_error_event(self, settings) -> None:
        """Agent 抛错时应发 error 事件，且 HTTP 状态仍是 200。

        这是 SSE 的固有约束：响应头早已发出，无法再改状态码。
        正因如此错误码才是跨传输稳定的标识。
        """
        from app.llm.errors import LLMUpstreamError

        class Boom:
            def stream(self, question: str):
                raise LLMUpstreamError("模型服务暂时不可用")

        with _client_with(settings, Boom()) as c:
            r = c.post("/api/v1/ask/stream", json={"question": "x"})
            assert r.status_code == 200
            events = _events(r.text)
            assert events[-1][0] == "error"
            err = events[-1][1]
            assert err["code"] == "llm_upstream_unavailable"
            assert err["retryable"] is True
            assert err["request_id"]

    def test_error_event_hides_internal_details(self, settings) -> None:
        """错误事件不得含异常类名或上游原文。"""
        from app.llm.errors import LLMUpstreamError

        class Leaky:
            def stream(self, question: str):
                raise LLMUpstreamError("上游返回: key=sk-abcdef1234")

        with _client_with(settings, Leaky()) as c:
            raw = c.post("/api/v1/ask/stream", json={"question": "x"}).text
            assert "sk-abcdef1234" not in raw
            assert "LLMUpstreamError" not in raw

    def test_no_result_event_on_failure(self, settings) -> None:
        """失败时不得同时发 result——前端会误以为成功。"""
        from app.llm.errors import LLMUpstreamError

        class Boom:
            def stream(self, question: str):
                raise LLMUpstreamError("挂了")
                yield  # 使其为生成器

        with _client_with(settings, Boom()) as c:
            names = [n for n, _ in _events(c.post("/api/v1/ask/stream", json={"question": "x"}).text)]
            assert "result" not in names
            assert "done" not in names
            assert names[-1] == "error"

    def test_assembly_failure_still_yields_meta_first(self, settings) -> None:
        """装配失败也要先发 meta——保证连接立即有响应。"""
        registry = ServiceRegistry(settings)
        # 不注入 _agent_loop，让 get_agent_loop 因缺密钥抛错
        app = create_app(settings)
        app.dependency_overrides[get_registry] = lambda: registry
        with TestClient(app) as c:
            raw = c.post("/api/v1/ask/stream", json={"question": "x"}).text
            events = _events(raw)
            assert events[0][0] == "meta"
            assert events[-1][0] == "error"


class TestStreamValidation:
    """流式接口同样受输入校验保护。"""

    def test_empty_question_rejected_before_stream(self, settings) -> None:
        """空问题应在建立流之前就被拒（400），不进入事件流。"""
        with _client_with(settings, _FakeLoop()) as c:
            r = c.post("/api/v1/ask/stream", json={"question": ""})
            assert r.status_code == 400
            assert r.headers["content-type"].startswith("application/json")
            assert "text/event-stream" not in r.headers["content-type"]


__all__ = [
    "TestStreamErrorHandling",
    "TestStreamFormat",
    "TestStreamValidation",
]
