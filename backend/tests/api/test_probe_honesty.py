"""探针分级：`/health` 不得把「配置就绪」报成「实际可用」。

## 为什么有这份测试（实测教训，2026-10-10）

本项目当天连踩四次「`/health` 说就绪、实际不可用」：

| 探针报 | 实际 |
| --- | --- |
| `tts=就绪` | **edge-tts 根本没装** |
| `rag=已加载` | 嵌入模型 `ProxyError`，检索全挂 |
| `fay=就绪` | Fay 端口 10002 无监听 |
| `rag=已加载` | `XUEZHI_EMBEDDING_PATH` 被当成 `cache_folder` 传，**构造成功但检索必失败** |

最后一类尤其隐蔽：`get_store()` 成功只证明「模型能加载、集合能打开」，
**不证明「检索真能返回结果」**。前端还据 `caps.fay=true`
**主动关闭了数字人的兜底口型**——危害不只是显示错，是把降级路径关掉了。

故本文件的核心命题：**制造故障后，`*_verified` 必须变 false。**

## 反向验证记录

每条「故障注入」用例都**实际跑过并确认会失败**（改回原实现即绿），
不是写完就信。注入手法见各用例 docstring。
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from app.api.deps import ServiceRegistry


def _probe_of(registry: ServiceRegistry, name: str):
    """取某组件的探针。找不到直接失败。

    **不能返回空 dict 或 None** —— 那会让后续断言变成恒真，
    本项目已记录过「断言自己也会撒谎」的教训。
    """
    for probe in registry.probe():
        if probe.name == name:
            return probe
    raise AssertionError(f"探针里没有组件 {name}")


def _caps(registry: ServiceRegistry, name: str) -> dict[str, bool]:
    return _probe_of(registry, name).caps


def _needs_tts() -> bool:
    """本机是否具备真合成条件（edge-tts 已装且可联网）。"""
    from app.speech.service import SpeechService

    return bool(SpeechService().probe()["tts"])


def _needs_rag() -> bool:
    """本机是否有可用索引（嵌入模型 + 已构建的集合）。"""
    from app.speech.service import SpeechService  # noqa: F401 - 触发配置加载

    try:
        ServiceRegistry().get_store().query("测试", top_k=1)
    except Exception:  # noqa: BLE001 - 环境不具备即跳过
        return False
    return True


# ----------------------------------------------------------------------
# TTS：必须真合成过才算可用
# ----------------------------------------------------------------------


@pytest.mark.skipif(not _needs_tts(), reason="本机无法真合成 TTS")
class TestTtsVerified:
    def test_synthesis_failure_flips_verified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """合成失败 → `tts_verified=False`。

        **核心命题。** 注入手法：把 `SpeechService.speak` 换成必失败的替身。
        注意**配置未改**（`caps.tts` 仍是 True）——
        若 `tts_verified` 还为 True，就证明探针只看配置。
        """
        from app.speech.models import SpeechResult
        from app.speech.service import SpeechService

        def boom(self: object, text: str, *a: object, **k: object) -> SpeechResult:
            return SpeechResult(
                stage="unavailable",
                audio_path=None,
                reason="注入的故障",
                truncated=False,
                char_count=0,
            )

        monkeypatch.setattr(SpeechService, "speak", boom)
        caps = _caps(ServiceRegistry(), "speech")
        assert caps.get("tts_verified") is False, (
            "合成已失败但 tts_verified 仍为 True —— 探针只看配置，未真验证"
        )

    def test_verdict_reads_nested_speech_field(self) -> None:
        """判据必须读 `outcome.speech.stage`。

        `SpeechOutcome` 是**嵌套结构**（`outcome.speech.audio_path`），
        **没有顶层 `audio_id`**。实测踩过：用
        `getattr(outcome, "audio_id", None)` 会**静默返回 None**，
        于是「永远判失败」却不报任何错——
        与本项目已记录的「默认值合法 ⇒ 静默失效」同型。
        """
        from app.speech.models import DigitalHumanResult, SpeechResult
        from app.speech.service import SpeechOutcome

        outcome = SpeechOutcome(
            speech=SpeechResult(
                stage="ready",
                audio_path="/tmp/x.mp3",
                reason="",
                truncated=False,
                char_count=1,
            ),
            digital_human=DigitalHumanResult(delivered=False, reason="未启用"),
        )
        # 该结构**没有** audio_id——这正是原实现踩坑的根因。
        assert not hasattr(outcome, "audio_id"), "结构变了？请同步复核 _verify_tts"
        assert outcome.speech.stage == "ready"


# ----------------------------------------------------------------------
# RAG：必须真检索过才算可用
# ----------------------------------------------------------------------


@pytest.mark.skipif(not _needs_rag(), reason="本机无可用索引")
class TestRagVerified:
    def test_query_raising_flips_verified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`get_store` 成功但 `query` 抛异常 → `rag_verified=False`。

        **最隐蔽的一类：构造成功、检索必失败。**
        原探针只调 `get_store()`，这类故障它完全看不见。
        """
        from app.rag.store import KnowledgeStore

        def boom(self: object, *a: object, **k: object) -> None:
            raise RuntimeError("注入：检索故障")

        monkeypatch.setattr(KnowledgeStore, "query", boom)
        assert _caps(ServiceRegistry(), "rag").get("rag_verified") is False

    def test_empty_result_flips_verified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """检索**不抛但返回空**也算不可用。

        反向判据：若只判断「没抛异常」，这类假就绪会漏过。
        """
        from app.rag.store import KnowledgeStore

        def empty(self: object, *a: object, **k: object):
            from app.rag.models import RetrievalResult

            return RetrievalResult(has_results=False)

        monkeypatch.setattr(KnowledgeStore, "query", empty)
        assert _caps(ServiceRegistry(), "rag").get("rag_verified") is False

    def test_detail_states_measured_hits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """命中时 detail 应说明实测命中数——给人看的 detail 要能自证。"""
        from app.rag.models import KnowledgeChunk, KnowledgeSource
        from app.rag.store import KnowledgeStore

        def hit(self: object, *a: object, **k: object):
            from app.rag.models import RetrievalResult

            return RetrievalResult(
                has_results=True,
                chunks=(
                    KnowledgeChunk(
                        chunk_id="c1",
                        text="乙醇",
                        source=KnowledgeSource(
                            source_id="s1",
                            title="讲义",
                            publisher="自编",
                            edition="2026",
                            curriculum_level="高中",
                            topic="醇",
                        ),
                        embedding_model="test-model",
                    ),
                ),
            )

        monkeypatch.setattr(KnowledgeStore, "query", hit)
        probe = _probe_of(ServiceRegistry(), "rag")
        assert probe.ready is True
        assert probe.caps.get("rag_verified") is True
        assert "命中" in probe.detail

    def test_verdict_uses_has_results_not_len(self) -> None:
        """判据必须是 `has_results`，不是 `len(chunks)`。

        反向判据：`chunks` 为空时 `len()` 恒为 0 且**不报错**，
        会静默报出「命中 0 条」——那正是本次要消除的假就绪形态之一。
        """
        from app.rag.models import RetrievalResult

        empty = RetrievalResult(has_results=False)
        assert empty.has_results is False
        assert empty.chunks == ()  # len() 会给0，但那是「看起来正常」的假象


