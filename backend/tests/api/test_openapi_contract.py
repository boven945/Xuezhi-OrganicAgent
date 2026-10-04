"""OpenAPI 契约的静态化与漂移检测（F4）。

## 这个测试存在的理由

改接口字段却不更新契约，是**前端静默出错**的根源：
后端测试全绿（它测自己的实现），前端测试也全绿
（它对着旧字段断言），两边都对，只是**彼此不再匹配**。

`docs/interface-contract.md` 是双方共同的约定，
而这份静态契约文件就是它的**机器可读形式**。

## 反向验证是本测试的核心要求

断言"契约一致"很容易——**只要没人改任何东西它就一直成立**。
故本测试必须自己制造漂移，确认检测真的会失败，
否则就是一条恒真断言（本项目已吃过这个亏，见
`docs/development-log-2026-10.md`）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPO_ROOT / "docs" / "api" / "openapi.json"


# ----------------------------------------------------------------------
# 契约文件本身
# ----------------------------------------------------------------------
class TestContractFile:
    def test_contract_file_exists(self) -> None:
        """契约必须入库。

        不入库就没有"共同约定"，前后端只能各自读代码猜。
        """
        assert CONTRACT_PATH.exists(), (
            f"契约文件不存在：{CONTRACT_PATH}。请运行 "
            f"python -m scripts.export_openapi"
        )

    def test_contract_is_valid_json(self) -> None:
        spec = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        assert isinstance(spec, dict)

    def test_contract_has_required_sections(self) -> None:
        """OpenAPI 必备字段。

        缺 ``openapi`` 版本号则下游工具（codegen、diff 工具）
        无法判断该按哪个版本解析。
        """
        spec = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        assert "openapi" in spec, "缺 openapi 版本字段"
        assert "info" in spec, "缺 info 段"
        assert "paths" in spec, "缺 paths 段"
        # 版本须是 3.x——本项目实测 FastAPI 0.142.2 默认输出 3.1.x
        assert spec["openapi"].startswith("3."), (
            f"OpenAPI 版本异常：{spec['openapi']}"
        )

    def test_contract_has_no_none_placeholders(self) -> None:
        """契约里不应出现 ``"title": "None"`` 这类占位。

        常见成因：路由函数没写 ``summary``/``description``，
        FastAPI 用函数名兜底；若函数名也不合理就会出现无意义标题。
        """
        spec = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        for path, methods in spec.get("paths", {}).items():
            for method, op in methods.items():
                if not isinstance(op, dict):
                    continue
                title = op.get("summary")
                assert title not in (None, "", "None"), (
                    f"{method.upper()} {path} 缺 summary"
                )

    def test_all_paths_have_operation_ids(self) -> None:
        """每个操作须有 ``operationId``。

        **为什么重要**：前端代码生成器（如 openapi-typescript、
        orval）用它生成函数名。缺了会导致前端拿不到类型化的方法，
        只能手写请求——那正是契约要消除的做法。
        """
        spec = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        for path, methods in spec.get("paths", {}).items():
            for method, op in methods.items():
                if not isinstance(op, dict) or method.startswith("x-"):
                    continue
                assert op.get("operationId"), (
                    f"{method.upper()} {path} 缺 operationId，"
                    f"前端无法生成类型化客户端"
                )

    def test_operation_ids_are_unique(self) -> None:
        """``operationId`` 必须全局唯一。

        重复会导致前端生成重名函数，静默覆盖其中一个——
        这类问题在契约 diff 里只看得出"没变"，很难排查。
        """
        spec = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        seen: dict[str, str] = {}
        duplicates: dict[str, list[str]] = {}
        for path, methods in spec.get("paths", {}).items():
            for method, op in methods.items():
                if not isinstance(op, dict) or method.startswith("x-"):
                    continue
                oid = op.get("operationId", "")
                loc = f"{method.upper()} {path}"
                if oid in seen:
                    duplicates.setdefault(oid, [seen[oid]]).append(loc)
                else:
                    seen[oid] = loc
        assert not duplicates, f"operationId 重复：{duplicates}"


# ----------------------------------------------------------------------
# 契约与代码的一致性（漂移检测）
# ----------------------------------------------------------------------
class TestContractMatchesCode:
    def test_committed_contract_is_up_to_date(self) -> None:
        """**核心断言**：已提交的契约必须与当前代码一致。

        这条失败意味着：有人改了接口但没更新契约。
        前端可能因此出错，而两边各自的测试都不会报。
        """
        result = subprocess.run(
            [sys.executable, "-m", "scripts.export_openapi", "--check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert result.returncode == 0, (
            "契约与代码不一致。\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}\n"
            "若变更是预期的，请运行：python -m scripts.export_openapi"
        )

    def test_export_is_deterministic(self) -> None:
        """两次导出必须**字节一致**。

        不一致意味着契约文件每次都在变，diff 失去意义——
        审阅者无法分辨「真变了」与「格式抖动」。
        """
        script_dir = REPO_ROOT / "scripts"
        first = subprocess.run(
            [sys.executable, "-m", "scripts.export_openapi", "--out", "o1.json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        second = subprocess.run(
            [sys.executable, "-m", "scripts.export_openapi", "--out", "o2.json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        try:
            assert first.returncode == 0, first.stderr
            assert second.returncode == 0, second.stderr
            p1 = REPO_ROOT / "o1.json"
            p2 = REPO_ROOT / "o2.json"
            assert p1.read_text(encoding="utf-8") == p2.read_text(
                encoding="utf-8"
            ), "两次导出不一致——契约文件有格式抖动，diff 会失真"
        finally:
            # 清理：临时产物不入库（.gitignore 未必覆盖）
            for name in ("o1.json", "o2.json"):
                (REPO_ROOT / name).unlink(missing_ok=True)

    def test_serializer_sorts_keys_and_ends_with_newline(self) -> None:
        """序列化须排序键、缩进 2、末尾换行。

        这三条是**确定性的来源**。缺任何一条，
        ``test_export_is_deterministic`` 迟早会失败。

        **实测踩过（断言自己撒谎）**：原先断言
        ``'\\n  "x": 3'``（2 空格）。但 ``x`` 位于**第二层**，
        ``indent=2`` 下第二层是 **4 空格**——
        正确的期望是 ``'\\n    "x": 3'``。
        写断言时凭想象设定期望值，而不是实际跑一遍看输出。
        """
        sys.path.insert(0, str(REPO_ROOT))
        try:
            from scripts.export_openapi import serialize
        finally:
            sys.path.pop(0)

        payload = {"zebra": 1, "alpha": {"y": 2, "x": 3}}
        out = serialize(payload)
        assert out.endswith("\n"), "末尾须有换行（POSIX 惯例）"
        assert out.index('"alpha"') < out.index('"zebra"'), "键须排序"
        # indent=2 -> 第一层 2 空格、第二层 4 空格
        assert '\n  "alpha": {' in out, "第一层缩进应为 2 空格"
        assert '\n    "x": 3' in out, "第二层缩进应为 4 空格"

    def test_check_mode_detects_drift(self) -> None:
        """**反向验证**：故意改坏契约，``--check`` 必须失败。

        没有这条，上面那条断言就是恒真的——
        只要没人动文件，它会一直通过。
        """
        original = CONTRACT_PATH.read_text(encoding="utf-8")
        tampered = original.replace('"openapi"', '"openapi_TAMPERED"', 1)
        assert tampered != original, "篡改未生效，测试本身有问题"
        CONTRACT_PATH.write_text(tampered, encoding="utf-8")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "scripts.export_openapi", "--check"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=180,
            )
            assert result.returncode == 1, (
                f"契约被篡改后 --check 仍返回 {result.returncode}，"
                f"说明漂移检测无效"
            )
            # 报错须指出这是契约变更，且给出修复方式
            assert "契约已变更" in result.stderr, "报错须说明是契约变更"
            assert "export_openapi" in result.stderr, "报错须给出修复命令"
        finally:
            CONTRACT_PATH.write_text(original, encoding="utf-8")

    def test_check_fails_when_file_missing(self) -> None:
        """契约文件缺失时 ``--check`` 须失败并说明怎么生成。"""
        backup = CONTRACT_PATH.read_text(encoding="utf-8")
        CONTRACT_PATH.unlink()
        try:
            result = subprocess.run(
                [sys.executable, "-m", "scripts.export_openapi", "--check"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=180,
            )
            assert result.returncode == 1
            assert "不存在" in result.stderr
            # 必须告诉人怎么修，否则只报错没有行动
            assert "export_openapi" in result.stderr
        finally:
            CONTRACT_PATH.write_text(backup, encoding="utf-8")


# ----------------------------------------------------------------------
# 契约与实际响应的一致性
# ----------------------------------------------------------------------
class TestContractMatchesResponses:
    """契约须与**真实响应**一致，不只是与代码声明一致。

    上一组测试保证「代码 == 契约」，这组保证「实际返回 == 契约」。
    两者都必要：Pydantic 声明与序列化行为不一致时，
    只有实际响应能暴露。
    """

    def test_answer_response_declares_sources_as_optional_list(self) -> None:
        """``AnswerResponse.sources`` 须是**可选的数组**。

        **实测踩过（断言基于错误假设）**：原先断言
        ``"sources" in required``，理由是「前端依赖它判断是否展示来源区」。
        但查模型发现 :attr:`AnswerResponse.sources` 用的是
        ``Field(default_factory=list)`` —— **有默认值，故契约里正确地
        不列为required**。强制它必填反而与实现不符。

        真正的风险不是「必填与否」，而是**字段整体消失**——
        那样前端读``response.sources`` 会得到 undefined。
        故改为断言三件事：
        1. 该字段**存在于** properties（没被删）
        2. 类型是 array（不是被改成 string/object）
        3. 指向 ``SourceItem``（元素类型没变）

        这三条任意一条被破坏，前端都会出错，且都在此覆盖。
        """
        spec = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        schemas = spec["components"]["schemas"]

        answer = schemas.get("AnswerResponse")
        assert answer is not None, "契约缺少 AnswerResponse schema"
        props = answer.get("properties", {})

        assert "sources" in props, (
            "AnswerResponse.sources 从契约中消失——"
            "前端读该字段会得到 undefined"
        )
        sources = props["sources"]
        assert sources.get("type") == "array", (
            f"sources 应为 array，实际 {sources.get('type')!r}"
        )
        ref = (sources.get("items") or {}).get("$ref", "")
        assert ref.endswith("/SourceItem"), (
            f"sources 元素类型应仍为 SourceItem，实际 {ref!r}"
        )
        assert "SourceItem" in schemas, "契约缺少 SourceItem schema 定义"

    def test_source_item_declares_contract_fields(self) -> None:
        """``SourceItem`` 须含 `interface-contract.md` §3 要求的四项。

        依据：``docs/interface-contract.md`` §3 要求来源含
        "source ID、标题、版本和定位"。少一项前端就无法定位原文。
        """
        spec = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        item = spec["components"]["schemas"].get("SourceItem")
        assert item is not None, "契约缺少 SourceItem schema"
        props = set(item.get("properties", {}))
        # 逐项核对，不写死字段名清单以外的假设
        for needed in ("source_id", "title"):
            assert needed in props, f"SourceItem 缺 {needed}（契约 §3 要求）"

    def test_every_path_declares_responses(self) -> None:
        """每个操作须声明响应，否则前端无从得知返回什么。"""
        spec = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        for path, methods in spec.get("paths", {}).items():
            for method, op in methods.items():
                if not isinstance(op, dict) or method.startswith("x-"):
                    continue
                assert op.get("responses"), (
                    f"{method.upper()} {path} 未声明任何响应"
                )

    def test_error_code_is_enumerated_in_contract(self) -> None:
        """错误码若在契约里枚举，须与代码登记表一致。

        当前实现**未**在 OpenAPI 中枚举错误码
        （FastAPI 不自动把自定义错误写进响应模型），
        故此处只锁定"若将来枚举了则须与代码一致"这一约定。

        ## 两次失败都源于同一个错误做法：按前缀盲抓

        **第一次**：用 ``tool_`` 前缀正则扫全文，抓到
        ``"default": "tool_verified"``——那是
        :class:`~app.chem.models.Verification` 枚举的默认值
        （表示"该结果由工具验证过"），与错误码无关。

        **第二次**：改用显式排除名单，又抓到 ``tool_invocations``——
        那是 :class:`AnswerResponse` 的**字段名**（工具调用记录）。

        两次都说明：**"长得像错误码的字符串"远多于错误码**。
        排除名单是打地鼠，加一个漏一个。

        故改为**结构化遍历**：只认``enum`` 数组里的值
        （错误码若被枚举，必然出现在某个 ``enum`` 里），
        逐个与登记表核对。不猜、不排除。
        """
        sys.path.insert(0, str(REPO_ROOT / "backend"))
        try:
            from app.api.errors import API_ERROR_SPECS, DOMAIN_CODE_SPECS
        finally:
            sys.path.pop(0)

        known = set(API_ERROR_SPECS) | set(DOMAIN_CODE_SPECS)
        spec = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

        # 只从 enum 数组里取候选——错误码若被正式枚举，必然在此
        enums: set[str] = set()

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                raw = node.get("enum")
                if isinstance(raw, list):
                    enums.update(v for v in raw if isinstance(v, str))
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(spec)

        # 契约里枚举出的、看起来像错误码的值
        looks_like_code = {
            v for v in enums
            if "_" in v and v.split("_", 1)[0] in
            {"api", "llm", "rag", "chem", "tool", "agent", "speech"}
        }
        unknown = looks_like_code - known
        assert not unknown, (
            f"契约枚举了未登记的错误码：{sorted(unknown)}——"
            f"前端会按它分支处理，但后端从不返回该码"
        )
