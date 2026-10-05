"""Agent 工具调度层测试。

运行方式（容器内，因本机 Smart App Control 拦截 grpc 等原生扩展）：

    docker run --rm xuezhi-chem-test python -m pytest backend/tests/agent/ -v

**测试不发起真实网络请求**：模型交互用替身对象注入。
真实连通性已由 `docs/llm-adapter-verification.md` §5.2 记录。
"""

from __future__ import annotations

import json

import pytest

from _env import chem_block_reason, has_chem
from app.agent import (
    AgentStepLimitError,
    Tool,
    ToolArgumentError,
    ToolDispatcher,
    ToolExecutionError,
    ToolNotFoundError,
    ToolRegistry,
    ToolResult,
    build_chem_tools,
    validate_arguments,
)
from app.agent.dispatcher import AgentLoop


# ----------------------------------------------------------------------
# 测试替身
# ----------------------------------------------------------------------
class _FakeToolCallDict(dict):
    """模拟 LangChain ``AIMessage.tool_calls`` 元素。

    实测（2026-10-03，langchain-openai 1.6.7）：元素是 **dict**，
    键为 name / args / id / type，且 ``args`` **已是 dict**（非 JSON 字符串）。
    这与 OpenAI 原始 SDK 的对象结构不同，替身须如实反映。
    """


def _tc(name: str, args: dict, call_id: str = "call-1") -> _FakeToolCallDict:
    return _FakeToolCallDict(name=name, args=args, id=call_id, type="tool_call")


class _FakeMessage:
    """模拟 LangChain ``AIMessage``。

    实测 ``AIMessage.type`` 为 ``"ai"``；这里额外提供 ``role="assistant"``
    便于断言，真实对象亦有该语义。
    """

    type = "ai"
    role = "assistant"

    def __init__(self, content=None, tool_calls=None) -> None:
        self.content = content
        self.tool_calls = tool_calls

    def __repr__(self) -> str:
        return f"_FakeMessage(content={self.content!r}, tool_calls={self.tool_calls!r})"


class _ScriptedClient:
    """按预设脚本返回 AIMessage，并记录收到的消息。"""

    #: 声明**不支持流式**。
    #:
    #: ``AgentLoop.stream`` 用
    #: ``getattr(self._client, "supports_streaming", True)`` 判断，
    #: 本替身没有 ``stream_with_tools`` 方法，
    #: 故必须显式置 False 才会走同步调用路径。
    #: 不置的话 getattr 会取默认值 True → 调用不存在的方法 → AttributeError。
    supports_streaming = False

    def __init__(self, script) -> None:
        self._script = list(script)
        self.calls: list[dict] = []

    def invoke_with_tools(self, messages, tools, *, persona=None):
        # persona 是后加的人设参数（keyword-only）。
        # 替身须显式接收——否则生产代码一传入就 TypeError。
        # 记进 calls 供断言：可验证人设确实透传到了模型层。
        self.calls.append(
            {"messages": list(messages), "tools": list(tools), "persona": persona}
        )
        if not self._script:
            raise AssertionError("脚本已耗尽但 Agent 仍在请求模型")
        return self._script.pop(0)


# ----------------------------------------------------------------------
# 工具定义与白名单
# ----------------------------------------------------------------------
def _echo_tool(name: str = "echo", **kw) -> Tool:
    return Tool(
        name=name,
        description="回显传入的文本",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        handler=lambda text: f"echo:{text}",
        **kw,
    )

def _role_of(m) -> str:
    """提取消息角色，兼容 dict 与 AIMessage 对象两种形态。

    实测 LangChain 的消息列表会混用两种形态：
    - 手工构造的 user/tool 消息是 dict
    - 模型返回的 assistant 消息是 AIMessage 对象（原样回传）
    """
    if isinstance(m, dict):
        return m.get("role", "")
    return getattr(m, "role", None) or getattr(m, "type", None) or ""


def _tool_messages(messages) -> list[dict]:
    return [m for m in messages if _role_of(m) == "tool"]


