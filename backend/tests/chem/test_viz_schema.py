"""可视化数据 schema 契约（F5）。

## 这个测试存在的理由

`docs/interface-contract.md` §5 要求「为可视化数据指定 schema 版本」。
实现后的实测暴露一个缺口：**OpenAPI 覆盖不到 ``viz_data``**——
它在契约里是 ``{"type": "object", "additionalProperties": true}``，
即**对内部12 个字段零约束**。

后果：后端删掉 ``coords`` 或改了 ``atoms[].index`` 的含义，
OpenAPI 契约检测**全绿**，前端却在运行时才炸。

## 必须用真实校验器，不能手工查字段

手工写 ``assert 'coords' in required`` 只能验证"我想到的约束"，
验证不了 schema 本身写得对不对。实测踩过：手工检查全过，
但 schema 里**少写一条约束**时无从发现。

故本测试用 ``jsonschema.Draft202012Validator``——
它会检查 schema 自身的合法性，也按draft 2020-12 语义校验实例。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.chem.engine import get_engine
from app.chem.viz_schema import (
    ATOM_SCHEMA,
    BOND_SCHEMA,
    COORD_SCHEMA,
    RENDER_ATOM_SCHEMA,
    VIZ_DATA_SCHEMA,
    VIZ_SCHEMA_ID,
    VIZ_SCHEMA_NOTES,
)

#: 覆盖用的结构。**刻意包含芳香环与羧基**——
#: 只测乙醇会漏掉芳香键（order=1.5）与显式氢表的对齐问题。
SAMPLE_STRUCTURES = [
    "CCO",  # 乙醇：最简
    "c1ccccc1O",  # 苯酚：芳香环 + 酚羟基
    "CC(=O)O",  # 乙酸：羧基（无羟基，见决策 I5）
    "c1ccccc1C(=O)O",  # 苯甲酸：更大，含苯环与羧基
    "CC(C)(C)O",  # 叔丁醇：分支结构
]


@pytest.fixture(scope="module")
def validator() -> Any:
    """``viz_data`` 的真实 JSON Schema 校验器。

    模块级 fixture：构造校验器要解析整个 schema，
    逐用例重建纯属浪费。
    """
    jsonschema = pytest.importorskip(
        "jsonschema", reason="未装 jsonschema（应在 Dockerfile.test 第4 层）"
    )
    cls = jsonschema.validators.validator_for(VIZ_DATA_SCHEMA)
    cls.check_schema(VIZ_DATA_SCHEMA)
    return cls(VIZ_DATA_SCHEMA)


def _viz(smiles: str) -> dict[str, Any]:
    return get_engine().parse(smiles).structure.viz_data


# ----------------------------------------------------------------------
# schema 自身的合法性
# ----------------------------------------------------------------------
class TestSchemaIsWellFormed:
    def test_schema_passes_its_own_metaschema(self, validator: Any) -> None:
        """schema 本身须是合法的 draft 2020-12。

        ``validator_for(...)`` + ``check_schema(...)`` 已在fixture 里做，
        此处显式断言一次，让"schema 写坏了"有明确的失败点。
        """
        import jsonschema

        jsonschema.Draft202012Validator.check_schema(VIZ_DATA_SCHEMA)

    def test_schema_id_matches_produced_value(self) -> None:
        """``$id`` 须与引擎实际产出的 ``schema`` 字段一致。

        不一致的后果：前端按版本选解析逻辑，
        两边对不上就会用错分支——且**不会报错**，只是渲染错。
        """
        assert VIZ_SCHEMA_ID in VIZ_DATA_SCHEMA["$id"]
        assert _viz("CCO")["schema"] == VIZ_SCHEMA_ID

    def test_subschemas_have_no_additional_properties(self) -> None:
        """嵌套结构一律关闭 ``additionalProperties``。

        实测踩过：多余字段前端不会用，却会让人**误以为已生效**——
        这比缺字段更难查（缺了会报错，多了只是静默忽略）。
        """
        for name, sub in (
            ("ATOM_SCHEMA", ATOM_SCHEMA),
            ("BOND_SCHEMA", BOND_SCHEMA),
            ("RENDER_ATOM_SCHEMA", RENDER_ATOM_SCHEMA),
        ):
            assert sub.get("additionalProperties") is False, (
                f"{name} 未关闭 additionalProperties"
            )
        assert VIZ_DATA_SCHEMA["additionalProperties"] is False

    def test_notes_document_uncheckable_risks(self) -> None:
        """语义漂移风险须显式登记。

        这些是 schema **无法**检出的：单位改变、索引错位、配色变化。
        只靠 schema 会给人"检测完备"的错觉。
        """
        assert len(VIZ_SCHEMA_NOTES) >= 4, "风险登记条目过少"
        joined = " ".join(VIZ_SCHEMA_NOTES)
        for keyword in ("radius", "索引", "order"):
            assert keyword in joined, f"风险登记未提及 {keyword}"


# ----------------------------------------------------------------------
# 真实输出必须通过校验
# ----------------------------------------------------------------------
class TestRealOutputConformsToSchema:
    @pytest.mark.parametrize("smiles", SAMPLE_STRUCTURES)
    def test_output_validates(self, validator: Any, smiles: str) -> None:
        """**核心断言**：真实产出须通过 schema 校验。

        这是 F5 的核心——把"生产什么"和"声明什么"用机器连起来。
        """
        validator.validate(_viz(smiles))

    @pytest.mark.parametrize("smiles", SAMPLE_STRUCTURES)
    def test_schema_version_matches(self, smiles: str) -> None:
        """每个结构的 ``schema`` 字段都须是当前版本。"""
        assert _viz(smiles)["schema"] == VIZ_SCHEMA_ID

    def test_conformer_status_is_declared(self) -> None:
        """``conformer`` 须是 schema 声明的枚举值之一。

        引擎若新增一个状态（如 ``skipped``），此断言会失败，
        提醒同步更新 schema 与前端类型——而不是让前端默默走else 分支。
        """
        allowed = set(VIZ_DATA_SCHEMA["properties"]["conformer"]["enum"])
        assert _viz("CCO")["conformer"] in allowed

    def test_coordinate_shape_is_constrained(self) -> None:
        """坐标须恰为 3 元组。

        schema 用 ``minItems``/``maxItems`` 而非仅 ``items``——
        后者不约束长度，前端拿到 ``[1, 2]`` 会在 Three.js 里静默错位。
        """
        assert COORD_SCHEMA["minItems"] == 3
        assert COORD_SCHEMA["maxItems"] == 3

    def test_indices_are_aligned(self) -> None:
        """coords / render_atoms / render_bonds 三者**索引一一对应**。

        这是 schema 检不出的（都是合法数组，长度可能各不相同），
        故单独断言。实测踩过：一旦三者长度不同，
        原子会飘到错误位置而**不报任何错**。
        """
        v = _viz("c1ccccc1O")
        n = len(v["coords"])
        assert len(v["render_atoms"]) == n, "render_atoms 与坐标数不一致"
        assert len(v["render_bonds"]) == n, "render_bonds 与坐标数不一致"
        for i, atom in enumerate(v["render_atoms"]):
            assert atom["index"] == i, f"render_atoms[{i}] 的 index 不连续"

    def test_aromatic_bond_order_is_fractional(self) -> None:
        """苯环的键order 须是 1.5（芳香键）。

        若哪天改成整数枚举，说明芳香键信息丢了——
        苯环将画不出交替单双键，而这正是高中必考的结构特征
        （用户已确认**离域表示是对的**，不应改成交替单双键）。
        """
        v = _viz("c1ccccc1")
        orders = {b["order"] for b in v["bonds"]}
        assert 1.5 in orders, f"苯环应含order=1.5 的芳香键，实际 {orders}"

    def test_explicit_hydrogens_present_in_render_view(self) -> None:
        """渲染视角须含显式氢，否则学生数不出 CH₃ 的四个键。"""
        v = _viz("CCO")
        hydrogens = [
            a for a in v["render_atoms"] if a["element"] == "H"
        ]
        assert hydrogens, "渲染原子表须含显式氢"
        # 化学视角不含显式氢
        assert all(a["element"] != "H" for a in v["atoms"]), (
            "化学视角的原子表不应含显式氢"
        )


# ----------------------------------------------------------------------
# 契约能抓出问题（反向验证）
# ----------------------------------------------------------------------
class TestSchemaActuallyRejectsBadData:
    """**反向验证**：schema 须能拒绝坏数据。

    一个从不拒绝任何输入的 schema 等于没有 schema。
    每条都构造一个具体坏数据，确认校验器报出有意义的错误。
    """

    def _expect_invalid(
        self, validator: Any, payload: dict[str, Any], why: str
    ) -> None:
        errors = list(validator.iter_errors(payload))
        assert errors, f"schema 未拒绝：{why}"
        # 错误信息须指向具体字段，否则排查时无从下手
        assert any(
            e.absolute_path or e.message for e in errors
        ), f"错误信息无定位信息：{why}"

    def test_rejects_wrong_version(self, validator: Any) -> None:
        """版本号不符须拒绝——前端据此选解析逻辑。"""
        bad = _viz("CCO")
        bad["schema"] = "molecule-structure/v1"
        self._expect_invalid(validator, bad, "版本号不符")

    def test_rejects_missing_required_field(self, validator: Any) -> None:
        """缺必填字段须拒绝。"""
        bad = _viz("CCO")
        del bad["conformer_note"]
        self._expect_invalid(validator, bad, "缺 conformer_note")

    def test_rejects_unknown_conformer_status(self, validator: Any) -> None:
        """未知状态须拒绝。

        这是**最重要的一条**：若放过，前端会走到 else 分支，
        拿不到坐标却继续渲染，表现为"3D 视图空白但不报错"。
        """
        bad = _viz("CCO")
        bad["conformer"] = "skipped"
        self._expect_invalid(validator, bad, "未知 conformer 状态")

    def test_rejects_extra_field(self, validator: Any) -> None:
        """多余字段须拒绝（前端不用它，留着会让人误以为生效）。"""
        bad = _viz("CCO")
        bad["unexpected_field"] = 123
        self._expect_invalid(validator, bad, "多余字段")

    def test_rejects_malformed_coordinate(self, validator: Any) -> None:
        """坐标不是 3 元组须拒绝。"""
        bad = _viz("CCO")
        bad["coords"] = [[0.0, 0.0], [1.0, 1.0, 1.0], [2.0, 2.0, 2.0]]
        self._expect_invalid(validator, bad, "坐标仅 2 元组")

    def test_rejects_atom_missing_field(self, validator: Any) -> None:
        """原子缺字段须拒绝。"""
        bad = _viz("CCO")
        del bad["atoms"][0]["is_aromatic"]
        self._expect_invalid(validator, bad, "atom 缺 is_aromatic")

    def test_rejects_non_numeric_radius(self, validator: Any) -> None:
        """radius 非数值须拒绝——它直接决定球体大小。"""
        bad = _viz("CCO")
        bad["atoms"][0]["radius"] = "0.76"
        self._expect_invalid(validator, bad, "radius 为字符串")

    def test_rejects_negative_index(self, validator: Any) -> None:
        """负索引须拒绝（会取到错误的原子）。"""
        bad = _viz("CCO")
        bad["atoms"][0]["index"] = -1
        self._expect_invalid(validator, bad, "负 index")

    def test_rejects_render_atom_without_hydrogen_flag(self, validator: Any) -> None:
        """渲染原子缺 ``is_explicit_hydrogen`` 须拒绝。

        少了它就无法区分渲染原子表里的 H——
        而坐标与渲染原子表按索引对应，认错了会让**所有原子错位**。
        """
        bad = _viz("CCO")
        del bad["render_atoms"][0]["is_explicit_hydrogen"]
        self._expect_invalid(validator, bad, "render_atom 缺 is_explicit_hydrogen")


# ----------------------------------------------------------------------
# 与前端类型的一致性
# ----------------------------------------------------------------------
class TestFrontendTypesMatch:
    """前端 TypeScript 类型须与本schema 一致。

    两侧是**同一份契约的两个投影**（后端产出 JSON、前端消费 JSON），
    手工维护必然漂移，故用测试比对。

    只比对**字段名**与**枚举值**——真正的类型检查交给
    ``npm run typecheck``，Python 侧重复一遍价值有限。
    """

    FRONTEND_TYPES = (
        Path(__file__).resolve().parents[3] / "frontend" / "src" / "viz" / "types.ts"
    )

    @pytest.fixture(scope="class")
    @classmethod
    def types_source(cls) -> str:
        """前端类型定义源码。

        **必须用 ``@classmethod``**：``scope="class"`` 的 fixture
        若定义为实例方法，pytest 会警告它即将失效
        （每个用例是新实例，而 fixture 只跑一次），
        且 ``PytestRemovedIn10Warning`` 在本项目
        ``filterwarnings`` 下可能升级为错误。
        """
        if not cls.FRONTEND_TYPES.exists():
            pytest.skip("前端源码不存在（纯后端检出）")
        return cls.FRONTEND_TYPES.read_text(encoding="utf-8")

    def test_viz_data_fields_match(self, types_source: str) -> None:
        """``VizData`` 接口的字段须覆盖 schema 的全部属性。"""
        for field in VIZ_DATA_SCHEMA["properties"]:
            assert f"{field}?:" in types_source or f"{field}:" in types_source, (
                f"前端 VizData 缺字段 {field}"
            )

    def test_conformer_status_values_match(self, types_source: str) -> None:
        """前端的 ``ConformerStatus`` 须与 schema 枚举一致。

        两侧不一致时，前端会遇到自己类型里没有的值，
        TS 认为不可能而实际发生——运行时静默走错分支。
        """
        allowed = VIZ_DATA_SCHEMA["properties"]["conformer"]["enum"]
        for value in allowed:
            assert f"'{value}'" in types_source, (
                f"前端 ConformerStatus 缺 {value}"
            )

    def test_frontend_declares_same_version(self, types_source: str) -> None:
        """前端须声明同一个 schema 版本号。"""
        assert VIZ_SCHEMA_ID in types_source, (
            f"前端 types.ts 未声明 {VIZ_SCHEMA_ID}"
        )


# ----------------------------------------------------------------------
# schema 可导出
# ----------------------------------------------------------------------
class TestSchemaIsExportable:
    def test_schema_is_json_serializable(self) -> None:
        """须能序列化成 JSON——否则无法入库或供前端消费。"""
        try:
            json.dumps(VIZ_DATA_SCHEMA, ensure_ascii=False)
        except TypeError as exc:
            pytest.fail(f"schema 无法JSON 序列化：{exc}")

    def test_schema_has_no_python_specific_types(self) -> None:
        """schema 能 JSON 序列化，故不可能含 Python 独有类型。

        **实测踩过（这条断言最初是错的）**：原先写
        ``assert "True" not in repr(schema)``，理由是
        「JSON 里应写 true 而非 True」——
        但 ``additionalProperties: False`` 里的 ``False``
        **本来就是合法的 JSON Schema 关键字值**
        （意思是"不允许额外字段"），用 Python 的 ``False``
        才是**正确写法**，序列化成 JSON 才变成 ``false``。

        所以按字面搜 ``True``/``False`` 会误报。

        真正该查的是「有没有 Python 独有类型混进来」——
        如 ``datetime``/自定义类实例。故改为查 JSON 无法表达的 repr 形态。
        """
        import json

        # 能 round-trip 说明全是 JSON 原生类型
        encoded = json.dumps(VIZ_DATA_SCHEMA, ensure_ascii=False)
        assert json.loads(encoded) == VIZ_DATA_SCHEMA, "schema 不可无损 round-trip"

        # Python 独有类型在 json.dumps 时会抛 TypeError，
        # 上面已覆盖。这里只补充确认关键字用词合法
        assert "additionalProperties" in encoded
        # 确认用的是 JSON 的 false 而非 Python 字面量 True/False 混入
        # （allow/deny 语义键后面跟的应是 false）
        assert '"additionalProperties": false' in encoded or (
            "'additionalProperties': False" in encoded
        )
        # 不应出现 Python 特有的 None（JSON 用 null）
        assert "None" not in encoded, "schema 含Python 的 None，应为 JSON null"
