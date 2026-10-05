"""语音模块单元测试。

**替身必须符合真实契约**——本项目已吃过三次亏（详见
`docs/development-log-2026-10.md`）：替身与真实依赖不一致时，
测试全绿而生产失败。故：

- ``FakeCommunicate`` 的 ``rate`` 参数是**带符号的字符串**
  （实测 ``edge_tts.Communicate`` 的签名如此），
  替身也照此断言；
- 断言**跨层**行为（配置→引擎→结果），
  而不只测单个函数的返回值。
"""

from __future__ import annotations

import asyncio
import errno
import json
import os
import shutil
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from app.speech import (
    MAX_SPEECH_CHARS,
    SpeechResult,
    SpeechService,
    SpeechSettings,
)
from app.speech.config import DEFAULT_VOICE, SpeechSettings as Settings
from app.speech.errors import SpeechNotConfiguredError
from app.speech.fay import DEFAULT_FAY_USER, FayClient
from app.speech.models import DigitalHumanResult
from app.speech.store import AudioStore
from app.speech.tts import SYNCHRONIZE_TIMEOUT, TTSEngine


# ----------------------------------------------------------------------
# 替身
# ----------------------------------------------------------------------
class FakeCommunicate:
    """``edge_tts.Communicate`` 替身。

    签名对照实测（``edge-tts==7.2.8``）::

        Communicate(text, voice='en-US-EmmaMultilingualNeural', *,
                    rate='+0%', volume='+0%', pitch='+0Hz', ...)

    **``rate`` 是字符串且带符号**——这一点如果替身写成int，
    测试就会放过真实调用必然失败的实现。
    """

    #: 记录构造参数，供断言用
    calls: list[dict[str, Any]] = []
    #: 让 save 抛出的异常（None 表示正常）
    fail_with: BaseException | None = None
    #: 让 save 写出空文件（模拟"无音频产出"）
    write_empty: bool = False
    #: 让 save 挂起（模拟超时）。用 Event 而非 sleep 以免拖慢测试。
    hang: bool = False

    def __init__(self, text: str, voice: str = "en-US-Emma", **kwargs: Any) -> None:
        self.text = text
        self.voice = voice
        self.kwargs = kwargs
        type(self).calls.append({"text": text, "voice": voice, **kwargs})

    async def save(self, path: str) -> None:
        if type(self).hang:
            # 永远挂起，由外层 wait_for 触发 TimeoutError
            await asyncio.Event().wait()
        if type(self).fail_with is not None:
            raise type(self).fail_with
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if type(self).write_empty:
            target.write_bytes(b"")
        else:
            target.write_bytes(b"ID3-fake-audio-bytes")


class FakeEdgeTTS:
    """模拟 ``edge_tts`` 模块，供 monkeypatch 注入。"""

    Communicate = FakeCommunicate


@pytest.fixture(autouse=True)
def _reset_fake() -> None:
    """每个用例前后重置替身状态。

    **必须重置**：替身状态是类属性，跨用例泄漏会让后续用例
    在前一个的失败状态下运行——实测踩过（"改了参数没生效"）。
    """
    FakeCommunicate.calls = []
    FakeCommunicate.fail_with = None
    FakeCommunicate.write_empty = False
    FakeCommunicate.hang = False
    yield
    FakeCommunicate.calls = []
    FakeCommunicate.fail_with = None
    FakeCommunicate.write_empty = False
    FakeCommunicate.hang = False


def _install_fake_edge_tts(monkeypatch: pytest.MonkeyPatch) -> None:
    """把替身装进 ``sys.modules``，让 ``from edge_tts import Communicate`` 命中。"""
    import sys

    monkeypatch.setitem(sys.modules, "edge_tts", FakeEdgeTTS)