class TestRegistry:
    def test_register_and_get(self):
        reg = ToolRegistry([_echo_tool()])
        assert len(reg) == 1
        assert reg.get("echo").name == "echo"
        assert "echo" in reg

    def test_duplicate_name_rejected(self):
        reg = ToolRegistry([_echo_tool()])
        with pytest.raises(ValueError, match="重复"):
            reg.register(_echo_tool())

    def test_invalid_name_rejected(self):
        bad = Tool(
            name="bad name!", description="x", parameters={}, handler=lambda: "x"
        )
        with pytest.raises(ValueError):
            ToolRegistry([bad])

    def test_unregistered_tool_raises(self):
        """白名单边界：未注册工具必须抛错，不得静默通过。"""
        reg = ToolRegistry([_echo_tool()])
        with pytest.raises(ToolNotFoundError) as e:
            reg.get("dangerous_shell")
        assert e.value.code == "tool_not_found"
        # 错误消息应告知可用工具
        assert "echo" in e.value.user_message

    def test_to_openai_tools_shape(self):
        reg = ToolRegistry([_echo_tool()])
        schema = reg.to_openai_tools()
        assert len(schema) == 1
        item = schema[0]
        assert item["type"] == "function"
        assert item["function"]["name"] == "echo"
        assert "parameters" in item["function"]

    def test_empty_registry(self):
        reg = ToolRegistry()
        assert len(reg) == 0
        assert reg.to_openai_tools() == []


# ----------------------------------------------------------------------
# 参数校验
# ----------------------------------------------------------------------
class TestValidateArguments:
    def test_valid_json_string(self):
        tool = _echo_tool()
        assert validate_arguments(tool, '{"text": "hi"}') == {"text": "hi"}

    def test_dict_accepted(self):
        tool = _echo_tool()
        assert validate_arguments(tool, {"text": "hi"}) == {"text": "hi"}

    def test_malformed_json(self):
        tool = _echo_tool()
        with pytest.raises(ToolArgumentError):
            validate_arguments(tool, "{not json}")

    def test_empty_arguments(self):
        tool = _echo_tool()
        with pytest.raises(ToolArgumentError):
            validate_arguments(tool, "")

    def test_missing_required(self):
        tool = _echo_tool()
        with pytest.raises(ToolArgumentError) as exc:
            validate_arguments(tool, "{}")
        assert "text" in exc.value.user_message

    def test_wrong_type(self):
        tool = _echo_tool()
        with pytest.raises(ToolArgumentError):
            validate_arguments(tool, '{"text": 123}')

    def test_extra_field_rejected(self):
        """未声明字段必须拒绝，避免模型传入意外参数而不自知。"""
        tool = _echo_tool()
        with pytest.raises(ToolArgumentError) as exc:
            validate_arguments(tool, '{"text": "hi", "hack": 1}')
        assert "hack" in exc.value.user_message

    def test_bool_not_accepted_as_number(self):
        """bool 是 int 子类，需显式排除。"""
        tool = Tool(
            name="t", description="d",
            parameters={"type": "object", "properties": {"n": {"type": "number"}}},
            handler=lambda **_: "ok",
        )
        with pytest.raises(ToolArgumentError):
            validate_arguments(tool, '{"n": true}')

    def test_error_does_not_echo_arguments(self):
        """错误消息不回显原始参数（可能含学生输入或注入内容）。"""
        tool = _echo_tool()
        with pytest.raises(ToolArgumentError) as e:
            validate_arguments(tool, '{"text": 123, "secret": "SENSITIVE"}')
        assert "SENSITIVE" not in e.value.user_message

    def test_non_object_json(self):
        tool = _echo_tool()
        with pytest.raises(ToolArgumentError):
            validate_arguments(tool, "[1,2,3]")


# ----------------------------------------------------------------------
# 调度器
# ----------------------------------------------------------------------
class TestDispatcher:
    def test_successful_call(self):
        d = ToolDispatcher(ToolRegistry([_echo_tool()]))
        r = d.execute("echo", '{"text": "hello"}')
        assert r.ok is True
        assert r.content == "echo:hello"
        assert r.source == "tool_verified"

    def test_unregistered_tool_raises(self):
        """未注册工具必须抛出，不返回 ok=False（这是安全边界）。"""
        d = ToolDispatcher(ToolRegistry([_echo_tool()]))
        with pytest.raises(ToolNotFoundError):
            d.execute("rm_rf", "{}")

    def test_bad_arguments_return_failure_result(self):
        """参数错误转为结构化失败，让模型知道"工具没成功"。"""
        d = ToolDispatcher(ToolRegistry([_echo_tool()]))
        r = d.execute("echo", "{}")
        assert r.ok is False
        assert r.error_code == "tool_argument_invalid"
        assert r.error_message

    def test_handler_exception_isolated(self):
        def boom(text):
            raise RuntimeError("内部细节不应外泄")

        tool = Tool(
            name="boom", description="d",
            parameters={"type": "object", "properties": {"text": {"type": "string"}},
                        "required": ["text"]},
            handler=boom,
        )
        d = ToolDispatcher(ToolRegistry([tool]))
        r = d.execute("boom", '{"text": "x"}')
        assert r.ok is False
        assert r.error_code == "tool_execution_failed"
        assert "内部细节" not in (r.error_message or "")

    def test_timeout_returns_structured_failure(self):
        import time as _t

        def slow(text):
            _t.sleep(2.0)
            return "never"

        tool = Tool(
            name="slow", description="d",
            parameters={"type": "object", "properties": {"text": {"type": "string"}},
                        "required": ["text"]},
            handler=slow, timeout=0.2,
        )
        d = ToolDispatcher(ToolRegistry([tool]))
        r = d.execute("slow", '{"text": "x"}')
        assert r.ok is False
        assert r.error_code == "tool_timeout"

    def test_non_string_result_serialized(self):
        tool = Tool(
            name="obj", description="d",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda: {"a": 1, "中文": "值"},
        )
        d = ToolDispatcher(ToolRegistry([tool]))
        r = d.execute("obj", "{}")
        assert r.ok is True
        assert json.loads(r.content)["a"] == 1