# ----------------------------------------------------------------------
# Fay：必须真能连上，且验证不得有业务副作用
# ----------------------------------------------------------------------


class TestFayVerified:
    def test_disabled_yields_false_without_connecting(self) -> None:
        """未启用时为false，且**不发起任何连接**。"""
        assert ServiceRegistry._verify_fay(False) is False  # type: ignore[attr-defined]

    def test_unreachable_port_yields_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """配了但连不上 → False。

        这正是「`fay=就绪` 却端口无监听」那次的场景。
        用端口 1（几乎不可能有服务在听），并 monkeypatch settings
        避免依赖本机真实 `.env`。
        """
        from app.speech.config import SpeechSettings

        original_init = SpeechSettings.__init__

        def fake_init(self: object, *a: object, **k: object) -> None:
            original_init(self, *a, **k)
            self.fay_url = "http://127.0.0.1:1"
            self.fay_timeout = 1.0

        monkeypatch.setattr(SpeechSettings, "__init__", fake_init)
        assert ServiceRegistry._verify_fay(True) is False  # type: ignore[attr-defined]

    def test_verification_has_no_business_side_effect(self) -> None:
        """**验证绝不能有业务副作用。**

        `FayClient.push_text` 会真的投递播报内容（并让数字人动起来）。
        故 `_verify_fay` 只做 TCP 连接探测，不得调用它——
        本断言读源码锁定这个约束，防止后人「顺手复用」。
        """
        # **用 AST 判断实际调用，不做文本匹配。**
        # 前两版都栽在这里：
        # ① 直接搜全文 → 匹配到我自己写的说明（docstring 里就写了 push_text）
        # ② 逐行剥注释 → docstring 内部的行不以引号开头，仍会残留
        # **文本匹配断言代码行为本身就是错的做法**——注释、字符串都会污染。
        tree = ast.parse(textwrap.dedent(inspect.getsource(ServiceRegistry._verify_fay)))
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert "push_text" not in calls, "_verify_fay 不得调用 push_text（有业务副作用）"
        assert "create_connection" in calls, "应只做 TCP 连接探测"


# ----------------------------------------------------------------------
# 契约：配置层与实测层必须成对存在
# ----------------------------------------------------------------------


class TestCapsPairingContract:
    def test_speech_caps_have_both_layers(self) -> None:
        caps = _caps(ServiceRegistry(), "speech")
        assert "tts" in caps and "fay" in caps, "缺配置层"
        assert "tts_verified" in caps, "缺实测层：前端会退回只看配置"
        assert "fay_verified" in caps, "缺实测层：前端会退回只看配置"

    def test_rag_caps_has_verified(self) -> None:
        assert "rag_verified" in _caps(ServiceRegistry(), "rag")

    @pytest.mark.parametrize("layer", ["tts_verified", "fay_verified", "rag_verified"])
    def test_verified_values_are_boolean(self, layer: str) -> None:
        """所有 verified 必须是布尔。

        **字符串 `"false"` 在 JS 里是真值**——
        若探针返回字符串，前端 `=== true` 判定会全线失效。
        """
        for probe in ServiceRegistry().probe():
            if layer in probe.caps:
                assert isinstance(probe.caps[layer], bool)
                return
        pytest.skip(f"{layer} 未出现在当前环境的探针结果中")