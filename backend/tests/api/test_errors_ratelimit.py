"""限流器与错误映射的单元测试。

这两个模块是纯逻辑，不依赖 FastAPI 运行时，可独立快速测试。
"""

from __future__ import annotations

import pytest

from app.agent.errors import AgentStepLimitError, ToolTimeoutError
from app.api.errors import (
    API_ERROR_SPECS,
    API_MESSAGES,
    RETRYABLE_CODES,
    build_error_payload,
    classify,
    public_message,
)
from app.api.ratelimit import RateLimiter
from app.chem.errors import InvalidStructureError, MoleculeTooLargeError
from app.llm.errors import LLMNotConfiguredError, LLMRateLimitError, LLMTimeoutError
from app.rag.errors import EmbeddingUnavailableError, RAGError


class TestRateLimiter:
    """令牌桶行为。"""

    def test_new_client_gets_full_bucket(self) -> None:
        """新客户端应拿到满桶，允许一次突发。"""
        limiter = RateLimiter(rate=1.0, capacity=5)
        # 桶容量 5，故同一时刻前 5 次全部放行
        for i in range(5):
            allowed, wait = limiter.allow("a", now=0.0)
            assert allowed is True, f"第 {i + 1} 次应放行"
            assert wait == 0.0
        # 第 6 次：桶已空，同一时刻无补充 → 拒绝
        allowed, wait = limiter.allow("a", now=0.0)
        assert allowed is False
        assert wait == pytest.approx(1.0, abs=0.05)

    def test_tokens_refill_over_time(self) -> None:
        """时间流逝应补充令牌。"""
        limiter = RateLimiter(rate=2.0, capacity=2)
        assert limiter.allow("a", now=0.0)[0] is True
        assert limiter.allow("a", now=0.0)[0] is True
        assert limiter.allow("a", now=0.0)[0] is False
        # 注入 0.5 秒：rate=2/s × 0.5s = 1 个令牌 → 恰好恢复 1 次
        assert limiter.allow("a", now=0.5)[0] is True
        assert limiter.allow("a", now=0.5)[0] is False
        # 再过 0.5 秒又补 1 个，表现为稳定 rate=2/s
        assert limiter.allow("a", now=1.0)[0] is True

    def test_different_clients_are_independent(self) -> None:
        """不同客户端互不影响——这是限流的基本语义。"""
        limiter = RateLimiter(rate=1.0, capacity=1)
        assert limiter.allow("client-a", now=0.0)[0] is True
        assert limiter.allow("client-b", now=0.0)[0] is True
        # 各自耗尽后互不影响
        assert limiter.allow("client-a", now=0.0)[0] is False
        assert limiter.allow("client-b", now=0.0)[0] is False

    def test_never_exceeds_capacity(self) -> None:
        """长时间空闲后桶不应超过容量值（否则一次突发可放行数十次）。"""
        limiter = RateLimiter(rate=10.0, capacity=3)
        # 注入 1000 秒：理论补充 10000 个令牌，但被 capacity 钳在 3，
        # 故同时刻仍只放行 3 次——这就是"突发上限"的意义。
        passed = 0
        for _ in range(10):
            if limiter.allow("a", now=1000.0)[0]:
                passed += 1
            else:
                break
        assert passed == 3, f"突发上限应恰好为 capacity=3，实际 {passed}"

    def test_backwards_time_does_not_deduct_tokens(self) -> None:
        """时间倒流不应扣令牌。

        现实中``time.monotonic()`` 不会倒流，但测试注入了乱序时间；
        更重要的是这段逻辑若写错，会在真实环境的时钟校正下
        造成「令牌凭空消失」的诡异限流故障。
        """
        limiter = RateLimiter(rate=1.0, capacity=5)
        assert limiter.allow("a", now=100.0)[0] is True
        # now 比上次更早：elapsed<0，必须被钳到 0 而非扣除
        for _ in range(4):
            assert limiter.allow("a", now=50.0)[0] is True

    def test_retry_after_is_rounded_to_tenth(self) -> None:
        """重试等待应量化到 0.1 秒，便于 Retry-After 头使用。

        用注入时间而非真实时钟：否则测试结果取决于机器负载，
        偶发失败会被误判为代码问题（这类flaky 测试最难查）。
        """
        limiter = RateLimiter(rate=3.0, capacity=1)
        assert limiter.allow("a", now=0.0)[0] is True
        allowed, wait = limiter.allow("a", now=0.0)
        assert allowed is False
        assert wait == pytest.approx(0.3, abs=0.05)

    def test_evicts_stale_buckets_to_bound_memory(self) -> None:
        """客户端数量超上限时应清理，防止随机 IP 撑爆内存。"""
        limiter = RateLimiter(rate=1.0, capacity=2, max_clients=4)
        # 逐个客户端把桶用空，制造"都不满"的高压场景
        for i in range(20):
            limiter.allow(f"c{i}", now=0.0)
            limiter.allow(f"c{i}", now=0.0)
        assert len(limiter._buckets) <= 4  # noqa: SLF001 - 验证内存上界

    def test_rejects_invalid_config(self) -> None:
        """非法配置应在构造期报错，而不是运行中静默失效。"""
        for kwargs in (
            {"rate": 0, "capacity": 1},
            {"rate": 1, "capacity": 0},
            {"rate": 1, "capacity": 1, "max_clients": 0},
            {"rate": -1, "capacity": 1},
        ):
            with pytest.raises(ValueError):
                RateLimiter(**kwargs)  # type: ignore[arg-type]

    def test_reset_clears_state(self) -> None:
        limiter = RateLimiter(rate=1.0, capacity=1)
        assert limiter.allow("a", now=0.0)[0] is True
        assert limiter.allow("a", now=0.0)[0] is False
        limiter.reset()
        assert limiter.allow("a", now=0.0)[0] is True