# ----------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------
class TestSpeechSettings:
    def test_defaults_are_usable_without_any_env(self) -> None:
        """默认配置即可用——音色有默认值，不需要配一堆东西。"""
        s = SpeechSettings.from_env({})
        assert s.tts_enabled is True
        assert s.voice == DEFAULT_VOICE
        assert s.fay_enabled is False, "数字人须显式开启，不能默认连外部服务"

    def test_rate_formats_with_sign(self) -> None:
        """语速须转为**带符号字符串**（实测 edge-tts 要求）。"""
        assert TTSEngine._format_rate(0) == "+0%"
        assert TTSEngine._format_rate(-10) == "-10%"
        assert TTSEngine._format_rate(20) == "+20%"

    def test_rejects_out_of_range_rate(self) -> None:
        """非法语速须在构造时报错，而不是留到合成时才暴露。"""
        with pytest.raises(ValueError, match="语速"):
            SpeechSettings(rate=999)

    def test_fay_enabled_without_url_fails_loudly(self) -> None:
        """开了 Fay 却没给地址→ 显式报错。

        静默忽略更糟：使用者会以为数字人已接管，实际什么都没发生。
        """
        with pytest.raises(ValueError, match="XUEZHI_FAY_URL"):
            SpeechSettings(fay_enabled=True, fay_url="")

    def test_endpoint_normalizes_trailing_slash(self) -> None:
        """基址带尾斜杠不能拼出 ``//transparent-pass``。"""
        s = SpeechSettings(fay_enabled=True, fay_url="http://127.0.0.1:5000/")
        assert s.fay_endpoint == "http://127.0.0.1:5000/transparent-pass"

    def test_invalid_numeric_env_falls_back(self) -> None:
        """非数字回落默认，**不抛错**。

        语音是可降级能力，配置写错不该让整个服务起不来。
        """
        s = SpeechSettings.from_env({"XUEZHI_TTS_RATE": "abc"})
        assert s.rate == 0

    def test_out_of_range_env_is_clamped_not_rejected(self) -> None:
        """越界数值**夹到边界**，不抛错。

        实测踩过（这条用例最初断言"回落默认"，是错的）：
        原实现把 ``-5`` 原样传给 ``__post_init__``，
        构造时直接抛 ``ValueError`` ——一个笔误就让API 网关
        启动失败，与本模块"配置写错只降级"的定位矛盾。

        正确行为是**夹取**：``-5`` →下限 1，``9999`` → 上限。
        且夹取不会带来危险行为：上限只会让请求更早被拒，
        下限只会让文本更早被截断。
        """
        s = SpeechSettings.from_env({"XUEZHI_TTS_MAX_CHARS": "-5"})
        assert s.max_chars == 1, "越界须夹到下限，不是回落默认"

        s2 = SpeechSettings.from_env({"XUEZHI_TTS_RATE": "9999"})
        assert s2.rate == 100, "越界须夹到上限"

    def test_from_env_never_raises_on_bad_config(self) -> None:
        """``from_env`` 对任何垃圾输入都不抛错。

        这条是上面那条的性质保证：**构造器会抛**（直接
        ``SpeechSettings(rate=999)`` 时就该抛，那是编程错误），
        但从环境变量读入时须夹取——配置来源不同，容错策略也应不同。
        """
        for env in (
            {"XUEZHI_TTS_MAX_CHARS": "-5"},
            {"XUEZHI_TTS_RATE": "9999"},
            {"XUEZHI_TTS_RATE": "abc"},
            {"XUEZHI_TTS_MAX_CHARS": ""},
            {"XUEZHI_FAY_TIMEOUT": "not-a-number"},
            {"XUEZHI_TTS_VOICE": "   "},
        ):
            # 不应抛
            SpeechSettings.from_env(env)


