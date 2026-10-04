"""限流器与错误映射的单元测试。

这两个模块是纯逻辑，不依赖 FastAPI 运行时，可独立快速测试。
"""

from __future__ import annotations

import pytest

from app.agent.errors import AgentStepLimitError, ToolTimeoutError
from app.api.errors import (
    API_ERROR_SPECS,
    API_MESSAGES,
    DOMAIN_CODE_SPECS,
    DOMAIN_MESSAGES,
    RETRYABLE_CODES,
    build_error_payload,
    classify,
    public_message,
)
from app.api.ratelimit import RateLimiter
from app.chem.errors import InvalidStructureError, MoleculeTooLargeError
from app.llm.errors import LLMNotConfiguredError, LLMRateLimitError, LLMTimeoutError
from app.rag.errors import EmbeddingUnavailableError, RAGError


def _collect_real_error_codes() -> set[str]:
    """扫描各下层模块，收集**真实定义**的错误码。

    从源码正则提取而非 import 取属性：多数错误码只出现在类属性
    上，遍历模块 ``__all__`` 拿不全；而错误码一致性是**编译期事实**，
    读源码更直接，也能在模块导入失败时给出明确报错。

    Returns:
        全部下层模块定义过的错误码集合。
    """
    import re
    from pathlib import Path

    real: set[str] = set()
    # 采集范围须覆盖**全部**有errors.py 的模块。
    # 实测踩过（2026-10-04，加语音端点时）：新增了 app/speech/errors.py，
    # 但本helper 仍只扫 llm/rag/chem/agent——
    # 于是 speech 的错误码完全不在检查范围内，
    # 双向一致性检查对它**失效**（既不报缺失也不报多余）。
    # "api" 指本模块自身：``_RateLimited``（api_rate_limited）
    # 与 ``_AudioGoneError``（speech_audio_gone）都定义在这里。
    # 实测踩过：漏了 api 时，双向检查把这两个码判为
    #「文案表含不存在的错误码」——它们明明就在本文件里。
    for mod in ("llm", "rag", "chem", "agent", "speech", "api"):
        path = Path(__file__).resolve().parents[2] / "app" / mod / "errors.py"
        text = path.read_text(encoding="utf-8")
        real.update(re.findall(r'code\s*=\s*"([a-z_]+)"', text))
    return real


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


