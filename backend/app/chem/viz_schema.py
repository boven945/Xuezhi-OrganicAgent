"""可视化数据的机器可读 schema。

## 存在的理由：OpenAPI 覆盖不到这里

`docs/interface-contract.md` §5 要求「为 API 与可视化数据分别指定
schema 版本」，但实现后的实测暴露一个缺口：

- ``VisualizationHint.data`` 在 OpenAPI 里是
  ``{"type": "object", "additionalProperties": true}``——
  **对内部结构零约束**。
- 而 ``viz_data`` 有 **12 个顶层字段、4 组嵌套结构**
  （原子表、键表、渲染原子表、渲染键表），字段增减完全不体现在契约里。

后果：后端删掉 ``coords`` 或改了 ``atoms[].index`` 的含义，
OpenAPI 契约检测**全绿**，前端却在运行时才炸。

故本模块提供独立于 OpenAPI 的 JSON Schema，并配契约测试。
两者职责不同：OpenAPI 管**HTTP 形状**，本文件管**渲染数据形状**。

## 为什么放在后端而非前端

权威来源是 :mod:`app.chem.engine`（它生产数据）。
把 schema 放在生产者旁边，才能在**同一个提交**里同时改
"产出什么"和"声明什么"——否则两份定义必然漂移。
前端 ``src/viz/types.ts`` 是它的 TypeScript 投影，
由契约测试比对双方是否一致。

## 版本策略

``$id`` 里的 ``v2`` 与 :data:`VIZ_SCHEMA_ID` 保持一致。
**升版规则**：删字段、改字段类型、改字段语义→ 必须升版；
加可选字段 → 可不升版（但要同步前端类型）。

**语义变更无法被 schema 检出**：如 ``radius`` 单位从 Å 改成 nm，
schema 完全匹配而渲染全错。故 :data:`VIZ_SCHEMA_NOTES`
显式登记这类不可机械检测的风险。
"""

from __future__ import annotations

from typing import Any, Final

#: 可视化数据 schema 的标识。**与 engine.py 产出的 ``schema`` 字段一致**。
#:
#: 刻意放在本模块而非各处硬编码：后端产出、前端校验、
#: 契约测试三处必须引用同一个值。
VIZ_SCHEMA_ID: Final[str] = "molecule-structure/v2"


#: 原子（化学视角，不含显式氢）。
#:
#: ``radius`` 取 RDKit 周期表的**共价半径**（Å），不自己硬编码——
#: 那份表来自实验数据，硬编码等于用近似替换权威数据。
ATOM_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "required": [
        "index", "element", "atomic_number", "radius",
        "outer_electrons", "formal_charge",
        "attached_hydrogens", "is_aromatic",
    ],
    "properties": {
        "index": {"type": "integer", "minimum": 0},
        "element": {"type": "string", "minLength": 1, "maxLength": 8},
        "atomic_number": {"type": "integer", "minimum": 1},
        "radius": {"type": "number", "exclusiveMinimum": 0},
        "outer_electrons": {"type": "integer", "minimum": 0},
        "formal_charge": {"type": "integer"},
        # 隐式氢数（不含显式氢）。乙醇的三个碳分别是 3/2/1。
        "attached_hydrogens": {"type": "integer", "minimum": 0},
        "is_aromatic": {"type": "boolean"},
    },
    # 不允许多余字段：多出来的字段前端会忽略，
    # 让人以为它生效了，实际没有——这比缺字段更难查。
    "additionalProperties": False,
}

#: 键。``order`` 用 float 以表达芳香键的 1.5。
BOND_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "required": ["begin", "end", "order", "is_aromatic"],
    "properties": {
        "begin": {"type": "integer", "minimum": 0},
        "end": {"type": "integer", "minimum": 0},
        # 1 / 1.5 / 2 / 3。用 number 而非 integer：芳香键是 1.5
        "order": {"type": "number", "minimum": 0},
        "is_aromatic": {"type": "boolean"},
    },
    "additionalProperties": False,
}

#: 渲染视角的原子（含显式氢）。
#:
#: 与 :data:`ATOM_SCHEMA` 的差别：多 ``is_explicit_hydrogen``。
#: **坐标与渲染原子表按索引一一对应**，故两者字段必须一致。
RENDER_ATOM_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "required": [
        "index", "element", "atomic_number", "radius",
        "outer_electrons", "formal_charge",
        "attached_hydrogens", "is_aromatic", "is_explicit_hydrogen",
    ],
    "properties": {
        "index": {"type": "integer", "minimum": 0},
        "element": {"type": "string", "minLength": 1, "maxLength": 8},
        "atomic_number": {"type": "integer", "minimum": 1},
        "radius": {"type": "number", "exclusiveMinimum": 0},
        "outer_electrons": {"type": "integer", "minimum": 0},
        "formal_charge": {"type": "integer"},
        # 显式氢表里恒为 0（氢本身无隐式氢）
        "attached_hydrogens": {"type": "integer", "minimum": 0},
        "is_aromatic": {"type": "boolean"},
        "is_explicit_hydrogen": {"type": "boolean"},
    },
    "additionalProperties": False,
}