class TestErrorClassification:
    """下层错误码到 API 契约的映射。"""

    @pytest.mark.parametrize(
        ("exc", "expected_retryable", "expected_status"),
        [
            # 上游瞬时故障：可重试，且状态码须体现故障类型
            (LLMTimeoutError("x"), True, 504),
            (LLMRateLimitError("x"), True, 429),
            (EmbeddingUnavailableError("x"), True, 503),
            (ToolTimeoutError("x"), True, 504),
            # 缺配置：503（服务未就绪）而非 500。实测踩过——保守默认的
            # 500 会让编排系统当成进程内部错误反复重启，而真正需要
            # 重启的恰恰只有未配置这一种。
            (LLMNotConfiguredError("x"), False, 503),
            # 输入问题：400
            (InvalidStructureError("x"), False, 400),
            (MoleculeTooLargeError("x"), False, 400),
            # 步数上限：不可重试
            (AgentStepLimitError("x"), False, 500),
        ],
    )
    def test_retryability(self, exc, expected_retryable, expected_status) -> None:
        """可重试性与状态码须与deployment-operations.md §8 一致。

        状态码不是随意选的：503 / 504 / 429 让客户端与编排系统能区分
        「等一会儿就好」「上游超时可重试」「上游限流了我们」
        「你输入有问题」，从而采取不同动作。
        """
        spec = classify(exc)
        assert spec.retryable is expected_retryable
        assert spec.http_status == expected_status

    def test_unregistered_domain_code_falls_back_conservatively(self) -> None:
        """未登记的下层错误码走保守默认（500、不可重试）。

        宁可让客户端不重试，也不要在语义不明时误导它打爆上游。
        """
        spec = classify(RAGError("未登记的检索错误"))
        assert spec.http_status == 500
        assert spec.retryable is False
        # 错误码本身须原样保留，便于排障时定位到具体模块
        assert spec.code == "rag_internal_error"

    def test_domain_code_is_preserved(self) -> None:
        """下层错误码须原样透传，不得被压平成 tool_error。

        若压平，「检索不可用」与「模型不可用」在数据结构上就
        不可区分了——这正是 knowledge_tools.py 刻意透传的原因。
        """
        exc = EmbeddingUnavailableError("嵌入模型不可用")
        spec = classify(exc)
        assert spec.code == "rag_embedding_unavailable"

    def test_unknown_exception_is_internal_and_hides_details(self) -> None:
        """未知异常降级为内部错误，且不泄露细节。"""
        exc = RuntimeError("连接 /home/secret/path 失败，key=abc123")
        payload = build_error_payload(exc, request_id="rid-1")
        error = payload["error"]
        assert error["code"] == "api_internal_error"
        assert error["retryable"] is False
        assert error["request_id"] == "rid-1"
        # 关键：异常消息中的路径与疑似密钥不得出现在响应里
        blob = str(payload)
        assert "/home/secret" not in blob
        assert "abc123" not in blob
        assert "RuntimeError" not in blob

    def test_api_level_codes_have_specs(self) -> None:
        """API 层自身的错误码必须有明确契约，不能走兜底。"""
        for code in ("api_invalid_input", "api_rate_limited", "api_not_ready"):
            assert code in API_ERROR_SPECS
            assert API_ERROR_SPECS[code].http_status > 0

    def test_non_retryable_codes_excluded(self) -> None:
        """llm_not_configured 不该出现在可重试集合里。

        这是本模块最容易写错的地方——前缀匹配 ``llm_`` 会把它
        误判为可重试，导致客户端对着未配置的密钥无限重试。
        """
        assert "llm_not_configured" not in RETRYABLE_CODES
        assert "llm_timeout" in RETRYABLE_CODES

    def test_public_message_hides_domain_message(self) -> None:
        """下层异常的自带文案**不得**原样透出。

        实测依据：``LLMUpstreamError`` 的 ``user_message`` 可能就是
        上游返回的原文（构造 "上游返回: key=sk-xxx" 时该串会出现在响应中）。
        故 API 层作为最后闸门，一律给通用文案。
        """
        exc = InvalidStructureError("化学结构式无法解析，请检查是否写错括号。")
        spec = classify(exc)
        msg = public_message(exc, spec)
        assert msg == "服务暂时不可用，请稍后重试。"
        assert "括号" not in msg

    def test_public_message_never_empty(self) -> None:
        """任何情况下文案都不得为空串——前端会显示空白。"""
        for exc in (
            InvalidStructureError(""),
            RuntimeError(""),
            Exception(),
        ):
            spec = classify(exc)
            assert public_message(exc, spec).strip()

    def test_api_level_messages_are_specific(self) -> None:
        """API 层自身的错误码须给出针对性文案，而非通用兜底。"""
        from app.api.errors import API_ERROR_SPECS, classify as cls

        exc = type("Sig", (Exception,), {"code": "api_rate_limited"})()
        assert public_message(exc, cls(exc)) == API_MESSAGES["api_rate_limited"]
        assert "频繁" in public_message(exc, cls(exc))

    def test_rate_limit_message_is_human_readable(self) -> None:
        """限流文案须是中文可读，不是技术术语。"""
        from app.api.app import _RateLimited

        spec = classify(_RateLimited())
        msg = public_message(_RateLimited(), spec)
        assert "频繁" in msg
        assert "rate" not in msg.lower()


__all__ = ["TestErrorClassification", "TestRateLimiter"]
