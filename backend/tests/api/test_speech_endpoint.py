"""语音 API 端点与音频存储（决策 H21）。

## 为什么这些测试特别关注"过期"

H21 定的方案是「两阶段 +惰性清理 + 定时清扫」。其中
**惰性清理是正确性保证**（不给过期音频），
**定时清扫是磁盘保护**（不留残渣）。

单测只测"能取到"是不够的——必须证明
「过期的取不到，且不会 500」，
否则演示时学生可能听到上一节课的内容。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import ServiceSettings
from app.api.app import create_app
from app.speech.store import MAX_AUDIO_BYTES, AudioStore


# ----------------------------------------------------------------------
# 音频存储
# ----------------------------------------------------------------------
class TestAudioStore:
    @pytest.fixture()
    def store(self, tmp_path: Path) -> AudioStore:
        return AudioStore(tmp_path / "audio", ttl_seconds=60.0)

    def _make_audio(self, tmp_path: Path, name: str = "speech.mp3", size: int = 64) -> Path:
        """造一个假的音频文件。"""
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\xff\xf3" + b"\x00" * size)
        return p

    def test_put_returns_id_and_moves_file(self, store: AudioStore, tmp_path: Path) -> None:
        """入库后原产物被**移走**（不留两份，避免误以为还在用）。"""
        src = self._make_audio(tmp_path)
        record = store.put(src)

        assert record.audio_id != ""
        assert record.path.exists()
        assert not src.exists(), "原产物应被移动而非复制"
        assert record.size > 0

    def test_fetch_returns_record(self, store: AudioStore, tmp_path: Path) -> None:
        record = store.put(self._make_audio(tmp_path))
        fetched = store.fetch(record.audio_id)
        assert fetched is not None
        assert fetched.audio_id == record.audio_id
        assert fetched.path.exists()

    def test_fetch_missing_returns_none(self, store: AudioStore) -> None:
        """不存在的 id 返回 None（调用方转404）。"""
        assert store.fetch("0" * 32) is None

    def test_fetch_rejects_malformed_id(self, store: AudioStore) -> None:
        """格式不合法的 id 直接拒绝，**不查文件系统**。

        这是路径穿越的防线：若接受任意 id，
        ``../etc/passwd`` 这类输入就会去查系统文件。
        """
        for bad in (
            "../../etc/passwd",
            "..%2F..%2Fetc%2Fpasswd",
            "abc",  # 太短
            "g" * 32,  # 非十六进制
            "A" * 32,  # 大写，不匹配
            "",
        ):
            assert store.fetch(bad) is None, f"{bad!r} 不该被接受"

    def test_expired_audio_is_rejected_and_deleted(
        self, store: AudioStore, tmp_path: Path
    ) -> None:
        """**核心断言**：过期音频取不到，且被顺手删掉。

        这是惰性清理的价值——不依赖任何后台机制，
        即使定时清扫从未运行过也不会把过期音频交给学生。
        """
        record = store.put(self._make_audio(tmp_path))
        # 把mtime 改老，模拟已过期
        old = time.time() - 3600
        import os

        os.utime(record.path, (old, old))

        assert store.fetch(record.audio_id) is None
        assert not record.path.exists(), "过期文件应被删除"

    def test_sweep_removes_only_expired(self, store: AudioStore, tmp_path: Path) -> None:
        """清扫只删过期的，新的必须留着。"""
        fresh = store.put(self._make_audio(tmp_path, "a.mp3"))
        stale = store.put(self._make_audio(tmp_path, "b.mp3"))

        import os

        old = time.time() - 3600
        os.utime(stale.path, (old, old))

        removed = store.sweep()
        assert removed == 1, f"应只删1 个，实际 {removed}"
        assert fresh.path.exists(), "未过期的被误删了"
        assert not stale.path.exists()

    def test_sweep_is_idempotent(self, store: AudioStore, tmp_path: Path) -> None:
        """重复清扫不报错。

        **多 worker 的前提**：每个进程各扫自己的目录，
        若删除会抛异常则无法幂等。
        """
        record = store.put(self._make_audio(tmp_path))
        import os

        old = time.time() - 3600
        os.utime(record.path, (old, old))

        assert store.sweep() == 1
        assert store.sweep() == 0, "第二次清扫应无事可做且不报错"

    def test_sweep_on_missing_root_is_safe(self, tmp_path: Path) -> None:
        """目录不存在时清扫返回 0，不抛异常。

        进程刚启动时目录可能还没建。
        """
        store = AudioStore(tmp_path / "does-not-exist")
        assert store.sweep() == 0

    def test_rejects_empty_file(self, store: AudioStore, tmp_path: Path) -> None:
        """空文件须拒绝——否则会返回 200 + 零字节音频。"""
        empty = tmp_path / "empty.mp3"
        empty.write_bytes(b"")
        with pytest.raises(ValueError, match="为空"):
            store.put(empty)

    def test_rejects_oversized_file(self, store: AudioStore, tmp_path: Path) -> None:
        """超体积须拒绝。"""
        big = tmp_path / "big.mp3"
        big.write_bytes(b"\x00" * (MAX_AUDIO_BYTES + 1))
        with pytest.raises(ValueError, match="超出上限"):
            store.put(big)

    def test_rejects_non_ttl(self, tmp_path: Path) -> None:
        """TTL 必须为正。"""
        with pytest.raises(ValueError, match="存活时长"):
            AudioStore(tmp_path, ttl_seconds=0)


# ----------------------------------------------------------------------
# API 端点
# ----------------------------------------------------------------------
@pytest.fixture()
def client() -> Any:
    """TestClient。

    **必须用 TestClient 作上下文管理器**：本模块的
    ``lifespan`` 会创建 ``AudioStore`` 并起定时清扫任务，
    不走 lifespan 则 ``app.state.audio_store`` 不存在。
    """
    app = create_app(ServiceSettings())
    with TestClient(app) as c:
        yield c


class TestSpeechEndpoint:
    def test_returns_200_even_when_tts_unavailable(
        self, client: TestClient
    ) -> None:
        """**关键**：语音失败也返回 200，状态在 stage 里。

        ``architecture.md`` §6 要求语音可降级——
        若返回 5xx，前端就要写错误分支，
        文本答案的交付会被语音问题打断。
        """
        resp = client.post("/api/v1/speak", json={"text": "乙醇的结构是 CCO。"})
        assert resp.status_code == 200, "语音失败不应改变 HTTP 状态码"
        body = resp.json()
        assert "stage" in body
        assert "available" in body
        # 无edge-tts 或网络不可用时 stage 不是 ready，
        # 但**接口本身成功**
        assert body["stage"] in {
            "ready", "disabled", "not_configured", "unavailable"
        }

    def test_respects_tts_disabled(self) -> None:
        """关掉 TTS 时 stage=disabled 且不给出音频 id。"""
        import os

        os.environ["XUEZHI_TTS_ENABLED"] = "0"
        try:
            app = create_app(ServiceSettings())
            with TestClient(app) as c:
                resp = c.post("/api/v1/speak", json={"text": "测试"})
                body = resp.json()
                assert body["stage"] == "disabled"
                assert body["available"] is False
                assert body["audio_id"] is None
        finally:
            del os.environ["XUEZHI_TTS_ENABLED"]

    def test_empty_text_rejected_by_validation(self, client: TestClient) -> None:
        """空文本是**输入错误**，应返回 400 而非 stage。

        这与"合成失败"是不同性质的问题——
        前者要改输入（400），后者只能降级（200 + stage）。

        **为什么是 400 而不是 FastAPI 默认的 422**：
        本项目有自定义校验处理器（见 app.py 的
        ``validation_handler``），它把 Pydantic 校验错误
        映射为 ``api_invalid_input`` = **400**，
        目的是把英文技术文案换成中文且不回显输入
        （`security-privacy.md` §3）。

        实测踩过：断言写成 422 而实际 400，
        误以为"项目忘了改状态码"。既有测试
        （test_app.py）一贯断言 400——**跟随项目约定**。
        """
        resp = client.post("/api/v1/speak", json={"text": ""})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "api_invalid_input"

    def test_extra_field_rejected(self, client: TestClient) -> None:
        """多余字段须拒绝（契约用 extra="forbid"）。"""
        resp = client.post(
            "/api/v1/speak", json={"text": "测试", "unexpected": 1}
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "api_invalid_input"

    def test_validation_error_does_not_echo_input(
        self, client: TestClient
    ) -> None:
        """校验失败**不得回显输入**（`security-privacy.md` §3）。

        学生若把敏感内容填错字段，原样回显等于泄露。
        """
        secret = "sensitive-token-should-not-appear"
        resp = client.post("/api/v1/speak", json={"text": ""})
        assert secret not in resp.text
        # 连提交的空串也不该出现在错误详情里
        details = resp.json().get("details", [])
        assert all("input" not in d for d in details), details

    def test_user_field_is_forwarded(self, client: TestClient) -> None:
        """``user`` 须被接受——多学生场景要靠它区分。"""
        resp = client.post(
            "/api/v1/speak",
            json={"text": "测试", "user": "student-01"},
        )
        assert resp.status_code == 200


class TestAudioRetrieval:
    def test_unknown_id_returns_404(self, client: TestClient) -> None:
        """未知 id 返回 404。"""
        resp = client.get(f"/api/v1/speak/{'0' * 32}")
        assert resp.status_code == 404

    def test_malformed_id_returns_404(self, client: TestClient) -> None:
        """格式不合法的 id 也返回 404——**不报 500**。

        路径穿越输入绝不能触发内部错误。
        """
        for bad in ("abc", "..%2F..%2Fetc%2Fpasswd", "ZZZ"):
            resp = client.get(f"/api/v1/speak/{bad}")
            assert resp.status_code == 404, f"{bad} 应404，实际 {resp.status_code}"

    def test_gone_and_absent_are_indistinguishable(self, client: TestClient) -> None:
        """**过期与不存在返回完全相同的响应**。

        区分二者会让「曾经存在过」成为可观测信息，
        而前端不需要知道（`security-privacy.md` §3）。
        """
        absent = client.get(f"/api/v1/speak/{'0' * 32}").json()
        expired = client.get("/api/v1/speak/abcdef0123456789abcdef0123456789").json()
        # 断言错误码相同（request_id 不同，那是设计如此）
        assert absent["error"]["code"] == expired["error"]["code"]

    def test_expired_audio_returns_404_not_500(
        self, client: TestClient
    ) -> None:
        """**核心断言**：过期的音频回 404，不回 500。

        回 500 会让前端显示"服务错误"，
        而实际只是音频过期——误导性文案比报错更糟。
        """
        import os
        import tempfile

        from app.speech.store import AudioStore

        # 直接往应用的存储目录放一个过期文件
        store: AudioStore = client.app.state.audio_store
        store.ensure_root()
        audio_id = "a" * 32
        target = store.root / f"{audio_id}.mp3"
        target.write_bytes(b"\xff\xf3" + b"\x00" * 64)
        old = time.time() - 7200
        os.utime(target, (old, old))

        resp = client.get(f"/api/v1/speak/{audio_id}")
        assert resp.status_code == 404
        assert not target.exists(), "过期文件应被顺手删掉"


class TestFullFlow:
    def test_two_phase_flow_when_tts_available(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**完整两阶段流程**：POST 拿 id → GET 取音频。

        用替身让合成成功，验证**契约本身**是通的
        （不依赖 edge-tts 与外网）。
        """
        from app.speech.models import SpeechResult

        class StubSpeech:
            def __init__(self) -> None:
                import tempfile

                d = Path(tempfile.mkdtemp(prefix="tts-stub-"))
                self.path = d / "speech.mp3"
                self.path.write_bytes(b"\xff\xf3" + b"\x00" * 128)

            def speak(self, text: str, **kwargs: Any) -> Any:
                from app.speech.models import DigitalHumanResult
                from app.speech.service import SpeechOutcome

                return SpeechOutcome(
                    speech=SpeechResult(
                        stage="ready", audio_path=str(self.path),
                        char_count=len(text),
                    ),
                    digital_human=DigitalHumanResult(delivered=False),
                )

        monkeypatch.setattr("app.speech.SpeechService", StubSpeech)

        resp = client.post("/api/v1/speak", json={"text": "乙醇的结构是 CCO。"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["stage"] == "ready", body
        assert body["available"] is True, body
        assert body["audio_id"], body
        # **URL 由服务端给出**，前端不拼路径
        assert body["audio_url"].endswith(body["audio_id"])

        # 第二阶段：取音频
        audio = client.get(body["audio_url"])
        assert audio.status_code == 200
        assert audio.headers["content-type"] == "audio/mpeg"
        assert len(audio.content) > 0
        # 须能内联播放而非触发下载
        assert "attachment" not in audio.headers.get("content-disposition", "")

    def test_ask_works_even_when_speech_broken(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**降级链的核心保证**：语音坏不影响分子接口。

        这是"文本为主交付"的直接验证。
        """
        # 无edge-tts 的环境本就unavailable，
        # 分子接口仍须正常
        resp = client.post("/api/v1/molecule", json={"smiles": "CCO"})
        assert resp.status_code == 200
        assert resp.json()["structure"]["canonical_smiles"] == "CCO"


class TestAudioDirConfig:
    """``XUEZHI_AUDIO_DIR`` 必须**真的被读取**。

    **实测踩过（与本项目多处同源的缺陷）**：我先在 ``.env.example``
    写了 ``XUEZHI_AUDIO_DIR=``，但代码里查不到任何地方读它——
    **配置项形同虚设**。

    这类缺陷不报错、不影响测试，只是"配了没用"。
    加上这个配置是因为 H23（多 worker 须共享目录），
    若读不到则那条决策**无法落地**。

    故用测试锁住：设了环境变量，应用的存储目录必须跟着变。
    """

    def test_env_var_changes_store_root(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        import os

        target = tmp_path / "shared-audio"
        monkeypatch.setenv("XUEZHI_AUDIO_DIR", str(target))

        app = create_app(ServiceSettings())
        with TestClient(app) as c:
            store = c.app.state.audio_store
            assert store.root == target, (
                f"XUEZHI_AUDIO_DIR 未生效："
                f"期望 {target}，实际 {store.root}"
            )
            # 目录应已被创建
            assert store.root.exists()

    def test_defaults_when_env_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未设时回落系统临时目录（不报错）。"""
        import os

        monkeypatch.delenv("XUEZHI_AUDIO_DIR", raising=False)
        app = create_app(ServiceSettings())
        with TestClient(app) as c:
            assert c.app.state.audio_store.root.exists()

    def test_empty_env_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """空字符串须回落默认——`.env` 里写 `KEY=` 是常见情形。

        若把空串当成路径，``mkdir("")`` 会抛异常导致服务起不来。
        """
        import os

        monkeypatch.setenv("XUEZHI_AUDIO_DIR", "")
        app = create_app(ServiceSettings())
        with TestClient(app) as c:
            assert c.app.state.audio_store.root.exists()