class TestToolResultPayload:
    def test_success_payload_is_content(self):
        assert ToolResult(ok=True, content="data").to_model_payload() == "data"

    def test_failure_payload_tells_model_error(self):
        """失败时必须明确告知模型，避免模型猜测结果。"""
        payload = ToolResult(
            ok=False, error_code="tool_timeout", error_message="超时了"
        ).to_model_payload()
        data = json.loads(payload)
        assert data["error"] == "tool_timeout"
        assert data["message"] == "超时了"


# ----------------------------------------------------------------------
# 内置化学工具
# ----------------------------------------------------------------------
@pytest.mark.skipif(
    not has_chem(),
    reason=chem_block_reason() or "RDKit 不可用",
)
class TestChemTools:
    """内置化学工具的集成测试。

    每个用例独立构造注册表——RDKit 引擎较轻量，无需共享夹具，
    也避免了 class 级夹具在pytest 10 的弃用问题。

    整类跳过而非逐个用例：RDKit 的 C++ 扩展若被 Windows 应用
    控制策略拦截，本类全部用例都跑不了（实测本机如此）。
    **保持类级跳过**比在每个方法上挂标记更不易漏。
    """

    @staticmethod
    def _registry() -> ToolRegistry:
        return ToolRegistry(build_chem_tools())

    def test_two_tools_registered(self):
        assert set(self._registry().names()) == {"parse_smiles", "describe_molecule"}

    def test_no_reaction_kinematics_tool(self):
        """不得提供"判断反应是否发生"的工具。

        `product-scope.md` §5：缺少可靠反应模板时不得声称已完成机理证明。
        """
        names = " ".join(self._registry().names()).lower()
        for forbidden in ("reaction", "mechanism", "kinetic"):
            assert forbidden not in names

    def test_parse_smiles_works(self):
        d = ToolDispatcher(self._registry())
        r = d.execute("parse_smiles", '{"smiles": "CCO"}')
        assert r.ok is True
        data = json.loads(r.content)
        assert data["properties"]["molecular_formula"] == "C2H6O"
        assert data["verification"] == "tool_verified"

    def test_result_carries_scope_note(self):
        """结果须带"不代表机理已验证"的说明。"""
        d = ToolDispatcher(self._registry())
        r = d.execute("parse_smiles", '{"smiles": "CCO"}')
        notes = json.loads(r.content)["notes"]
        assert any("不代表反应机理" in n for n in notes)

    def test_invalid_smiles_returns_structured_error(self):
        """化学错误转为工具失败，不抛异常也不编造结果。"""
        d = ToolDispatcher(self._registry())
        r = d.execute("parse_smiles", '{"smiles": "CC("}')
        assert r.ok is False
        assert r.error_code == "tool_execution_failed"

    def test_describe_molecule(self):
        d = ToolDispatcher(self._registry())
        r = d.execute("describe_molecule", '{"smiles": "c1ccccc1"}')
        assert r.ok is True
        assert "C6H6" in r.content

    def test_tool_schema_is_serializable(self):
        """工具定义须可 JSON 序列化（要发给模型）。"""
        payload = json.dumps(self._registry().to_openai_tools(), ensure_ascii=False)
        assert "parse_smiles" in payload