class TestDomainMessageConsistency:
    """文案表与错误码表**必须一致**（防止凭空写出不存在的码）。

    实测踩过：写``DOMAIN_MESSAGES`` 时凭印象加了 `llm_auth_failed`——
    **该错误码根本不存在**（`app/llm/errors.py` 里没有此类）。
    这类错误不会让任何测试失败，只是多了一条永远用不上的文案。
    """

    def test_every_domain_message_key_is_a_real_error_code(self) -> None:
        """文案表里的每个码都必须是下层真实定义的。"""
        real = _collect_real_error_codes()

        orphans = set(DOMAIN_MESSAGES) - real
        assert not orphans, f"文案表含不存在的错误码：{sorted(orphans)}"

    def test_every_real_error_code_has_a_message(self) -> None:
        """**反向检查**：每个真实错误码都必须有针对性文案。

        实测踩过（2026-10-04，容器内回归时发现）：本测试原先只查
        「文案表里有没有不存在的码」（孤儿码），**不查缺失**——
        于是 `DOMAIN_MESSAGES` 整个缺了 chem 段（4 个码），
        几百个测试全绿，而学生输错结构式时看到的是
        「服务暂时不可用，请稍后重试」。

        **两个方向都得查**：孤儿码是写了没用的文案（无害但冗余），
        缺失码让学生拿到误导性提示（有害）。后者更严重，
        却恰好是原测试的盲区。
        """
        real = _collect_real_error_codes()
        missing = real - set(DOMAIN_MESSAGES)
        assert not missing, (
            f"错误码缺针对性文案，会落到通用兜底「服务暂时不可用」："
            f"{sorted(missing)}"
        )

    def test_chem_invalid_structure_message_is_actionable(self) -> None:
        """非法结构式的文案必须指向「改输入」，而非「重试」。

        **为什么单独测**：学生把 ``C1CC``（少一个右括号）发进来时，
        服务端一切正常。给他「服务暂时不可用，请稍后重试」是
        误导——他会反复重试，而正确动作是检查括号。
        这类「文案与真实原因不符」的缺陷不会被任何结构性断言抓到，
        只能针对具体场景钉住。
        """
        from app.chem.errors import InvalidStructureError

        exc = InvalidStructureError("内部细节不应出现")
        message = public_message(exc, classify(exc))
        assert message == DOMAIN_MESSAGES["chem_invalid_structure"]
        # 不能是通用兜底
        assert "服务暂时不可用" not in message
        # 应指向修正输入的动作
        assert "检查" in message or "修正" in message
        # 不得回显异常自身的消息（可能含内部细节）
        assert "内部细节" not in message

    def test_chem_codes_are_not_merged(self) -> None:
        """三个 chem 码的文案必须各不相同。

        `docs/product-scope.md` §5：「结构非法」与「超出支持范围」
        是不同性质的问题，学生该得到不同引导。
        合并成一句话等于把这个区分在最后一层抹掉。
        """
        codes = (
            "chem_invalid_structure",
            "chem_unsupported_structure",
            "chem_structure_too_large",
        )
        messages = [DOMAIN_MESSAGES[c] for c in codes]
        assert len(set(messages)) == len(codes), "三个 chem 文案不能相同"

    def test_retryable_domain_codes_have_messages(self) -> None:
        """可重试的下层码都该有针对性文案（否则又落回通用兜底）。"""
        for code in ("llm_timeout", "rag_retrieval_failed", "tool_timeout"):
            assert code in DOMAIN_MESSAGES, f"{code} 缺文案"

    def test_every_real_error_code_has_a_status_spec(self) -> None:
        """**反向检查**：每个真实错误码都必须登记HTTP 状态码。

        与 :meth:`test_every_real_error_code_has_a_message` 同源的问题，
        但影响面不同：漏登记状态码会让``_spec_for_domain_code``
        落到保守默认 **500**。

        **为什么 500 对输入错误是错的**：学生发来 ``C1CC``（少一个
        右括号），服务返回 500 会让前端以为「服务端崩了」，
        进而可能触发无意义的重试或错误上报。而这是纯粹的
        客户端输入问题，正确状态码是 4xx（实测 ``chem_invalid_structure``
        登记为 400）。
        """
        real = _collect_real_error_codes()
        missing = real - set(DOMAIN_CODE_SPECS)
        assert not missing, (
            f"错误码未登记状态码，会落到保守默认 500：{sorted(missing)}"
        )

    def test_input_error_codes_are_4xx(self) -> None:
        """输入类错误必须是 4xx，不能是 5xx。

        判据是**谁能修复**：客户端能改的就归 4xx。
        实测踩过——若这类码漏登记，会落默认 500，
        前端会当成服务端故障处理（重试/上报），方向完全错。
        """
        for code in (
            "chem_invalid_structure",
            "chem_unsupported_structure",
            "chem_structure_too_large",
            "tool_argument_invalid",
        ):
            spec = DOMAIN_CODE_SPECS[code]
            assert 400 <= spec.http_status < 500, (
                f"{code} 是输入类问题，应为 4xx，实为 {spec.http_status}"
            )
            assert not spec.retryable, f"{code} 重试无意义，不应标为可重试"

    def test_messages_do_not_leak_exception_content(self) -> None:
        """文案是**本模块撰写**的常量，不含任何动态内容。"""
        for code, message in DOMAIN_MESSAGES.items():
            assert "{" not in message, f"{code} 的文案不应含模板占位符"
            assert len(message) <= 40, f"{code} 文案过长，学生不会读"