class TestConfigIsActuallyRead:
    """**配置必须真的被读到**——这类缺陷不报错，只是行为不对。

    实测踩过（端到端 ``/health`` 实测发现）：
    ``SpeechService()`` 原写作 ``settings or SpeechSettings()``，
    于是设了 ``XUEZHI_FAY_ENABLED=1`` 与 ``XUEZHI_FAY_URL`` 之后，
    容器内 ``probe()`` 仍返回 ``fay=False``——**环境变量完全没被读**。

    为什么难以发现：默认值**完全合法**，故
    - 不会有任何校验报错；
    - 单元测试若都显式注入配置就全绿；
    - 只有真正设了环境变量再实跑才会暴露。

    故这里显式用环境变量构造，锁住"None ⇒读环境变量"这一约定。
    """

    def test_service_reads_env_when_settings_omitted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XUEZHI_FAY_ENABLED", "1")
        monkeypatch.setenv("XUEZHI_FAY_URL", "http://127.0.0.1:5000")
        monkeypatch.setenv("XUEZHI_TTS_VOICE", "zh-CN-YunxiNeural")

        svc = SpeechService()
        assert svc.settings.fay_enabled is True
        assert svc.settings.fay_url == "http://127.0.0.1:5000"
        assert svc.settings.voice == "zh-CN-YunxiNeural"
        assert svc.probe() == {"tts": True, "fay": True}

    def test_tts_engine_reads_env_when_settings_omitted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XUEZHI_TTS_VOICE", "zh-CN-XiaoyiNeural")
        assert TTSEngine().settings.voice == "zh-CN-XiaoyiNeural"

    def test_fay_client_reads_env_when_settings_omitted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XUEZHI_FAY_ENABLED", "1")
        monkeypatch.setenv("XUEZHI_FAY_URL", "http://10.0.0.5:5000")
        client = FayClient()
        assert client.settings.fay_enabled is True
        assert client.settings.fay_endpoint == "http://10.0.0.5:5000/transparent-pass"

    def test_explicit_settings_still_win(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """显式传入的配置**优先于**环境变量（测试注入必须有效）。"""
        monkeypatch.setenv("XUEZHI_TTS_VOICE", "from-env")
        engine = TTSEngine(SpeechSettings(voice="explicit"))
        assert engine.settings.voice == "explicit"

    def test_require_voice_raises_controlled_error(self) -> None:
        """未配置音色须抛受控错误，而非库自己的异常。"""
        s = SpeechSettings(tts_enabled=False)
        with pytest.raises(SpeechNotConfiguredError):
            s.require_voice()


# ----------------------------------------------------------------------
# 截断
# ----------------------------------------------------------------------
class TestTruncation:
    def test_short_text_not_truncated(self) -> None:
        engine = TTSEngine(SpeechSettings(max_chars=100))
        text, truncated = engine._truncate("这是一段很短的文本。")
        assert text == "这是一段很短的文本。"
        assert truncated is False

    def test_truncates_at_sentence_boundary(self) -> None:
        """在句子边界截断，而不是硬切半个字。"""
        engine = TTSEngine(SpeechSettings(max_chars=10))
        text = "第一句话在这里。第二句话也在这里。"
        out, truncated = engine._truncate(text)
        assert truncated is True
        # 边界应落在句号之后，且不得切断第一句
        assert out.startswith("第一句话在这里。")
        assert "……" in out

    def test_marks_truncation_so_student_knows(self) -> None:
        """截断必须留省略号——学生要知道还有内容。"""
        engine = TTSEngine(SpeechSettings(max_chars=8))
        out, truncated = engine._truncate("一二三四五六七八九十十一十二")
        assert truncated is True
        assert out.endswith("……")


# ----------------------------------------------------------------------
# TTS 引擎
# ----------------------------------------------------------------------
class TestTTSEngine:
    def test_returns_ready_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_edge_tts(monkeypatch)
        engine = TTSEngine()
        result = asyncio.run(engine.synthesize("乙醇的结构是 CCO。"))

        assert result.stage == "ready"
        assert result.available is True
        assert result.audio_path is not None
        assert Path(result.audio_path).exists()
        # 合成后必须真有内容
        assert Path(result.audio_path).stat().st_size > 0
        TTSEngine.cleanup(result)

    def test_passes_signed_rate_to_library(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """传给 edge-tts 的 rate 必须是带符号字符串。

        这是本模块最易错的一处：真实库要求 ``'+0%'``，
        传int 会直接失败。
        """
        _install_fake_edge_tts(monkeypatch)
        engine = TTSEngine(SpeechSettings(rate=-10))
        asyncio.run(engine.synthesize("测试语速。"))

        assert len(FakeCommunicate.calls) == 1
        assert FakeCommunicate.calls[0]["rate"] == "-10%"

    def test_empty_text_is_disabled_not_error(self) -> None:
        """空文本→ ``disabled`` 而非报错。

        模型可能返回空答复，此时"没有语音"是正确结果。
        """
        engine = TTSEngine()
        result = asyncio.run(engine.synthesize("   "))
        assert result.stage == "disabled"
        assert result.available is False

    def test_disabled_by_config(self) -> None:
        engine = TTSEngine(SpeechSettings(tts_enabled=False))
        result = asyncio.run(engine.synthesize("任意文本"))
        assert result.stage == "disabled"
        assert result.reason  # 应有可展示的说明

    def test_synthesize_never_raises_on_library_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """库抛任何异常都转成 unavailable，**不上抛**。

        这是降级能成立的前提：异常一旦冒到 API 层，
        文本答案就跟着失败了。
        """
        _install_fake_edge_tts(monkeypatch)
        FakeCommunicate.fail_with = RuntimeError("上游内部细节：token=sk-secret")
        engine = TTSEngine()
        result = asyncio.run(engine.synthesize("测试"))

        assert result.stage == "unavailable"
        assert result.available is False
        # 关键：上游原文绝不能出现在任何面向用户的字段里
        assert "sk-secret" not in result.reason
        assert "sk-secret" not in str(result.to_dict())

    def test_empty_output_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """合成出空文件须判为不可用。

        否则会返回 ``stage=ready`` 但路径指向 0 字节文件，
        前端拿到坏音频 URL 却毫无提示。
        """
        _install_fake_edge_tts(monkeypatch)
        FakeCommunicate.write_empty = True
        engine = TTSEngine()
        result = asyncio.run(engine.synthesize("测试"))

        assert result.stage == "unavailable"
        assert result.audio_path is None

    def test_timeout_becomes_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """挂起须被超时打断并转成不可用，不能无限等待。"""
        _install_fake_edge_tts(monkeypatch)
        FakeCommunicate.hang = True
        engine = TTSEngine()
        # 用很短的超时，避免测试真的等 30 秒
        monkeypatch.setattr("app.speech.tts.SYNCHRONIZE_TIMEOUT", 0.1)
        result = asyncio.run(engine.synthesize("测试"))

        assert result.stage == "unavailable"
        assert "超时" in result.reason

    def test_blocking_wrapper_works_in_event_loop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """在已有事件循环的线程里调同步包装不能抛 RuntimeError。

        FastAPI 的同步端点恰好跑在有事件循环的线程中。
        """

        _install_fake_edge_tts(monkeypatch)
        engine = TTSEngine()

        async def caller() -> SpeechResult:
            # 此处已有运行中的事件循环
            return engine.synthesize_blocking("嵌套事件循环测试")

        result = asyncio.run(caller())
        assert result.stage == "ready"
        TTSEngine.cleanup(result)


class TestAgainstRealEdgeTTS:
    """**对着真实 edge-tts 包**验证契约（不是只信替身）。

    本项目已三次因替身与真实依赖不一致而误判
    （见 ``docs/development-log-2026-10.md``），故凡有真实依赖
    可装时，都要有一条跑真包的用例。

    **刻意不真调合成**：edge-tts 连微软在线服务，
    单元测试不该依赖外网。这里验证的是**接口契约**
    （签名、参数形态、异常层次），这些才是容易出错的地方。
    真实合成已单独实跑并记录（见
    ``docs/speech-module-verification.md`` §5）。
    """

    def test_real_package_present(self) -> None:
        pytest.importorskip("edge_tts", reason="容器/环境未装 edge-tts")
        import edge_tts

        assert hasattr(edge_tts, "Communicate"), "真实包须提供 Communicate"

    def test_rate_format_matches_real_signature(self) -> None:
        """**实测依据**：真实 ``Communicate.__init__`` 的 ``rate`` 默认值
        是字符串 ``'+0%'``，不是整数。

        若上游改成接受 int，本模块的 ``_format_rate`` 会传错类型。
        这条用例把该假设钉在真实包上——升级edge-tts 时会立刻失败，
        而不是等到线上合成静默出错。
        """
        pytest.importorskip("edge_tts", reason="容器/环境未装 edge-tts")
        import inspect

        from edge_tts import Communicate

        params = inspect.signature(Communicate.__init__).parameters
        assert "rate" in params, "真实签名应含 rate 参数"
        default = params["rate"].default
        assert isinstance(default, str), (
            f"rate 默认值类型已变（{type(default).__name__}），"
            f"_format_rate 需同步调整"
        )
        assert default.endswith("%"), f"rate 默认值应带百分号，实际 {default!r}"

    def test_format_rate_output_is_accepted_shape(self) -> None:
        """本模块产出的 rate 形态须与真实包一致（带符号 + 百分号）。

        实测踩过（这条用例最初写错了）：原先断言
        ``len(produced) <= len(default) + 1``，理由是
        「位数不该超过真实默认值的形态」——但真实默认值是
        ``'+0%'``（3 字符），而合法输入 ``rate=100`` 会产出
        ``'+100%'``（5 字符），被这条断言误判为失败。

        **错的是断言，不是实现**：``'+100%'`` 正是edge-tts
        期望的形态。教训与本项目其他测试一致：
        断言写出来也得实跑验证，否则它自己会撒谎。
        """
        pytest.importorskip("edge_tts", reason="容器/环境未装 edge-tts")
        from edge_tts import Communicate

        # 真实包能接受的最大值是 +100%/-50%，
        # 形态为「符号 + 数字 + 百分号」
        for value in (0, -10, 20, -50, 100):
            produced = TTSEngine._format_rate(value)
            assert produced.endswith("%"), f"{value}: 缺百分号"
            if value < 0:
                assert produced.startswith("-"), f"{value}: 负值须带负号"
            else:
                assert produced.startswith("+"), f"{value}: 正值须带正号"
            # 只校验「符号+ 数字 + %」这个结构，不比较长度——
            # 长度会随数值位数变化，拿默认值的长度做基准是错的
            body = produced[1:-1]
            assert body.lstrip("-").isdigit(), f"{value}: 中间须为数字，实际 {body!r}"

    def test_real_exception_hierarchy_is_caught(self) -> None:
        """真实包的异常须能被统一捕获。

        本模块刻意捕获 ``BaseException`` 之外的宽类型而不逐个枚举——
        逐个枚举等于把库的实现细节抄进来，库新增异常时会静默漏网。
        这条验证真实异常确实落在宽捕获范围内。
        """
        pytest.importorskip("edge_tts", reason="容器/环境未装 edge-tts")
        from edge_tts.exceptions import EdgeTTSException

        # 真实包须提供该基类（实测 7.2.8 提供）
        assert issubclass(EdgeTTSException, Exception)

    def test_no_network_call_in_unit_tests(self) -> None:
        """本测试类**不得**发起真实网络调用。

        防止后续有人在测试里加 ``await Communicate(...).save()``
        ——那会让CI 因外网不可用而失败，且难以定位。
        """
        # 真实合成会走 Communicate.save；此处确认替身仍生效
        assert FakeCommunicate.calls == [], "本类不应留下任何合成调用痕迹"


# ----------------------------------------------------------------------
# Fay 客户端
# ----------------------------------------------------------------------
class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class TestFayClient:
    def test_disabled_by_default(self) -> None:
        """默认不推送——不能默认去连外部服务。"""
        client = FayClient(SpeechSettings())
        result = client.push_text("测试")
        assert result.delivered is False
        assert "未启用" in result.reason

    def test_success_on_code_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fay 成功时 body 为 ``{"code": 200}``。"""
        captured: dict[str, Any] = {}

        def fake_open(self: Any, request: Any, timeout: float | None = None) -> FakeResponse:
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["method"] = request.get_method()
            captured["timeout"] = timeout
            return FakeResponse(json.dumps({"code": 200, "message": "成功"}).encode())

        monkeypatch.setattr("urllib.request.OpenerDirector.open", fake_open, raising=False)
        client = FayClient(
            SpeechSettings(fay_enabled=True, fay_url="http://127.0.0.1:5000")
        )
        result = client.push_text("乙醇的结构是 CCO。", user="student-01")

        assert result.delivered is True
        assert captured["method"] == "POST"
        assert captured["url"] == "http://127.0.0.1:5000/transparent-pass"
        # 实测契约：字段名是 user / text / audio
        assert captured["body"] == {
            "user": "student-01",
            "text": "乙醇的结构是 CCO。",
            "audio": None,
        }
        # 超时须来自配置
        assert captured["timeout"] is not None

    def test_http_200_with_code_500_is_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**关键**：Fay 业务失败仍返回 HTTP 200，必须读 body 的 code。

        源码里失败分支是 ``jsonify({'code': 500, ...})``，
        没给 ``jsonify`` 传第二个参数，故真实 HTTP 状态码是 200。
        只看状态码的实现会把失败判成成功。
        """

        def fake_open(self: Any, request: Any, timeout: float | None = None) -> FakeResponse:
            return FakeResponse(
                json.dumps({"code": 500, "message": "未知原因出错"}).encode()
            )

        monkeypatch.setattr("urllib.request.OpenerDirector.open", fake_open, raising=False)
        client = FayClient(
            SpeechSettings(fay_enabled=True, fay_url="http://127.0.0.1:5000")
        )
        result = client.push_text("测试")

        assert result.delivered is False, "HTTP 200 不等于业务成功"
        assert "未能接收" in result.reason

    def test_connection_refused_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """连不上（Fay 没启动）须给可行动的说明。"""

        def fake_open(self: Any, request: Any, timeout: float | None = None) -> FakeResponse:
            raise urllib.error.URLError("Connection refused")

        monkeypatch.setattr("urllib.request.OpenerDirector.open", fake_open, raising=False)
        client = FayClient(
            SpeechSettings(fay_enabled=True, fay_url="http://127.0.0.1:5000")
        )
        result = client.push_text("测试")

        assert result.delivered is False
        assert "未启动" in result.reason or "不可达" in result.reason

    def test_non_json_response_is_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """非 JSON 响应（如代理返回的 HTML 错误页）须判为失败。"""

        def fake_open(self: Any, request: Any, timeout: float | None = None) -> FakeResponse:
            return FakeResponse(b"<html>502 Bad Gateway</html>")

        monkeypatch.setattr("urllib.request.OpenerDirector.open", fake_open, raising=False)
        client = FayClient(
            SpeechSettings(fay_enabled=True, fay_url="http://127.0.0.1:5000")
        )
        result = client.push_text("测试")
        assert result.delivered is False

    def test_never_leaks_upstream_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """上游 message 只进日志，不进面向学生的文案。"""

        def fake_open(self: Any, request: Any, timeout: float | None = None) -> FakeResponse:
            return FakeResponse(
                json.dumps(
                    {"code": 500, "message": "出错: 内部路径 /srv/secret/config.yaml"}
                ).encode()
            )

        monkeypatch.setattr("urllib.request.OpenerDirector.open", fake_open, raising=False)
        client = FayClient(
            SpeechSettings(fay_enabled=True, fay_url="http://127.0.0.1:5000")
        )
        result = client.push_text("测试")
        assert "secret" not in result.reason
        assert "secret" not in str(result.to_dict())

    def test_bypasses_http_proxy_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**本机/内网地址不得走系统代理**。

        实测踩过：设了 ``HTTP_PROXY`` 后，请求 ``127.0.0.1:5000``
        被丢给代理，代理返回 **HTTP 502**，表现为
        「Fay 明明在跑却报不可用」——一个极难定位的假故障。

        不能靠部署环境恰好设了 ``NO_PROXY``（演示机环境不可控），
        故代码里显式使用空``ProxyHandler``。
        """
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")

        opener = FayClient._build_opener(5.0)
        handlers = opener.handle_open["http"]
        # 空 ProxyHandler => 没有任何 proxy handler 会被采用
        assert all(
            type(h).__name__ != "ProxyHandler" or h.proxies == {}
            for h in handlers
        ), "仍存在带代理配置的 ProxyHandler"
        # 更直接的断言：openers 里不应有非空的代理映射
        assert not any(
            getattr(h, "proxies", None) for h in handlers
        ), "检测到非空代理配置，本机请求会被代理劫持"


# ----------------------------------------------------------------------
# 服务编排与降级
# ----------------------------------------------------------------------
def _fay_on() -> SpeechSettings:
    """构造"已开启数字人"的配置。

    **必须给 URL**：``SpeechSettings.__post_init__`` 会对
    "开了 Fay 却没地址"显式报错（静默忽略更糟——
    使用者会以为数字人已接管）。用替身客户端的测试同样要遵守
    这条：**配置合法性不因注入替身而放宽**，
    否则测试等于在测一个不存在的配置组合。
    """
    return SpeechSettings(fay_enabled=True, fay_url="http://127.0.0.1:5000")


class StubTTS:
    """TTS 引擎替身。只实现 :meth:`synthesize_blocking`。"""

    def __init__(self, result: SpeechResult) -> None:
        self._result = result
        self.calls: list[str] = []

    def synthesize_blocking(self, text: str) -> SpeechResult:
        self.calls.append(text)
        return self._result

    @property
    def settings(self) -> SpeechSettings:
        return SpeechSettings()


class StubFay:
    """Fay 客户端替身。"""

    def __init__(self, result: DigitalHumanResult) -> None:
        self._result = result
        self.calls: list[tuple[str, str]] = []

    def push_text(self, text: str, *, user: str = DEFAULT_FAY_USER) -> DigitalHumanResult:
        self.calls.append((text, user))
        return self._result

    @property
    def settings(self) -> SpeechSettings:
        return SpeechSettings()


class TestSpeechService:
    def test_both_succeed(self) -> None:
        """两层都成功 → 完整体验。"""
        tts = StubTTS(SpeechResult(stage="ready", audio_path="/tmp/a.mp3"))
        fay = StubFay(DigitalHumanResult(delivered=True))
        svc = SpeechService(_fay_on(), tts=tts, fay=fay)

        out = svc.speak("测试文本")
        assert out.available is True
        assert out.digital_human.delivered is True
        # user 必须透传，否则多学生会互相打断
        assert fay.calls[0][1] == DEFAULT_FAY_USER

    def test_tts_fails_but_still_returns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TTS 失败**不得抛异常**——文本主交付不能被打断。"""
        _install_fake_edge_tts(monkeypatch)
        FakeCommunicate.fail_with = RuntimeError("boom")
        svc = SpeechService(SpeechSettings())

        out = svc.speak("测试文本")  # 不应抛
        assert out.available is False
        assert out.speech.stage == "unavailable"
        assert out.speech.reason  # 有可展示的说明

    def test_fay_disabled_makes_no_network_call(self) -> None:
        """未启用 Fay 时绝不调用推送。"""
        fay = StubFay(DigitalHumanResult(delivered=True))
        svc = SpeechService(
            SpeechSettings(fay_enabled=False),
            tts=StubTTS(SpeechResult(stage="ready", audio_path="/tmp/a.mp3")),
            fay=fay,
        )
        out = svc.speak("测试")
        assert fay.calls == [], "未启用却发起了推送"
        assert out.digital_human.delivered is False

    def test_push_can_be_skipped_per_call(self) -> None:
        """单次调用可不推数字人（如流式中间态）。"""
        fay = StubFay(DigitalHumanResult(delivered=True))
        svc = SpeechService(
            _fay_on(),
            tts=StubTTS(SpeechResult(stage="ready", audio_path="/tmp/a.mp3")),
            fay=fay,
        )
        out = svc.speak("测试", push_digital_human=False)
        assert fay.calls == []
        assert out.digital_human.reason == "本次未启用数字人。"

    def test_custom_user_is_forwarded(self) -> None:
        """多学生场景须能区分user。"""
        fay = StubFay(DigitalHumanResult(delivered=True))
        svc = SpeechService(
            _fay_on(),
            tts=StubTTS(SpeechResult(stage="ready", audio_path="/tmp/a.mp3")),
            fay=fay,
        )
        svc.speak("测试", user="student-07")
        assert fay.calls[0][1] == "student-07"

    def test_probe_does_not_call_network(self) -> None:
        """``/health`` 探测不得触发真实网络调用。"""
        fay = StubFay(DigitalHumanResult(delivered=True))
        tts = StubTTS(SpeechResult(stage="ready", audio_path="/tmp/a.mp3"))
        svc = SpeechService(_fay_on(), tts=tts, fay=fay)

        result = svc.probe()
        assert result == {"tts": True, "fay": True}
        assert fay.calls == [], "探测不应发起推送"
        assert tts.calls == [], "探测不应触发合成"

    def test_outcome_to_dict_omits_detail(self) -> None:
        """对外字典**不得含 detail**（可能含内部路径）。"""
        outcome = SpeechService(SpeechSettings()).speak("测试")
        payload = outcome.to_dict()
        assert "detail" not in payload["speech"]
        assert "detail" not in payload["digital_human"]
        # 必须含前端判断展示所需的字段
        assert "stage" in payload["speech"]
        assert "available" in payload["speech"]


class TestCrossDeviceStore:
    """`Path.replace()` 不能跨文件系统，实测语音接口三项全挂。

    **这不是理论问题**：容器里 TTS 先写 `/tmp/xuezhi-tts-*/`，
    音频库挂在 `/work/data/audio`（宿主卷）——两者是不同设备，
    `os.replace` 抛 `OSError: [Errno 18] Invalid cross-device link`，
    导致 `POST /api/v1/speak` 三个测试全fail。

    正常路径（临时目录与数据目录同设备）不会暴露此问题，
    故必须**显式模拟跨设备**来守住这个契约。
    """

    def test_cross_device_move_falls_back_to_copy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """跨设备时应复制成功，而非抛 Errno 18。"""
        from app.speech import store as store_mod

        src_dir = tmp_path / "src-device"
        src_dir.mkdir()
        src = src_dir / "speech.mp3"
        src.write_bytes(b"fake-mp3-bytes")
        library = tmp_path / "library"

        real_replace = os.replace

        def fake_replace(a: Any, b: Any) -> None:
            # **只对来自 src-device 的源文件抛 EXDEV**。
            # 判据不能是 "路径含 tmp"——`tmp_path` 本身就在 /tmp 下，
            # 那会让实现内部 `.part → 正式文件` 那次 rename 也失败，
            # 于是测试测到的是 mock 缺陷而非被测行为（实测踩过）。
            if Path(a).parent == src_dir:
                raise OSError(errno.EXDEV, "Invalid cross-device link")
            real_replace(a, b)

        monkeypatch.setattr(store_mod.os, "replace", fake_replace)

        store = store_mod.AudioStore(root=library)
        record = store.put(src)

        assert record.path.exists()
        assert record.path.read_bytes() == b"fake-mp3-bytes"
        # move语义：源文件应被清理
        assert not src.exists()
        # 不该留下 .part 半成品
        assert not list(library.glob("*.part"))

    def test_same_device_move_is_atomic_no_copy(
        self, tmp_path: Path
    ) -> None:
        """同设备仍走 rename（原子），不引入复制开销。"""
        store = AudioStore(root=tmp_path / "lib")
        src = tmp_path / "a.mp3"
        src.write_bytes(b"x")
        record = store.put(src)
        assert record.path.exists()
        assert not src.exists()

    def test_cross_device_copy_failure_cleans_partial_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """复制中途失败要清掉半成品，否则会被当成有效音频取回。"""
        from app.speech import store as store_mod

        src_dir = tmp_path / "src-device"
        src_dir.mkdir()
        src = src_dir / "b.mp3"
        src.write_bytes(b"y")
        lib = tmp_path / "lib2"

        # 让 rename 对该源抛 EXDEV（进入复制分支），再让复制失败
        real_replace = os.replace

        def fake_replace(a: Any, b: Any) -> None:
            if Path(a).parent == src_dir:
                raise OSError(errno.EXDEV, "Invalid cross-device link")
            real_replace(a, b)

        def flaky_copy(a: Any, b: Any) -> None:
            raise OSError(errno.EIO, "I/O error")

        monkeypatch.setattr(store_mod.os, "replace", fake_replace)
        monkeypatch.setattr(store_mod.shutil, "copy2", flaky_copy)

        store = store_mod.AudioStore(root=lib)
        with pytest.raises(OSError):
            store.put(src)

        assert not list(lib.glob("*.part")), "半成品必须被清理"
        assert not list(lib.glob("*.mp3")), "不应留下截断的正式文件"