# ----------------------------------------------------------------------
# Agent 主循环
# ----------------------------------------------------------------------
class TestAgentLoop:
    def test_direct_answer_without_tool(self):
        client = _ScriptedClient([_FakeMessage(content="直接回答")])
        loop = AgentLoop(client, ToolDispatcher(ToolRegistry([_echo_tool()])))
        out = loop.run("问题")
        assert out["text"] == "直接回答"
        assert out["steps"] == 1
        assert out["tool_invocations"] == []

    def test_tool_call_then_answer(self):
        client = _ScriptedClient(
            [
                _FakeMessage(
                        tool_calls=[_tc("echo", {"text": "hi"})]
                    ),
                _FakeMessage(content="工具结果说明如下"),
            ]
        )
        loop = AgentLoop(client, ToolDispatcher(ToolRegistry([_echo_tool()])))
        out = loop.run("问题")
        assert out["text"] == "工具结果说明如下"
        assert out["steps"] == 2
        assert out["tool_invocations"] == [{"tool": "echo", "ok": True, "error_code": None}]

    def test_tool_result_is_sent_back(self):
        client = _ScriptedClient(
            [
                _FakeMessage(tool_calls=[_tc("echo", {"text": "hi"}, "cid-1")]),
                _FakeMessage(content="done"),
            ]
        )
        loop = AgentLoop(client, ToolDispatcher(ToolRegistry([_echo_tool()])))
        loop.run("问题")
        second = client.calls[1]["messages"]
        tool_msgs = _tool_messages(second)
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "cid-1"
        assert tool_msgs[0]["content"] == "echo:hi"

    def test_assistant_message_preserved_as_object(self):
        """assistant 消息必须带 tool_calls，否则模型无法对应结果。"""
        client = _ScriptedClient(
            [
                _FakeMessage(tool_calls=[_tc("echo", {"text": "x"}, "cid-9")]),
                _FakeMessage(content="done"),
            ]
        )
        loop = AgentLoop(client, ToolDispatcher(ToolRegistry([_echo_tool()])))
        loop.run("问题")
        second = client.calls[1]["messages"]
        assistant = [m for m in second if _role_of(m) == "assistant"]
        assert assistant, "缺少 assistant 消息"
        # AIMessage 原样回传，tool_calls 得以保留
        assert assistant[0].tool_calls[0]["id"] == "cid-9"

    def test_tool_failure_still_returns_answer(self, ):
        """工具失败时循环继续，最终答复仍可返回（降级但不中断）。"""
        client = _ScriptedClient(
            [
                _FakeMessage(tool_calls=[_tc("echo", {})]),
                _FakeMessage(content="抱歉，工具未成功。"),
            ]
        )
        loop = AgentLoop(client, ToolDispatcher(ToolRegistry([_echo_tool()])))
        out = loop.run("问题")
        assert out["tool_invocations"][0]["ok"] is False
        assert out["text"] == "抱歉，工具未成功。"

    def test_failure_payload_reaches_model(self):
        client = _ScriptedClient(
            [
                _FakeMessage(tool_calls=[_tc("echo", {})]),
                _FakeMessage(content="done"),
            ]
        )
        loop = AgentLoop(client, ToolDispatcher(ToolRegistry([_echo_tool()])))
        loop.run("问题")
        tool_msg = _tool_messages(client.calls[1]["messages"])[0]
        assert "error" in tool_msg["content"]

    def test_step_limit_raises(self):
        """模型持续调用工具时必须受控中止。"""
        infinite = [
            _FakeMessage(tool_calls=[_tc("echo", {"text": "x"})])
            for _ in range(10)
        ]
        client = _ScriptedClient(infinite)
        loop = AgentLoop(
            client, ToolDispatcher(ToolRegistry([_echo_tool()])), max_steps=3
        )
        with pytest.raises(AgentStepLimitError) as e:
            loop.run("问题")
        assert e.value.code == "agent_step_limit_reached"

    def test_multiple_tools_in_one_turn(self):
        client = _ScriptedClient(
            [
                _FakeMessage(
                        tool_calls=[
                            _tc("echo", {"text": "a"}, "c1"),
                            _tc("echo", {"text": "b"}, "c2"),
                        ]
                    ),
                _FakeMessage(content="都处理完了"),
            ]
        )
        loop = AgentLoop(client, ToolDispatcher(ToolRegistry([_echo_tool()])))
        out = loop.run("问题")
        assert len(out["tool_invocations"]) == 2
        tool_msgs = _tool_messages(client.calls[1]["messages"])
        assert {m["tool_call_id"] for m in tool_msgs} == {"c1", "c2"}

    def test_tools_always_passed_to_model(self):
        """每一轮都应传 tools，模型才能继续决策。"""
        client = _ScriptedClient(
            [
                _FakeMessage(tool_calls=[_tc("echo", {"text": "x"})]),
                _FakeMessage(content="done"),
            ]
        )
        loop = AgentLoop(client, ToolDispatcher(ToolRegistry([_echo_tool()])))
        loop.run("问题")
        for call in client.calls:
            assert len(call["tools"]) == 1

    def test_empty_reply_raises(self):
        from app.llm.errors import LLMError

        client = _ScriptedClient([_FakeMessage(content="   ")])
        loop = AgentLoop(client, ToolDispatcher(ToolRegistry([_echo_tool()])))
        with pytest.raises(LLMError):
            loop.run("问题")

    def test_history_included(self):
        client = _ScriptedClient([_FakeMessage(content="ok")])
        loop = AgentLoop(
            client,
            ToolDispatcher(ToolRegistry([_echo_tool()])),
            history=[("user", "之前的问题")],
        )
        loop.run("新问题")
        msgs = client.calls[0]["messages"]
        assert msgs[0]["content"] == "之前的问题"  # dict 形态
        assert msgs[-1]["content"] == "新问题"      # dict 形态


