"""测试用的环境能力探测。

## 为什么要单独一个模块

本机（Windows + Smart App Control）会按二进制签名拦截部分
第三方原生扩展。**实测 2026-10-04**：RDKit 的 ``rdchem``
被拦截，导致依赖它的测试无法运行。

关键教训：**`import rdkit` 成功不代表 RDKit 可用**——
前者只加载 Python 层，不触碰 C++ 扩展。必须试``from rdkit import Chem``。
本项目第一轮自检就是只查了顶层 ``rdkit``，误判为"可用"。

## 为什么用 skip 而不是让测试失败

被拦是**环境限制**，不是代码缺陷。让测试红着会掩盖真实回归——
这是比「测试失败」更坏的结果：人会开始忽略失败。

但也不能静默跳过：跳过原因必须明确（``pytest -rs`` 可见），
让人知道"这些用例在别的机器上会跑"。

**不适用于生产代码**——服务运行时的降级处理在
``app/api/deps.py::get_agent_loop``，两者是独立机制。
"""

from __future__ import annotations

import importlib

#: 探测结果缓存。避免每个测试用例都重新尝试导入（导入失败有开销）。
_CACHE: dict[str, bool] = {}


def has_chem() -> bool:
    """化学引擎（RDKit）是否可用。

    刻意用 ``from rdkit import Chem`` 而非 ``import rdkit``：
    后者不加载 C++ 扩展，会给出假阳性的"可用"。
    """
    if "chem" not in _CACHE:
        try:
            importlib.import_module("rdkit.Chem")
            _CACHE["chem"] = True
        except Exception:  # noqa: BLE001 - 任何失败都算不可用
            _CACHE["chem"] = False
    return _CACHE["chem"]


def chem_block_reason() -> str:
    """RDKit不可用的原因（供skip 消息用）。

    区分两种情况——处置完全不同：
    - **被应用控制策略拦截**：装也没用，不是缺依赖；
    - **未安装**：pip install 即可。
    """
    try:
        importlib.import_module("rdkit.Chem")
        return ""
    except Exception as exc:  # noqa: BLE001
        if "应用程序控制策略" in str(exc) or "AppLocker" in str(exc):
            return (
                "RDKit 的 C++ 扩展被 Windows 应用控制策略"
                "（Smart App Control / WDAC）按签名拦截。"
                "这不是缺依赖，重装无效；容器内不受主机策略管辖，"
                "或改用免策略的机器。"
            )
        return f"RDKit 未安装或不可用（{type(exc).__name__}）。pip install rdkit"


__all__ = ["chem_block_reason", "has_chem"]
