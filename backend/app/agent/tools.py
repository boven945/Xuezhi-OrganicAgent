"""工具定义与注册表。

安全设计（`security-privacy.md` §4）：

- 工具是**服务端预先注册的受控函数**，不是模型提供的代码。
  模型只能"点名调用"，不能定义或修改工具实现。
- 参数经 JSON 解析后逐字段校验类型与范围，**不做 eval、不执行表达式**。
- 每个工具有独立超时，超时按受控错误返回。
- 执行结果为结构化数据，不含可执行内容。

实测依据（2026-10-03，openpangu-2.0-flash）：
``tool_calls[i].function.arguments`` 是 **JSON 字符串**（非 dict），
需 ``json.loads`` 解析；``id`` 形如 ``chatcmpl-tool-<hex>``，
回填时须原样传递。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from .errors import (
    ToolArgumentError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolTimeoutError,
)

#: 类型名到 Python 类型的映射。刻意只支持少数基础类型——
#: 不引入任意类型求值，避免模型构造出危险参数。
_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


@dataclass(frozen=True, slots=True)
class Tool:
    """一个已注册的工具。

    Attributes:
        name: 工具名，模型据此调用。必须唯一。
        description: 给模型看的功能说明，影响模型是否选择调用它。
        parameters: JSON Schema 描述的参数字典。
        handler: 服务端实现。**不来自模型**。
        timeout: 单次执行超时（秒）。
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]
    timeout: float = 15.0

    def to_openai_schema(self) -> dict[str, Any]:
        """转为 OpenAI ``tools`` 数组中的一项。

        实测该字段结构可用（openpangu-2.0-flash 接受并正确调用）。
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True, slots=True)
class ToolResult:
    """工具执行结果。

    ``ok=False`` 时 ``error_message`` 面向用户、``error_code`` 稳定，
    用于让模型知道"工具失败了"而不是"没有结果"——
    这是 `architecture.md` §5「不可用服务不得被静默替换」的实现方式。
    """

    ok: bool
    content: str = ""
    error_code: str | None = None
    error_message: str | None = None
    #: 来源标记：工具产出 vs 模型推断。本模块的 content 一律为 tool_verified。
    source: str = "tool_verified"

    def to_model_payload(self) -> str:
        """转为回填给模型的内容。

        失败时把错误明确告诉模型，让它在答复中如实说明，
        而不是猜测结果。
        """
        if self.ok:
            return self.content
        return json.dumps(
            {"error": self.error_code, "message": self.error_message},
            ensure_ascii=False,
        )


class ToolRegistry:
    """工具白名单。

    只有显式注册的工具可被调用（`architecture.md` §5）。
    """

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools:
            self.register(t)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具名重复：{tool.name}")
        if not tool.name or not tool.name.replace("_", "").isalnum():
            raise ValueError(f"工具名不合法：{tool.name!r}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        """按名取工具。

        Raises:
            ToolNotFoundError: 未注册。**这是白名单边界，不静默通过。**
        """
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFoundError(name, tuple(sorted(self._tools)))
        return tool

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def to_openai_tools(self) -> list[dict[str, Any]]:
        """生成 ``tools`` 参数。只暴露已注册工具。"""
        return [t.to_openai_schema() for t in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools


# ----------------------------------------------------------------------
# 参数校验
# ----------------------------------------------------------------------
def validate_arguments(
    tool: Tool, raw_arguments: str | Mapping[str, Any]
) -> dict[str, Any]:
    """校验并归一化工具参数。

    Args:
        tool: 目标工具。
        raw_arguments: 模型给出的参数。实测为 **JSON 字符串**，
            此处同时接受 dict 以便测试与内部调用。

    Returns:
        归一化后的参数字典。

    Raises:
        ToolArgumentError: JSON 解析失败、缺必填项、类型不符、含未声明字段。
    """
    if isinstance(raw_arguments, str):
        text = raw_arguments.strip()
        if not text:
            raise ToolArgumentError(f"工具 {tool.name} 的参数为空。")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            # 不回显原始参数：可能含学生输入或注入内容
            raise ToolArgumentError(
                f"工具 {tool.name} 的参数不是合法 JSON。",
                detail=f"JSONDecodeError at line {exc.lineno}",
            ) from None
    elif isinstance(raw_arguments, Mapping):
        parsed = dict(raw_arguments)
    else:
        raise ToolArgumentError(
            f"工具 {tool.name} 的参数类型不受支持。"
        )

    if not isinstance(parsed, dict):
        raise ToolArgumentError(f"工具 {tool.name} 的参数必须是 JSON 对象。")

    schema = tool.parameters or {}
    properties: dict[str, Any] = schema.get("properties", {}) or {}
    required: list[str] = list(schema.get("required", []) or [])

    # 未声明字段直接拒绝：避免模型传入意外参数被忽略而不知情
    extra = set(parsed) - set(properties)
    if extra:
        raise ToolArgumentError(
            f"工具 {tool.name} 收到了未声明的参数：{'、'.join(sorted(extra))}。"
        )

    missing = [k for k in required if k not in parsed]
    if missing:
        raise ToolArgumentError(
            f"工具 {tool.name} 缺少必填参数：{'、'.join(missing)}。"
        )

    normalized: dict[str, Any] = {}
    for key, value in parsed.items():
        spec = properties.get(key, {})
        expected = spec.get("type")
        if expected and expected in _JSON_TYPES:
            allowed = _JSON_TYPES[expected]
            # bool 是 int 的子类，需单独排除，否则 True 会被当成整数通过
            if expected in ("number", "integer") and isinstance(value, bool):
                raise ToolArgumentError(
                    f"参数 {key} 应为 {expected}，实际是布尔值。"
                )
            if not isinstance(value, allowed):
                raise ToolArgumentError(
                    f"参数 {key} 应为 {expected}。"
                )
        normalized[key] = value

    return normalized


# ----------------------------------------------------------------------
# 内置化学工具
# ----------------------------------------------------------------------
def build_chem_tools() -> list[Tool]:
    """构造化学工具集，桥接 ``backend-chem`` 模块。

    与 :mod:`app.chem` 的边界划分（`architecture.md` §2）：
    RDKit 只做结构解析与客观属性计算，**不判断反应机理**
    （`product-scope.md` §5）。因此这里**不提供**"判断反应是否发生"之类的工具。
    """
    from app.chem.errors import ChemError
    from app.chem.engine import ChemEngine

    engine = ChemEngine()

    def parse_smiles(smiles: str) -> str:
        """解析 SMILES 分子表达式，返回分子式、分子量与官能团。

        此工具只确认结构合法性与客观属性，不代表反应机理已被验证。
        """
        result = engine.parse(smiles)
        payload: dict[str, Any] = {
            "canonical_smiles": result.structure.canonical_smiles,
            "properties": result.properties.to_dict(),
            "functional_groups": [
                {"name": g.name, "matched": g.matched}
                for g in result.functional_groups
            ],
            "notes": list(result.notes),
            "verification": result.verification.value,
        }
        return json.dumps(payload, ensure_ascii=False)

    def describe_molecule(smiles: str) -> str:
        """用一句话描述分子的结构特征（基于客观计算结果）。"""
        result = engine.parse(smiles)
        p = result.properties
        groups = [g.name for g in result.functional_groups if g.matched]
        parts = [
            f"分子式 {p.molecular_formula}",
            f"分子量约 {p.molecular_weight:.2f}",
        ]
        if p.num_rings:
            parts.append(f"含 {p.num_rings} 个环")
        if groups:
            parts.append(f"含官能团：{'、'.join(groups)}")
        return "；".join(parts) + "。"

    def _guard(fn: Callable[[str], str]) -> Callable[[str], str]:
        """把化学模块的受控错误转成工具层错误。

        这样上层能区分"工具失败"与"模型生成失败"，
        满足 `architecture.md` §5 的结构化失败要求。
        """

        def wrapper(smiles: str) -> str:
            try:
                return fn(smiles)
            except ChemError as exc:
                raise ToolExecutionError(exc.user_message, detail=exc.code) from None
            except Exception as exc:  # 防御性：不让底层异常泄漏
                raise ToolExecutionError(
                    "化学工具执行失败。", detail=type(exc).__name__
                ) from None

        return wrapper

    return [
        Tool(
            name="parse_smiles",
            description=(
                "解析 SMILES 分子表达式，返回规范化 SMILES、分子式、分子量、"
                "环数、杂原子数与命中的官能团列表。"
                "仅用于确认结构合法性与客观属性，不代表反应机理已被验证。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "smiles": {
                        "type": "string",
                        "description": "SMILES 分子表达式，例如 CCO 表示乙醇",
                    }
                },
                "required": ["smiles"],
            },
            handler=_guard(parse_smiles),
            timeout=15.0,
        ),
        Tool(
            name="describe_molecule",
            description=(
                "根据 SMILES 表达式用一句话描述分子结构特征"
                "（分子式、分子量、环数、官能团）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "smiles": {
                        "type": "string",
                        "description": "SMILES 分子表达式，例如 c1ccccc1 表示苯",
                    }
                },
                "required": ["smiles"],
            },
            handler=_guard(describe_molecule),
            timeout=15.0,
        ),
    ]


__all__ = [
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "validate_arguments",
    "build_chem_tools",
]
