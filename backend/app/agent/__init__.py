"""Agent 编排层。

职责边界见 `docs/architecture.md` §2：任务规划、工具选择、参数校验、
结果回传、超时和失败隔离。

关键设计约束（均来自实测与文档，非假设）：

- **工具白名单是强制边界**（`architecture.md` §5）。模型提出的任何调用
  都必须经调度层校验，未注册的工具一律拒绝。
- **禁止执行模型生成的任意代码**（`security-privacy.md` §4）。本层只做
  JSON 参数解析与受控函数调用，不 eval、不拼接 shell、不执行模型返回的脚本。
- **不使用 tool_choice 精确点名**。实测（2026-10-03）openPangu 的
  ``tool_choice`` 仅接受 ``none`` / ``auto`` / ``required``，传具体的
  ``{"type":"function",...}`` 会报 ``ModelArts.81001``。因此工具选择
  依赖白名单收敛候选 + 提示词引导，而非强制点名。
- **工具失败以结构化状态返回**（`architecture.md` §5）。不可用工具
  不得被静默替换为模型臆造结果。
"""

from .errors import (
    AgentError,
    ToolError,
    ToolNotFoundError,
    ToolArgumentError,
    ToolExecutionError,
    ToolTimeoutError,
    AgentStepLimitError,
)
from .tools import (
    Tool,
    ToolRegistry,
    ToolResult,
    build_chem_tools,
    validate_arguments,
)
from .dispatcher import AgentLoop, ToolDispatcher
from .knowledge_tools import build_knowledge_tools

__all__ = [
    "AgentError",
    "ToolError",
    "ToolNotFoundError",
    "ToolArgumentError",
    "ToolExecutionError",
    "ToolTimeoutError",
    "AgentStepLimitError",
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "build_chem_tools",
    "build_knowledge_tools",
    "validate_arguments",
    "ToolDispatcher",
    "AgentLoop",
]