#: 渲染视角的键。与 :data:`BOND_SCHEMA` 同构。
RENDER_BOND_SCHEMA: Final[dict[str, Any]] = BOND_SCHEMA

#: 坐标。[x, y, z]，单位 Å。
#:
#: **用 `prefixItems` 而非只写 `items: {type: array}`**：
#: 后者不约束长度，前端拿到 ``[1, 2]`` 会在 Three.js 里静默错位。
COORD_SCHEMA: Final[dict[str, Any]] = {
    "type": "array",
    "minItems": 3,
    "maxItems": 3,
    "prefixItems": [
        {"type": "number"},
        {"type": "number"},
        {"type": "number"},
    ],
}

#: ``viz_data`` 的完整 schema。
#:
#: ## 必填与可选的划分依据
#:
#: **必填**（任何情况下都必须有）：``schema`` / ``smiles`` /
#: ``atom_count`` / ``bond_count`` / ``conformer`` / ``conformer_note``。
#:
#: **可选**（构象失败时就没有）：
#: ``coords`` / ``render_atoms`` / ``render_bonds`` /
#: ``has_explicit_hydrogens`` / ``atoms`` / ``bonds``。
#:
#: 划分的意义：``conformer: "failed"`` 是**正常状态**
#: （坐标是增强信息，失败时学生仍应看到结构式），
#: 故这些字段不能是必填——否则失败路径无法表达。
VIZ_DATA_SCHEMA: Final[dict[str, Any]] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": f"https://xuezhi.local/schemas/{VIZ_SCHEMA_ID}.json",
    "title": "分子可视化数据",
    "description": (
        "由后端化学引擎产出的渲染数据。"
        "坐标、render_atoms、render_bonds 三者**索引一一对应**"
        "（均为加氢后的顺序），前端按索引取用，不需再做映射。"
    ),
    "type": "object",
    "required": [
        "schema", "smiles", "atom_count", "bond_count",
        "conformer", "conformer_note",
    ],
    "properties": {
        "schema": {
            "type": "string",
            "const": VIZ_SCHEMA_ID,
            "description": "schema 版本标识。前端据此选择解析逻辑。",
        },
        "smiles": {"type": "string", "minLength": 1},
        "atom_count": {"type": "integer", "minimum": 0},
        "bond_count": {"type": "integer", "minimum": 0},
        "conformer": {
            "type": "string",
            # 实测取值只有两个。``skipped`` 曾是第三种，
            # 但引擎现已不会产出它（见 _embed_conformer）。
            "enum": ["ready", "failed"],
            "description": (
                "构象生成状态。``failed`` **不是异常路径**——"
                "坐标是增强信息，生成失败时学生仍应能看到结构式与官能团。"
            ),
        },
        "conformer_note": {
            "type": "string",
            "description": "面向学生的说明，可直接展示。",
        },
        # 化学视角（不含显式氢）
        "atoms": {"type": "array", "items": ATOM_SCHEMA},
        "bonds": {"type": "array", "items": BOND_SCHEMA},
        # 渲染视角（含显式氢）
        "render_atoms": {"type": "array", "items": RENDER_ATOM_SCHEMA},
        "render_bonds": {"type": "array", "items": RENDER_BOND_SCHEMA},
        "coords": {"type": "array", "items": COORD_SCHEMA},
        "has_explicit_hydrogens": {"type": "boolean"},
    },
    # 顶层也不允许多余字段：多出来的字段前端不会用，
    # 留着只会让人误以为已生效
    "additionalProperties": False,
}


#: **schema 无法检出的风险**（务必随 schema 一起维护）。
#:
#: 这些是**语义漂移**：schema 完全匹配，行为却已改变。
#: 机械检查抓不到，只能靠人工评审 + 渲染实测。
VIZ_SCHEMA_NOTES: Final[tuple[str, ...]] = (
    "radius 单位是 Å。若改成 nm，schema 仍匹配但球体大小全错。",
    "render_atoms 与 coords 按索引一一对应。"
    "若两者顺序不一致（例如一个去氢一个加氢），schema 仍匹配但原子会飘。",
    "is_explicit_hydrogen 区分渲染原子表里的氢。"
    "若去掉该字段，渲染原子数会与坐标数不符——索引错位不会报错，只会飘。",
    "order 为 1.5 表示芳香键。若改成整数枚举，苯环会画不出交替单双键。",
    "颜色映射由前端 CPK 表负责（见 src/viz/elements.ts），"
    "schema 不约束颜色——改配色不会破坏契约，但会影响可读性。",
)


__all__ = [
    "ATOM_SCHEMA",
    "BOND_SCHEMA",
    "COORD_SCHEMA",
    "RENDER_ATOM_SCHEMA",
    "RENDER_BOND_SCHEMA",
    "VIZ_DATA_SCHEMA",
    "VIZ_SCHEMA_ID",
    "VIZ_SCHEMA_NOTES",
]