class TestNoCodeExecution:
    """安全回归：绝不能执行模型提供的代码。

    `security-privacy.md` §4 禁止模型拼接并执行任意 shell/SQL/Python。
    """

    def test_arguments_are_not_evaluated(self):
        tool = _echo_tool()
        # 恶意参数字符串：不应被 eval 执行
        r = ToolDispatcher(ToolRegistry([tool])).execute(
            "echo", '{"text": "__import__(\'os\').system(\'echo pwned\')"}'
        )
        assert r.ok is True
        # 结果只是字符串回显，没有执行
        assert r.content.startswith("echo:")

    def test_unknown_tool_cannot_be_invoked(self):
        d = ToolDispatcher(ToolRegistry([_echo_tool()]))
        for bad in ("shell", "exec", "eval", "__import__"):
            with pytest.raises(ToolNotFoundError):
                d.execute(bad, "{}")

    def test_json_only_parsing(self):
        """参数只走 json.loads，不接受 Python 字面量。"""
        tool = _echo_tool()
        with pytest.raises(ToolArgumentError):
            validate_arguments(tool, "{'text': 'hi'}")  # Python dict 字面量


class TestPersonaPropagation:
    """人设是否真的透传到模型层。

    ## 为什么要单独测这一层

    实测发现过一个**静默失效**：原先
    ``AgentLoop`` 构造的消息里只有 user/assistant/tool，
    **没有 system**，于是 ``BASE_SYSTEM_PROMPT` 里
    「不要编造答案」等契约**从未到达模型**。

    同样的失效也可能发生在人设上——字符串拼好了不等于发出去了。
    故此处断言"传到``invoke_with_tools`` 的 ``persona`` 参数"，
    而不只是断言拼接函数返回了什么。
    """

    def test_人设随问题一起传入模型层(self) -> None:
        from app.llm.persona import TEACHER_SYSTEM_PROMPT

        client = _ScriptedClient([_FakeMessage("答")])
        loop = AgentLoop(client, ToolDispatcher(ToolRegistry([])), persona=TEACHER_SYSTEM_PROMPT)
        loop.run("乙醇的官能团是什么")

        assert client.calls, "未发生模型调用"
        persona = client.calls[0]["persona"]
        assert persona is not None, "人设未传到模型层"
        assert "乙醇的官能团" in persona, "课题应随人设一起传下去"

    def test_未启用人设时传None(self) -> None:
        """关闭人设必须**完全不传**，而不是传空串。

        传空串会让 `compose_system_prompt` 走「strip 后判空」分支，
        行为虽同，但让调用方难以区分"没配"与"配错了"。
        """
        client = _ScriptedClient([_FakeMessage("答")])
        loop = AgentLoop(client, ToolDispatcher(ToolRegistry([])))
        loop.run("问题")

        assert client.calls[0]["persona"] is None

    def test_课题随问题变化(self) -> None:
        """人设里的课题必须跟着当前问题走，不能固定成第一次的。"""
        from app.llm.persona import TEACHER_SYSTEM_PROMPT

        # 两次 run() 各需一条回答——脚本只给一条会在第二轮耗尽
        client = _ScriptedClient([_FakeMessage("答1"), _FakeMessage("答2")])
        loop = AgentLoop(client, ToolDispatcher(ToolRegistry([])), persona=TEACHER_SYSTEM_PROMPT)
        loop.run("苯酚的酸性")
        loop.run("酯化的条件")

        first = client.calls[0]["persona"]
        second = client.calls[-1]["persona"]
        assert "苯酚的酸性" in first
        assert "酯化的条件" in second
