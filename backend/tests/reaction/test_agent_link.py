"""Agent 机理联动（P4）的单测。

覆盖三条链路，**每条都必须实跑，不许只断言"函数存在"**：

1. ``find_reaction_mechanism`` 工具：拿**真实** ``data/reactions/candidates.json``
   做关键词匹配，验证加权打分能选中正确的反应，且未收录时返回 ``found=false``。
2. :func:`_extract_reaction_hints`：工具 JSON → 可视化提示 dict。
   重点测**未命中 / 非法形状**不得产出提示（``interface-contract.md`` §3
   「无来源时为空，不伪造」对可视化同理）。
3. :class:`AgentLoop`：用假LLM 驱动真实 ``ToolDispatcher``，验证
   ``run()`` / ``stream()`` 的 ``done`` 事件确实带出 ``visualizations``——
   这是前端联动的唯一入口，漏了就等于功能没接上。

## 为什么必须有第 4 组（反向验证）

本机 WDAC 会按签名拦截 RDKit 的 C++ 扩展。若把
``build_reaction_tools()`` 误放进 ``get_agent_loop`` 的化学降级 ``try`` 块内，
**在这台机器上全部测试依然全绿**（因为恰好没触发），
而部署到拦截环境后机理联动会整体失效——正是本项目已两次踩过的
「默认值合法 ⇒ 静默失效」同型缺陷。故此处**显式断言工具注册在降级块之外**。
"""
from __future__ import annotations

import ast
import inspect
import json
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent.dispatcher import AgentLoop, ToolDispatcher, _extract_reaction_hints
from app.agent.reaction_tools import _match, build_reaction_tools
from app.agent.tools import ToolRegistry
from app.api.deps import ServiceRegistry
from app.api.models import AnswerResponse, VisualizationHint
from app.reaction.library import ReactionLibrary

_DATA = Path(__file__).resolve().parents[3] / "data" / "reactions" / "candidates.json"


@pytest.fixture()
def lib() -> ReactionLibrary:
    return ReactionLibrary.load(_DATA)


# ---------------------------------------------------------------------------
# 1. 关键词匹配（真实反应库）
# ---------------------------------------------------------------------------
def test_match_picks_correct_reaction(lib):
    cases = {
        "乙烯和溴的加成反应机理是怎样的": "addition-ethene-bromine",
        "溴乙烷为什么能发生水解": "substitution-sn2-bromoethane-hydroxide",
        "酯化反应是怎么断键成键的": "esterification-acetic-acid-ethanol",
        "乙醇和乙酸的酯化机理": "esterification-acetic-acid-ethanol",
        "SN2 反应的立体翻转过程": "substitution-sn2-bromoethane-hydroxide",
    }
    for query, expected in cases.items():
        hit = _match(query, lib)
        assert hit is not None, f"未匹配：{query}"
        assert hit["reaction_id"] == expected, f"{query} 选错了反应"


def test_match_scores_by_specificity(lib):
    """打分必须选**更具体**的词，而非库内排列顺序。

    反例：若改回"首条命中"，则库内第一条（加成）会通吃
    一切含"加成/溴"的 query，包括本该走酯化的问法。
    """
    hit = _match("乙酸乙酯是怎么生成的", lib)
    assert hit["reaction_id"] == "esterification-acetic-acid-ethanol"


def test_match_returns_none_when_unrelated(lib):
    assert _match("苯酚的酸性为什么比碳酸弱", lib) is None
    assert _match("", lib) is None


# ---------------------------------------------------------------------------
# 2. 工具层
# ---------------------------------------------------------------------------
def test_tool_returns_found_false_for_unrelated(lib):
    tools = build_reaction_tools(library=lib)
    assert [t.name for t in tools] == ["find_reaction_mechanism"]
    payload = json.loads(tools[0].handler(query="苯酚的酸性为什么比碳酸弱"))
    assert payload == {"found": False}


def test_tool_payload_carries_id_and_name(lib):
    tool = build_reaction_tools(library=lib)[0]
    payload = json.loads(tool.handler(query="乙烯和溴加成的机理"))
    assert payload["found"] is True
    assert payload["reaction_id"] == "addition-ethene-bromine"
    assert payload["name"]