class TestComponentUnavailable:
    """**组件不可用**与**代码缺陷**必须区分开（决策项 I6）。

    实测起因：本机 RDKit 的 C++ 扩展被应用控制策略拦截时，
    `/api/v1/molecule` 返回 500「服务内部错误」——
    既不真实（服务没崩）也无所行动（学生不知道该做什么）。
    """

    def test_dll_load_failure_is_component_unavailable(self) -> None:
        """DLL 加载失败 → 503 组件不可用（**实测的确切消息**）。"""
        exc = ImportError(
            "DLL load failed while importing rdchem: 应用程序控制策略已阻止此文件。"
        )
        spec = classify(exc)
        assert spec.code == "api_component_unavailable"
        assert spec.http_status == 503, "组件缺失不是服务内部错误"
        assert spec.retryable is False, "装不上重试无用，须人工介入"

    def test_module_not_found_is_component_unavailable(self) -> None:
        """模块没装 → 同样归为组件不可用。"""
        spec = classify(ModuleNotFoundError("No module named 'rdkit'"))
        assert spec.http_status == 503

    def test_linux_so_load_failure_is_component_unavailable(self) -> None:
        """Linux 的 `.so` 加载失败表述也要识别（部署环境不固定）。"""
        exc = ImportError("libX.so.1: cannot open shared object file")
        assert classify(exc).http_status == 503

    def test_typo_in_module_name_is_internal_error_not_component(self) -> None:
        """**反向用例**：代码写错符号名不算组件不可用。

        这是本组测试最重要的一条。若按 `isinstance(exc, ImportError)`
        一刀切，拼错的符号名会被报成「组件不可用」——
        **把代码缺陷伪装成环境问题，掩盖真bug**。

        实测踩过：判据里一度收录了 `cannot import name`，
        结果这条用例立刻失败。已从标记表中移除。
        """
        exc = ImportError("cannot import name 'get_engnie' from 'app.chem'")
        spec = classify(exc)
        assert spec.code == "api_internal_error", "代码缺陷不得伪装成组件不可用"
        assert spec.http_status == 500

    def test_oserror_cause_marks_component_unavailable(self) -> None:
        """`__cause__` 是 OSError 时（底层加载器错误）也算组件问题。"""
        try:
            try:
                raise OSError("拒绝访问")
            except OSError as inner:
                raise ImportError("load failed") from inner
        except ImportError as exc:
            spec = classify(exc)
        assert spec.http_status == 503

    def test_unrelated_error_is_not_component(self) -> None:
        """完全无关的异常不得被误判。"""
        assert classify(ValueError("数字格式不对")).http_status == 500

    def test_message_does_not_leak_internals(self) -> None:
        """响应**不得**回显 DLL 路径或策略细节。"""
        exc = ImportError(
            r"DLL load failed while importing rdchem: "
            r"C:\Users\Lenovo\.venv\Lib\site-packages\rdkit.pyd 被阻止"
        )
        message = public_message(exc, classify(exc))
        assert "venv" not in message
        assert ".pyd" not in message
        assert "rdchem" not in message
        # 应给出可行动的文案
        assert "不受影响" in message


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
        故 API 层作为最后闸门，不透传 ``exc.user_message``。

        **注意**：不给「下层自带文案」≠「不给针对性文案」。
        本层另有一张 :data:`DOMAIN_MESSAGES`（由本模块撰写）。

        **本用例验证的是"不透传"这一件事**，故用一个
        **本层自带、与异常文案字面不同**的文案来探测：
        ``ToolTimeoutError`` 的 ``user_message`` 说的是「工具响应超时」，
        若断言里的「工具响应超时」出现在结果里，就说明发生了透传。

        历史说明：本用例原先用 ``InvalidStructureError`` 作反例，
        依赖「``chem_invalid_structure`` 未登记在 ``DOMAIN_MESSAGES`` 中」
        这一前提。2026-10-04 补齐 chem 段文案后该前提失效
        （本层文案里也含「括号」二字），遂改用 ``tool_timeout``——
        断言的是**机制**（不透传），不该被文案表的内容变化牵连。
        """
        exc = ToolTimeoutError("工具响应超时")
        spec = classify(exc)
        msg = public_message(exc, spec)
        assert "工具响应超时" not in msg, "不得透传异常自带文案"
        assert "sk-" not in msg, "上游原文绝不能出现"

    def test_signal_exception_codes_resolve_in_both_tables(self) -> None:
        """**回归**：自带 code 的信号异常须在**两张表**里都能查到。

        实测踩过（2026-10-04，H21 音频端点）：
        ``classify`` 原本只查 ``API_ERROR_SPECS``（仅 ``api_`` 前缀），
        而 ``speech_audio_gone`` 登记在 ``DOMAIN_CODE_SPECS``。
        结果端到端拿到的是 ``api_internal_error`` + "服务内部错误"，
        **但 HTTP 状态码是对的 404** —— 两个字段自相矛盾：
        客户端看到 404 却收到"服务内部错误"。

        这类缺陷**结构上抓不到**：码确实登记了、文案也确实存在，
        只是查找路径没走到。故须按场景显式断言。
        """
        from app.api.app import _RateLimited
        from app.api.errors import _AudioGoneError

        # api_ 前缀的码走 API_ERROR_SPECS
        #（``_RateLimited`` 定义在 app.py 而非 errors.py——实测踩过）
        assert classify(_RateLimited()).code == "api_rate_limited"
        # 非 api_ 前缀的码须走 DOMAIN_CODE_SPECS（原先漏查）
        spec = classify(_AudioGoneError())
        assert spec.code == "speech_audio_gone", (
            f"音频失效被误分类为 {spec.code}，"
            f"客户端会收到与 404 矛盾的「服务内部错误」"
        )
        assert spec.http_status == 404

    def test_audio_gone_404_and_message_agree(self) -> None:
        """状态码与文案必须一致（针对上一条的语义级断言）。

        404 配"服务内部错误"是**自相矛盾**的响应：
        学生会以为该重试，而实际上重试同一个 id 仍会 404。
        """
        from app.api.errors import _AudioGoneError

        exc = _AudioGoneError()
        spec = classify(exc)
        message = public_message(exc, spec)
        assert spec.http_status == 404
        assert message != API_MESSAGES["api_internal_error"], (
            "404 响应的文案不能是「服务内部错误」"
        )
        assert "重新生成" in message, "应告诉客户端可行的下一步动作"

    def test_registered_domain_codes_get_specific_messages(self) -> None:
        """已登记的下层码应给出**针对性**文案，而非通用兜底。

        实测依据：收紧透传的第一版对所有下层码一律返回
        「服务暂时不可用」，结果缺密钥时学生看到的是这句话——
        既不知是配置问题，也无从行动（重启？申请密钥？）。

        **替身必须用真实的下层异常类**：`classify` 先走
        `isinstance(exc, _DOMAIN_ERRORS)` 判断，自造的 ``type(...)``
        不在那个元组里，会走不到下层分支——**这不是被测行为，
        是替身不合约**（首次写时就踩了：全部落回「服务内部错误」）。
        """
        cases = [
            (LLMNotConfiguredError("缺密钥"), "MAAS_API_KEY"),
            (LLMTimeoutError("超时"), "超时"),
            (EmbeddingUnavailableError("向量模型不可用"), "向量模型"),
        ]
        for exc, must_contain in cases:
            msg = public_message(exc, classify(exc))
            assert must_contain in msg, f"{exc.code} 的文案应提到「{must_contain}」"
            assert "服务内部错误" not in msg, f"{exc.code} 落回了内部错误文案"

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
        """API 层自身的错误码须给出针对性文案，而非通用兜底。

        用真实的限流信号异常（``app.py`` 里的 ``_RateLimited``），
        不自造 ``type(...)`` 替身——它能通过只是因为 classify 对
        API 层信号只看 ``code`` 属性，但**换成真实类才能保证
        契约没被改坏**。
        """
        from app.api.app import _RateLimited

        exc = _RateLimited()
        assert public_message(exc, classify(exc)) == API_MESSAGES["api_rate_limited"]
        assert "频繁" in public_message(exc, classify(exc))

    def test_rate_limit_message_is_human_readable(self) -> None:
        """限流文案须是中文可读，不是技术术语。"""
        from app.api.app import _RateLimited

        spec = classify(_RateLimited())
        msg = public_message(_RateLimited(), spec)
        assert "频繁" in msg
        assert "rate" not in msg.lower()


__all__ = ["TestErrorClassification", "TestRateLimiter"]
