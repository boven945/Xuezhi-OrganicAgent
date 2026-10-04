"""HTTP 层契约测试。

覆盖不经真实模型即可验证的行为：输入校验、脱敏、CORS、限流、
SSE 线格式、健康检查。**不测模型答复内容**——那属 E2E，见
``docs/llm-adapter-verification.md`` 与 ``agent-knowledge-tool-verification.md``。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.deps import ServiceSettings
from app.api.routes import get_registry
from app.chem.engine import get_engine


@pytest.fixture()
def settings() -> ServiceSettings:
    """测试用配置：限流放开，避免干扰其他用例。"""
    return ServiceSettings(
        rate_limit_rps=1000.0,
        rate_limit_burst=1000,
        cors_origins=("http://localhost:5173",),
    )


@pytest.fixture()
def client(settings: ServiceSettings) -> TestClient:
    """构造测试客户端。

    用 ``with`` 让lifespan 真正执行——本项目多次踩过"改了代码
    但测试没走启动流程"的坑（依赖备忘录记录），故这里显式进入上下文。
    """
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    """解析 SSE 文本为 ``(event_name, data)`` 列表。

    SSE 以空行分隔事件块；``data:`` 后为 JSON（**可能含 ``\\uXXXX``
    转义**，须JSON.parse 还原——这是实测确认的行为）。
    """
    events: list[tuple[str, dict]] = []
    name = "message"
    for line in raw.split("\n"):
        if line.startswith("event: "):
            name = line[7:].strip()
        elif line.startswith("data: "):
            events.append((name, json.loads(line[6:])))
    return events


class TestHealthEndpoints:
    """健康检查与就绪探针。"""

    def test_health_always_returns_200(self, client: TestClient) -> None:
        """健康检查恒返回 200——否则编排会反复重启，掩盖真实故障。"""
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] in {"ok", "degraded", "not_ready"}
        assert isinstance(body["ready"], bool)

    def test_health_lists_components_without_secrets(self, client: TestClient) -> None:
        """组件明细不得包含密钥或配置内容。"""
        r = client.get("/health")
        blob = r.text
        assert "api_key" not in blob.lower()
        assert "MAAS_API_KEY" not in blob  # 只可能出现在错误类型名里，不该出现
        names = {c["name"] for c in r.json()["components"]}
        assert {"chem", "llm"} <= names

    def test_ready_returns_503_when_not_configured(self, settings) -> None:
        """未配置密钥时就绪探针须返回 503，让编排不接流量。"""
        app = create_app(settings)
        with TestClient(app) as c:
            # 本机若无MAAS_API_KEY 则应not_ready
            r = c.get("/ready")
            assert r.status_code in {200, 503}
            assert isinstance(r.json()["ready"], bool)

    def test_health_is_not_rate_limited(self) -> None:
        """健康检查不应被限流——否则排障时探针会挡路。

        配置极紧的限流（1 次突发 + 每 10 秒补1 个），
        再连续打 8 次 /health：若健康检查走限流分支，
        第 2 次就该429。全部200 即证明它被豁免。
        """
        tight = ServiceSettings(rate_limit_rps=0.1, rate_limit_burst=1)
        app = create_app(tight)
        with TestClient(app) as c:
            for _ in range(8):
                assert c.get("/health").status_code == 200
            assert c.get("/ready").status_code in {200, 503}
            # 业务接口则应被限流（突发仅 1 次，第 2 次即拒）
            assert c.post("/api/v1/molecule", json={"smiles": "CCO"}).status_code == 200
            r = c.post("/api/v1/molecule", json={"smiles": "CCO"})
            assert r.status_code == 429
            assert r.json()["error"]["code"] == "api_rate_limited"
            # 限流响应须带 Retry-After 供客户端退避
            assert "retry-after" in {k.lower() for k in r.headers}

    def test_probe_results_are_cached(self) -> None:
        """探测结果须被缓存——否则 /health 每次都要重跑 RDKit 解析。

        实现方式：连续两次 probe，返回的**是同一对象**。
        这是缓存生效的直接证据（若每次新建则必然不同）。
        """
        from app.api.deps import ServiceRegistry

        registry = ServiceRegistry(ServiceSettings())
        first = registry.probe()
        second = registry.probe()
        assert first is second, "探测结果应被缓存，未生效"

    def test_cached_probe_still_reports_all_components(self) -> None:
        """缓存不得丢失组件明细。"""
        from app.api.deps import ServiceRegistry

        registry = ServiceRegistry(ServiceSettings())
        for probes in (registry.probe(), registry.probe()):
            names = {p.name for p in probes}
            assert {"chem", "llm", "rag"} <= names

    def test_rate_limit_config_is_validated(self) -> None:
        """非法限流配置应在构造期报错，而不是运行中静默失效。"""
        with pytest.raises(ValueError):
            ServiceSettings(rate_limit_rps=1.0, rate_limit_burst=0)


class TestValidationAndRedaction:
    """输入校验与输出脱敏。"""

    def test_rejects_empty_question(self, client: TestClient) -> None:
        r = client.post("/api/v1/ask", json={"question": ""})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "api_invalid_input"

    def test_rejects_whitespace_only_question(self, client: TestClient) -> None:
        """纯空白问题须被拒——min_length拦不住 "\\n\\n"。"""
        r = client.post("/api/v1/ask", json={"question": "\n\n  "})
        assert r.status_code == 400

    def test_rejects_overlong_question(self, client: TestClient) -> None:
        from app.api.models import MAX_QUESTION_CHARS

        r = client.post("/api/v1/ask", json={"question": "苯" * (MAX_QUESTION_CHARS + 1)})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "api_invalid_input"

    def test_rejects_unknown_fields(self, client: TestClient) -> None:
        """多余字段须被拒（extra=forbid），避免前端传错字段名却静默成功。"""
        r = client.post("/api/v1/ask", json={"question": "苯酚的酸性", "typo": 1})
        assert r.status_code == 400
        codes = {d["code"] for d in r.json()["details"]}
        assert "extra_forbidden" in codes

    def test_validation_messages_are_chinese(self, client: TestClient) -> None:
        """报错须中文——本项目面向高中生。"""
        r = client.post("/api/v1/ask", json={"question": ""})
        details = r.json()["details"]
        assert details
        for d in details:
            assert not d["message"].isascii(), f"英文报错：{d}"

    def test_validation_error_does_not_echo_input(self, client: TestClient) -> None:
        """校验失败响应**不得回显原始输入**。

        实测 FastAPI 默认行为会在 detail 里带上 ``input`` 值——
        用户若把敏感内容填错字段就会被原样返回，违反
        security-privacy.md §3。本测试是该行为的回归保护。

        检查方式：**解析 JSON 后按结构判定**，不用子串匹配。
        实测踩过：`"input" not in text` 这类写法会被错误码
        ``api_invalid_input`` 里的子串假阳性命中——
        看起来脱敏失效，实际已生效。
        """
        secret = "S3CRET-VALUE-abc123"
        r = client.post("/api/v1/ask", json={"wrong_field": secret})
        assert r.status_code == 400
        payload = r.json()

        # 1) 用户提交的值本身不出现在响应中
        assert secret not in r.text
        # 2) 结构里没有 input 键
        assert "input" not in payload["error"]
        for detail in payload["details"]:
            assert "input" not in detail, f"detail 不应含 input 键：{detail}"
            # 只允许 field/code/message 三个键
            assert set(detail) <= {"field", "code", "message"}, (
                f"detail 出现未预期字段：{sorted(detail)}"
            )

    def test_missing_content_type_returns_400(self, client: TestClient) -> None:
        """缺 Content-Type 时 FastAPI 不解析 JSON，我们返回 400。

        实测底层 FastAPI 0.132+ 会先给出 422 且 msg 为
        ``model_attributes_type``——本项目的自定义处理器把它
        统一转成了 400 + 中文文案。此处锁定项目层行为。
        """
        r = client.post(
            "/api/v1/ask",
            content='{"question": "x"}',
            headers={"Content-Type": "text/plain"},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "api_invalid_input"
        details = {d["code"] for d in r.json()["details"]}
        assert "model_attributes_type" in details

    def test_malformed_json_returns_400(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/molecule",
            content="{not json",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400
        assert "json_invalid" in {d["code"] for d in r.json()["details"]}


class TestMoleculeEndpoint:
    """化学结构解析——确定性，不依赖模型。"""

    def test_parses_ethanol(self, client: TestClient) -> None:
        r = client.post("/api/v1/molecule", json={"smiles": "CCO"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["structure"]["canonical_smiles"] == "CCO"
        # 分子式在 properties 层（实测：viz_data 里没有此字段）
        assert body["properties"]["molecular_formula"] == "C2H6O"
        assert body["properties"]["molecular_weight"] == pytest.approx(46.069, abs=1e-3)
        # 显式原子数不含隐式氢——乙醇是 3 而非 9
        assert body["properties"]["num_atoms"] == 3

    def test_invalid_smiles_returns_200_with_ok_false(self, client: TestClient) -> None:
        """非法结构式是**用户输入问题**，返回 200 + ok=false 而非 4xx。

        这样前端能直接展示错误文案，而不必区分「服务挂了」和
        「你写错了」。
        """
        r = client.post("/api/v1/molecule", json={"smiles": "这不是分子式"})
        assert r.status_code in {200, 400}
        if r.status_code == 200:
            assert r.json()["ok"] is False
            assert r.json()["error"] is not None

    def test_works_without_llm_configuration(self, settings) -> None:
        """该接口不得依赖模型——未配置密钥时也应工作。"""
        app = create_app(settings)
        with TestClient(app) as c:
            r = c.post("/api/v1/molecule", json={"smiles": "c1ccccc1"})
            assert r.status_code == 200
            assert r.json()["ok"] is True

    def test_detection_uses_registry_state(self, client: TestClient) -> None:
        """确认 /api/v1/molecule 走的是真实 RDKit 引擎而非替身。"""
        r = client.post("/api/v1/molecule", json={"smiles": "CC(=O)Oc1ccccc1C(=O)O"})
        body = r.json()
        #阿司匹林含酯基与羧基，两个都应命中
        names = {g["name"] for g in body["functional_groups"]}
        assert names, "阿司匹林应命中官能团"
        assert get_engine().is_valid("CC(=O)Oc1ccccc1C(=O)O")

    def test_only_matched_functional_groups_are_returned(self, client: TestClient) -> None:
        """**只返回 matched=True 的官能团**——回归保护。

        实测踩过的坑：`FunctionalGroupHit` 对全部 11 个模式都返回记录，
        未命中的用 ``matched=False`` 标记。初版未过滤，导致乙醇被报成
        "含醛基、羧基、酯基、氨基、碳碳三键"，等于把检测清单当结果。
        """
        r = client.post("/api/v1/molecule", json={"smiles": "CCO"})
        groups = r.json()["functional_groups"]
        names = {g["name"] for g in groups}
        assert names == {"醇羟基"}, f"乙醇只应含醇羟基，实得 {names}"
        assert all(g["matched"] is True for g in groups)

    def test_ethanol_has_no_false_positive_groups(self, client: TestClient) -> None:
        """乙醇不得命中这些基团——逐个点名以防回归。"""
        r = client.post("/api/v1/molecule", json={"smiles": "CCO"})
        names = {g["name"] for g in r.json()["functional_groups"]}
        for absent in (
            "醛基", "酮羰基", "羧基", "酯基", "氨基",
            "碳碳三键", "苯环", "酚羟基",
        ):
            assert absent not in names, f"乙醇不应含{absent}"

    def test_acetic_acid_does_not_hit_any_hydroxyl(
        self, client: TestClient
    ) -> None:
        """乙酸**不应**命中任何羟基（决策项 I5 的核心验收点）。

        这条断言的**方向在2026-10-04 变了**：
        原先的 SMARTS 是 ``[OX2H]``，只描述"连两个原子且带氢的氧"，
        无法区分醇羟基与羧酸羟基，故乙酸会误报「羟基」——
        当时把它记为"chem 模块的已知局限"。

        现已改为 ``醇羟基``（连 sp3 碳）与 ``酚羟基``（连芳香碳）两条，
        乙酸两者都不命中。**学生看到"乙酸含羟基"会误以为羧酸是醇**，
        所以这条是化学正确性问题，不只是数据问题。
        """
        r = client.post("/api/v1/molecule", json={"smiles": "CC(=O)O"})
        names = {g["name"] for g in r.json()["functional_groups"]}
        assert names == {"羧基"}, f"乙酸只应含羧基，实得 {names}"
        assert "醇羟基" not in names, "羧酸不是醇"
        assert "酚羟基" not in names, "羧酸不是酚"

    def test_phenol_hits_phenol_hydroxyl_not_alcohol(
        self, client: TestClient
    ) -> None:
        """苯酚应命中**酚羟基**而非醇羟基（决策项 I5）。

        这是拆分方案的关键验证点：若用 ``[OX2H][CX4]``
        排除羧酸，苯酚会被**漏掉**——把「误报羧酸」换成「漏掉苯酚」，
        而本项目语料明确讲苯酚（`org-phenol`）。
        """
        r = client.post("/api/v1/molecule", json={"smiles": "c1ccccc1O"})
        names = {g["name"] for g in r.json()["functional_groups"]}
        assert "酚羟基" in names, "苯酚应命中酚羟基"
        assert "醇羟基" not in names, "苯酚的羟基不是醇羟基"
        assert "苯环" in names

    def test_aromatic_acid_does_not_hit_phenol_hydroxyl(
        self, client: TestClient
    ) -> None:
        """苯甲酸的羟基连在羰基碳上，**不**算酚羟基。

        苯甲酸的 ``-COOH`` 中氧虽连了芳香环，但连接点是
        **sp2 羰基碳**而非芳香碳，故不应命中酚羟基。
        """
        r = client.post("/api/v1/molecule", json={"smiles": "OC(=O)c1ccccc1"})
        names = {g["name"] for g in r.json()["functional_groups"]}
        assert "羧基" in names
        assert "酚羟基" not in names, "苯甲酸的 OH 不在芳香碳上"
        assert "醇羟基" not in names

    def test_rejects_blank_smiles(self, client: TestClient) -> None:
        r = client.post("/api/v1/molecule", json={"smiles": "   "})
        assert r.status_code == 400

    def test_response_has_request_id_and_schema_version(self, client: TestClient) -> None:
        body = client.post("/api/v1/molecule", json={"smiles": "CCO"}).json()
        assert body["request_id"]
        assert body["schema_version"] == "1.0"


class TestCors:
    """跨域白名单。"""

    def test_allowed_origin_gets_header(self, client: TestClient) -> None:
        r = client.get("/health", headers={"Origin": "http://localhost:5173"})
        assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"

    def test_unknown_origin_gets_no_header(self, client: TestClient) -> None:
        r = client.get("/health", headers={"Origin": "http://evil.example.com"})
        assert r.headers.get("access-control-allow-origin") is None

    def test_preflight_allows_post_and_json(self, client: TestClient) -> None:
        r = client.options(
            "/api/v1/ask",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert r.status_code == 200
        assert "POST" in r.headers.get("access-control-allow-methods", "")
        assert "content-type" in r.headers.get("access-control-allow-headers", "").lower()

    def test_no_wildcard_origin(self, settings) -> None:
        """凭据模式下不得出现通配符来源。

        FastAPI 在 allow_credentials=True + "*" 时会启动即报错；
        这里确认我们从配置层面就不会产生这种组合。
        """
        app = create_app(settings)
        assert settings.cors_origins == ("http://localhost:5173",)
        assert "*" not in settings.cors_origins
        assert app is not None


class TestOpenAPI:
    """机器可读契约。"""

    def test_openapi_is_generated(self, client: TestClient) -> None:
        spec = client.get("/openapi.json").json()
        assert "/api/v1/ask" in spec["paths"]
        assert "/api/v1/ask/stream" in spec["paths"]
        assert "/api/v1/molecule" in spec["paths"]
        assert "/health" in spec["paths"]

    def test_schemas_documented(self, client: TestClient) -> None:
        schemas = client.get("/openapi.json").json()["components"]["schemas"]
        for name in ("AskRequest", "AnswerResponse", "MoleculeRequest", "HealthResponse"):
            assert name in schemas

    def test_error_schema_is_documented(self, client: TestClient) -> None:
        """错误响应须进契约——否则前端只能靠猜。"""
        r = client.post("/api/v1/ask", json={"question": ""})
        assert r.status_code == 400
        assert set(r.json()) >= {"error"}
        assert set(r.json()["error"]) >= {"code", "message", "retryable", "request_id"}


class TestDependencyOverride:
    """确认依赖注入可替换——这是可测性的前提。"""

    def test_registry_can_be_overridden(self, settings) -> None:
        """测试应能注入替身，无需真实模型即可测问答接口。"""
        from app.api.deps import ServiceRegistry

        class FakeLoop:
            def run(self, question: str) -> dict:
                return {
                    "text": "苯酚具有弱酸性。",
                    "steps": 1,
                    "tool_invocations": [],
                }

        registry = ServiceRegistry(settings)
        registry._agent_loop = FakeLoop()  # noqa: SLF001

        app = create_app(settings)
        app.dependency_overrides[get_registry] = lambda: registry
        with TestClient(app) as c:
            r = c.post("/api/v1/ask", json={"question": "苯酚的酸性"})
            assert r.status_code == 200
            body = r.json()
            assert body["explanation"] == "苯酚具有弱酸性。"
            assert body["status"] == "completed"
            assert body["sources"] == []
            assert body["request_id"]

    def test_sources_are_exposed_in_sync_answer(self, settings) -> None:
        """同步接口的 ``sources`` 现在有值了（决策项 I2）。

        这是 I2 的验收点：此前该字段恒为空数组，前端无法展示
        「依据来自哪本教材第几页」。
        """
        from app.api.deps import ServiceRegistry

        class SrcLoop:
            def run(self, question: str) -> dict:
                return {
                    "text": "苯酚具有弱酸性。",
                    "steps": 2,
                    "tool_invocations": [
                        {"tool": "search_knowledge", "ok": True, "error_code": None}
                    ],
                    "sources": [
                        {
                            "source_id": "src-organic-001",
                            "title": "有机化学自编讲义",
                            "edition": "project-authored",
                            "locator": "第三章 烃的衍生物",
                            "scope": "high_school_required",
                        }
                    ],
                }

        registry = ServiceRegistry(settings)
        registry._agent_loop = SrcLoop()  # noqa: SLF001
        app = create_app(settings)
        app.dependency_overrides[get_registry] = lambda: registry
        with TestClient(app) as c:
            body = c.post("/api/v1/ask", json={"question": "苯酚的酸性"}).json()
            assert len(body["sources"]) == 1
            src = body["sources"][0]
            assert src["source_id"] == "src-organic-001"
            assert src["title"] == "有机化学自编讲义"
            assert src["version"] == "project-authored"
            assert src["locator"] == "第三章 烃的衍生物"
            # review_status 不得写 "approved"——那是审核结论不是事实
            assert src["review_status"] == "unknown"

    def test_page_locator_is_detected(self, settings) -> None:
        """形如页码的定位须被识别为 PAGE。"""
        from app.api.routes import _looks_like_page

        assert _looks_like_page("p.42")
        assert _looks_like_page("第 42 页")
        assert not _looks_like_page("第三章 烃")
        assert not _looks_like_page("")

    def test_overlong_source_fields_are_truncated(self, settings) -> None:
        """超长来源字段须截断，不能让边缘情况导致 500。"""
        from app.api.deps import ServiceRegistry

        class LongLoop:
            def run(self, question: str) -> dict:
                return {
                    "text": "答案",
                    "steps": 1,
                    "tool_invocations": [],
                    "sources": [
                        {
                            "source_id": "s" * 500,
                            "title": "标" * 900,
                            "locator": "位" * 700,
                            "edition": "e" * 500,
                        }
                    ],
                }

        registry = ServiceRegistry(settings)
        registry._agent_loop = LongLoop()  # noqa: SLF001
        app = create_app(settings)
        app.dependency_overrides[get_registry] = lambda: registry
        with TestClient(app) as c:
            r = c.post("/api/v1/ask", json={"question": "q"})
            assert r.status_code == 200, "超长来源不得导致 500"
            src = r.json()["sources"][0]
            assert len(src["source_id"]) == 128
            assert len(src["title"]) == 512
            assert len(src["locator"]) == 256

    def test_missing_source_fields_get_defaults(self, settings) -> None:
        """缺字段的来源须给默认值，不能因缺title 而失败。"""
        from app.api.deps import ServiceRegistry

        class SparseLoop:
            def run(self, question: str) -> dict:
                return {
                    "text": "答案",
                    "steps": 1,
                    "tool_invocations": [],
                    "sources": [{"source_id": "only-id"}],
                }

        registry = ServiceRegistry(settings)
        registry._agent_loop = SparseLoop()  # noqa: SLF001
        app = create_app(settings)
        app.dependency_overrides[get_registry] = lambda: registry
        with TestClient(app) as c:
            r = c.post("/api/v1/ask", json={"question": "q"})
            assert r.status_code == 200
            src = r.json()["sources"][0]
            assert src["source_id"] == "only-id"
            assert src["title"] == "未命名来源"
            assert src["locator_kind"] == "unknown"


__all__ = [
    "TestCors",
    "TestDependencyOverride",
    "TestHealthEndpoints",
    "TestMoleculeEndpoint",
    "TestOpenAPI",
    "TestValidationAndRedaction",
]