def test_tool_schema_is_openai_compatible(lib):
    """注册进 Agent 前先验形状：缺 required 会被 dispatcher 直接拒参。"""
    schema = build_reaction_tools(library=lib)[0].to_openai_schema()
    fn = schema["function"]
    assert fn["name"] == "find_reaction_mechanism"
    assert fn["parameters"]["required"] == ["query"]
    assert "机理" in fn["description"]


# ---------------------------------------------------------------------------
# 3. 提示提取（Agent JSON → VisualizationHint）
# ---------------------------------------------------------------------------
def test_extract_hint_from_found_payload():
    hint = _extract_reaction_hints(
        json.dumps({"found": True, "reaction_id": "r1", "name": "加成"}, ensure_ascii=False)
    )
    assert hint is not None
    assert hint["kind"] == "reaction_mechanism"
    assert hint["data"]["reaction_id"] == "r1"
    assert hint["source"] == "tool_verified"
    # 必须能通过契约模型校验（前端按此解析）
    assert VisualizationHint.model_validate(hint).kind == "reaction_mechanism"


@pytest.mark.parametrize(
    "payload",
    [
        json.dumps({"found": False}),
        "not json at all",
        json.dumps({"found": True}),  # 缺 reaction_id
        json.dumps({"found": True, "reaction_id": ""}),
        json.dumps({"found": True, "reaction_id": 123}),
    ],
)
def test_extract_hint_never_invents(payload):
    """未命中/畸形一律None——**绝不编造动画**。"""
    assert _extract_reaction_hints(payload) is None


# ---------------------------------------------------------------------------
# 4. AgentLoop 端到端（假 LLM + 真 dispatcher）
# ---------------------------------------------------------------------------
class _FakeLLM:
    """按脚本回放 tool_calls 的最小 LLM 替身。

    ``supports_streaming=False`` 让 AgentLoop 直接走``invoke_with_tools``，
    避开流式合并路径——本组只验证"工具结果被提取进 done"，
    不重复验证流式合并（那部分由 dispatcher 自身测试覆盖）。
    """

    supports_streaming = False

    def __init__(self, script: list[Any]) -> None:
        self._script = script
        self._i = 0

    def invoke_with_tools(self, messages, tools, persona=None):
        msg = self._script[self._i]
        self._i += 1
        return msg


def _tool_call_msg(name, args, call_id="c1"):
    return SimpleNamespace(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": call_id, "type": "tool_call"}
        ],
    )


def _final_msg(text):
    return SimpleNamespace(content=text, tool_calls=[])


def test_agent_loop_surfaces_visualization(lib):
    tools = build_reaction_tools(library=lib)
    registry = ToolRegistry(tools)
    llm = _FakeLLM(
        [
            _tool_call_msg("find_reaction_mechanism", {"query": "乙烯和溴加成的机理"}),
            _final_msg("加成反应中双键断裂…"),
        ]
    )
    loop = AgentLoop(llm, ToolDispatcher(registry))
    result = loop.run("乙烯和溴的加成机理是什么")

    assert result["visualizations"], "机理提示未透出——前端联动会失效"
    hint = result["visualizations"][0]
    assert hint["kind"] == "reaction_mechanism"
    assert hint["data"]["reaction_id"] == "addition-ethene-bromine"
    # 工具调用记录仍要保留（诊断用）
    assert [i["tool"] for i in result["tool_invocations"]] == ["find_reaction_mechanism"]
    assert result["visualizations"][0]["source"] == "tool_verified"


def test_agent_loop_done_event_carries_visualization(lib):
    """stream() 的 done 事件必须带 visualizations——SSE 路径靠它。"""
    registry = ToolRegistry(build_reaction_tools(library=lib))
    loop = AgentLoop(
        _FakeLLM(
            [
                _tool_call_msg("find_reaction_mechanism", {"query": "酯化反应的机理"}),
                _final_msg("酯化先断羧基的 C–O…"),
            ]
        ),
        ToolDispatcher(registry),
    )
    events = list(loop.stream("酯化反应机理"))
    done = [e for e in events if e["type"] == AgentLoop.EVENT_DONE]
    assert len(done) == 1
    assert done[0]["visualizations"][0]["data"]["reaction_id"] == (
        "esterification-acetic-acid-ethanol"
    )


def test_agent_loop_no_visualization_when_tool_misses(lib):
    """工具 found=false 时不得产生任何提示（前端退回文本讲解）。"""
    registry = ToolRegistry(build_reaction_tools(library=lib))
    loop = AgentLoop(
        _FakeLLM(
            [
                _tool_call_msg("find_reaction_mechanism", {"query": "苯酚的酸性"}),
                _final_msg("苯酚…"),
            ]
        ),
        ToolDispatcher(registry),
    )
    assert loop.run("苯酚酸性为何弱于碳酸")["visualizations"] == []


def test_agent_loop_ignores_failed_tool(lib):
    """工具失败（未注册等）时不得产出提示——错误 JSON 不能被当成命中。"""
    registry = ToolRegistry(build_reaction_tools(library=lib))
    loop = AgentLoop(
        _FakeLLM(
            [
                _tool_call_msg("find_reaction_mechanism", {"query": "加成机理", "extra": 1}),
                _final_msg("…"),
            ]
        ),
        ToolDispatcher(registry),
    )
    result = loop.run("加成机理")
    assert result["visualizations"] == []
    assert result["tool_invocations"][0]["ok"] is False


# ---------------------------------------------------------------------------
# 5. 反向验证：注册位置（见模块 docstring）
# ---------------------------------------------------------------------------
def _call_indent(func_name: str) -> int | None:
    """用 **AST** 定位真实调用点的缩进。

    ## 为什么必须用 AST 而不是遍历文本行

    初版按文本搜索，连续踩了三次坑：
    1. 匹配到注释行（本文件与源码里都有说明这两个名字的注释）；
    2. 匹配到 docstring 里的 ``此时 ``build_chem_tools()`` 抛 ...``（缩进 8），
       而真实调用在缩进 16——**判据方向直接反了**。

    **注释与 docstring 都会污染文本搜索**，而它们恰恰是最该解释这段代码的内容。
    AST 只看语法节点，天然排除二者。

    **须先 dedent**：:func:`inspect.getsource` 对方法返回的是**方法体**，
    保留原有缩进，直接 ``ast.parse`` 会抛 ``IndentationError``（实测踩过）。

    Returns:
        调用点的 ``col_offset``；找不到返回 ``None``。
    """
    source = textwrap.dedent(inspect.getsource(ServiceRegistry.get_agent_loop))
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == func_name
        ):
            return node.col_offset
    return None


def test_reaction_tools_registered_outside_chem_fallback():
    """``build_reaction_tools()`` 必须在化学降级try 块**之外**调用。

    放进块内时，本机（chem 可用）测试全绿，部署到 RDKit 被拦截的环境
    才暴露——静默失效。故这里直接对源码结构断言。

    **判据用相对缩进而非魔法数字**：与 ``build_chem_tools()``（已知在try 块内）
    比对。这样重构调整整体缩进时本测试不会误报，
    而"是否在 try 块内"这个真正的意图仍被守住。
    """
    chem_indent = _call_indent("build_chem_tools")
    react_indent = _call_indent("build_reaction_tools")
    assert chem_indent is not None, "未找到化学工具注册点，源码结构已变"
    assert react_indent is not None, "get_agent_loop 未注册机理工具"

    assert react_indent < chem_indent, (
        f"机理工具注册在 try 块内（缩进 {react_indent} ≥ 化学工具的 "
        f"{chem_indent}），RDKit 不可用时会被一起吞掉"
    )


# ---------------------------------------------------------------------------
# 6. API 契约：visualization 落到 AnswerResponse
# ---------------------------------------------------------------------------
def test_answer_response_accepts_visualization():
    resp = AnswerResponse(
        request_id="r",
        status="completed",
        explanation="加成反应…",
        visualization=[
            {
                "kind": "reaction_mechanism",
                "data": {"reaction_id": "addition-ethene-bromine", "name": "加成"},
                "source": "tool_verified",
            }
        ],
    )
    hint = resp.visualization[0]
    assert hint.kind == "reaction_mechanism"
    assert hint.data["reaction_id"] == "addition-ethene-bromine"


def test_answer_response_defaults_to_empty():
    """无命中时恒为空列表而非 None——前端不必处理两种形态。"""
    resp = AnswerResponse(request_id="r", status="completed")
    assert resp.visualization == []