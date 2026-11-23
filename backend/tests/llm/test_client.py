"""模型适配层测试。

运行方式（本机 venv 即可，无需 Docker / 云端密钥）：

    .venv-xuezhi312/Scripts/python.exe -m pytest tests/llm/test_client.py

**测试不发起真实网络请求**：
- 配置校验为纯本地逻辑，直接测试
- 上游交互用替身对象注入，验证错误映射与文本提取
默认底座自 2026-11-23 起为本地 qwen2.5-7B（OpenAI 兼容端点），
云端盘古仅作为显式切换的可选项，相关常量保留供回归验证。
"""

from __future__ import annotations

import os

import pytest

from app.llm import (
    LLMConfig,
    LLMConfigError,
    LLMError,
    LLMNotConfiguredError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from app.llm.client import BASE_SYSTEM_PROMPT, LLMClient, create_client
from app.llm.config import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    LOCAL_QWEN_BASE_URL,
    MAAS_BASE_URL,
    MAAS_MODELS,
)

# 测试用假密钥，仅用于构造，不发起请求
FAKE_KEY = "sk-test-fake-key-not-real"


# ----------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------
class TestConfig:
    def test_defaults(self):
        cfg = LLMConfig(model=DEFAULT_MODEL, base_url=MAAS_BASE_URL, api_key=FAKE_KEY)
        assert cfg.timeout > 0
        assert cfg.max_retries >= 0
        assert 0.0 <= cfg.temperature <= 2.0
        assert cfg.max_completion_tokens > 0

    def test_model_and_base_url_kept(self):
        cfg = LLMConfig(model="openpangu-2.0-pro", base_url=MAAS_BASE_URL, api_key=FAKE_KEY)
        assert cfg.model == "openpangu-2.0-pro"
        assert cfg.base_url == MAAS_BASE_URL

    def test_api_key_never_in_repr(self):
        """密钥绝不能出现在 repr 中（会被日志意外打印）。"""
        cfg = LLMConfig(model=DEFAULT_MODEL, base_url=MAAS_BASE_URL, api_key=FAKE_KEY)
        assert FAKE_KEY not in repr(cfg)
        assert "api_key" not in repr(cfg).lower() or "field(repr=False)" not in repr(cfg)

    def test_api_key_never_in_str(self):
        cfg = LLMConfig(model=DEFAULT_MODEL, base_url=MAAS_BASE_URL, api_key=FAKE_KEY)
        assert FAKE_KEY not in str(cfg)

    def test_public_summary_excludes_secret(self):
        """可记录摘要必须不含密钥。"""
        cfg = LLMConfig(model=DEFAULT_MODEL, base_url=MAAS_BASE_URL, api_key=FAKE_KEY)
        summary = cfg.public_summary()
        assert FAKE_KEY not in str(summary)
        assert summary["api_key_configured"] is True
        assert summary["model"] == DEFAULT_MODEL
        assert summary["provider_host"] == "api.modelarts-maas.com"

    def test_official_model_detection(self):
        official = LLMConfig(model="openpangu-2.0-flash", base_url=MAAS_BASE_URL, api_key=FAKE_KEY)
        custom = LLMConfig(model="my-self-hosted", base_url="http://localhost:8000", api_key=FAKE_KEY)
        assert official.is_official_maas_model is True
        assert custom.is_official_maas_model is False

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"model": ""},                      # 空模型
            {"model": "   "},                    # 空白模型
            {"base_url": ""},                    # 空地址
            {"base_url": "ftp://x"},             # 协议不对
            {"api_key": ""},                     # 空密钥
            {"timeout": 0},                      # 超时非正
            {"timeout": -1},
            {"max_retries": -1},                 # 重试为负
            {"temperature": 2.5},                # 温度越界
            {"temperature": -0.1},
            {"max_completion_tokens": 0},        # token 上限非正
        ],
    )
    def test_invalid_config_rejected(self, kwargs):
        base = {"model": DEFAULT_MODEL, "base_url": MAAS_BASE_URL, "api_key": FAKE_KEY}
        base.update(kwargs)
        with pytest.raises(LLMConfigError):
            LLMConfig(**base)

    def test_error_message_does_not_leak_key(self):
        """校验失败时消息里不能出现密钥值。"""
        with pytest.raises(LLMConfigError) as exc:
            LLMConfig(model=DEFAULT_MODEL, base_url="ftp://bad", api_key=FAKE_KEY)
        assert FAKE_KEY not in exc.value.user_message
        assert FAKE_KEY not in str(exc.value.detail or "")

    def test_known_models_from_official_doc(self):
        """模型标识应与官方文档一致（2026-09-29 版）。"""
        assert "openpangu-2.0-flash" in MAAS_MODELS
        assert "openpangu-2.0-pro" in MAAS_MODELS
        assert MAAS_BASE_URL == "https://api.modelarts-maas.com/openai/v1"


class TestConfigFromEnv:
    def test_reads_from_env(self, monkeypatch):
        monkeypatch.setenv("MAAS_API_KEY", FAKE_KEY)
        monkeypatch.setenv("MAAS_MODEL", "openpangu-2.0-pro")
        monkeypatch.setenv("MAAS_BASE_URL", "http://custom:8000")
        cfg = LLMConfig.from_env()
        assert cfg.model == "openpangu-2.0-pro"
        assert cfg.base_url == "http://custom:8000"
        assert cfg.api_key == FAKE_KEY

    def test_defaults_when_only_key_set(self, monkeypatch):
        monkeypatch.setenv("MAAS_API_KEY", FAKE_KEY)
        monkeypatch.delenv("MAAS_MODEL", raising=False)
        monkeypatch.delenv("MAAS_BASE_URL", raising=False)
        cfg = LLMConfig.from_env()
        assert cfg.model == DEFAULT_MODEL
        assert cfg.base_url == DEFAULT_BASE_URL
        assert cfg.base_url == LOCAL_QWEN_BASE_URL

    def test_local_endpoint_allows_empty_key(self, monkeypatch):
        """本地推理无需密钥：空 key 自动填占位值（协作者零配置）。"""
        monkeypatch.delenv("MAAS_API_KEY", raising=False)
        monkeypatch.delenv("MAAS_BASE_URL", raising=False)
        cfg = LLMConfig.from_env()
        assert cfg.api_key == "local"
        assert cfg.public_summary()["local_endpoint"] is True

    def test_custom_local_endpoint_allows_empty_key(self, monkeypatch):
        """其他本机端点（如 Ollama 11434）同样无需密钥。"""
        monkeypatch.delenv("MAAS_API_KEY", raising=False)
        monkeypatch.setenv("MAAS_BASE_URL", "http://127.0.0.1:11434/v1")
        cfg = LLMConfig.from_env()
        assert cfg.api_key == "local"

    def test_remote_endpoint_requires_key(self, monkeypatch):
        """远端端点缺密钥必须快速失败，不能误判为本地模式。"""
        monkeypatch.delenv("MAAS_API_KEY", raising=False)
        with pytest.raises(LLMConfigError) as exc:
            LLMConfig.from_env(base_url=MAAS_BASE_URL)
        assert "MAAS_API_KEY" in exc.value.user_message

    def test_default_is_local_qwen(self):
        """默认底座为本地 qwen，盘古不再是默认值。"""
        assert DEFAULT_MODEL == "qwen2.5-7b-local"
        assert DEFAULT_BASE_URL == "http://127.0.0.1:8080/v1"
        assert "openpangu" not in DEFAULT_MODEL

    def test_local_disables_streaming_by_default(self, monkeypatch):
        """本地 llama.cpp 0.3.x 流式 + tools 返回空流，默认关闭。"""
        monkeypatch.delenv("MAAS_API_KEY", raising=False)
        monkeypatch.delenv("MAAS_BASE_URL", raising=False)
        monkeypatch.delenv("XUEZHI_LLM_STREAMING", raising=False)
        cfg = LLMConfig.from_env()
        assert cfg.streaming is False
        assert cfg.public_summary()["streaming"] is False

    def test_remote_enables_streaming_by_default(self, monkeypatch):
        """云端 MaaS 支持 SSE，默认保持开启。"""
        monkeypatch.setenv("MAAS_API_KEY", FAKE_KEY)
        monkeypatch.setenv("MAAS_BASE_URL", MAAS_BASE_URL)
        monkeypatch.delenv("XUEZHI_LLM_STREAMING", raising=False)
        assert LLMConfig.from_env().streaming is True

    def test_streaming_env_override(self, monkeypatch):
        """显式环境变量可覆盖自动推导（默认+可切）。"""
        monkeypatch.delenv("MAAS_API_KEY", raising=False)
        monkeypatch.setenv("XUEZHI_LLM_STREAMING", "1")
        assert LLMConfig.from_env().streaming is True

    def test_overrides_take_precedence(self, monkeypatch):
        monkeypatch.setenv("MAAS_API_KEY", "env-key")
        cfg = LLMConfig.from_env(model="openpangu-2.0-pro")
        assert cfg.model == "openpangu-2.0-pro"
        assert cfg.api_key == "env-key"


# ----------------------------------------------------------------------
# 客户端
# ----------------------------------------------------------------------
class _FakeResponse:
    """模拟 LangChain 返回对象。"""

    def __init__(self, content):
        self.content = content


class _FakeChain:
    """替身链，捕获调用参数并返回预设内容或抛出预设异常。"""

    def __init__(self, *, content="回复内容", error: Exception | None = None):
        self._content = content
        self._error = error
        self.calls: list[list[tuple[str, str]]] = []

    def invoke(self, messages):
        self.calls.append(messages)
        if self._error is not None:
            raise self._error
        return _FakeResponse(self._content)


def _client_with_fake_chain(**kwargs) -> tuple[LLMClient, _FakeChain]:
    """构造注入假链的客户端，避免真实网络调用。"""
    cfg = LLMConfig(model=DEFAULT_MODEL, base_url=MAAS_BASE_URL, api_key=FAKE_KEY, **kwargs)
    client = LLMClient.__new__(LLMClient)   # 绕过 SDK 客户端构造
    client._config = cfg
    chain = _FakeChain(**kwargs.pop("_fake", {}))
    client._chain = chain
    return client, chain


class TestClient:
    def test_complete_returns_text(self):
        client, _ = _client_with_fake_chain()
        assert client.complete("什么是酯化反应？") == "回复内容"

    def test_system_prompt_included_by_default(self):
        client, chain = _client_with_fake_chain()
        client.complete("问题")
        roles = [m[0] for m in chain.calls[0]]
        assert roles[0] == "system"
        assert chain.calls[0][0][1] == BASE_SYSTEM_PROMPT

    def test_custom_system_prompt(self):
        client, chain = _client_with_fake_chain()
        client.complete("问题", system_prompt="你是测试助手")
        assert chain.calls[0][0][1] == "你是测试助手"

    def test_empty_system_prompt_omitted(self):
        client, chain = _client_with_fake_chain()
        client.complete("问题", system_prompt="")
        assert all(m[0] != "system" for m in chain.calls[0])

    def test_history_preserved_in_order(self):
        client, chain = _client_with_fake_chain()
        client.complete("第三个问题", history=[("user", "第一问"), ("assistant", "第一答")])
        roles = [m[0] for m in chain.calls[0]]
        assert roles == ["system", "user", "assistant", "user"]
        assert chain.calls[0][-1][1] == "第三个问题"

    def test_empty_input_rejected(self):
        client, _ = _client_with_fake_chain()
        with pytest.raises(LLMError) as exc:
            client.complete("   ")
        assert exc.value.code == "llm_invalid_input"

    def test_model_property(self):
        client, _ = _client_with_fake_chain()
        assert client.model == DEFAULT_MODEL

    def test_public_config_has_no_secret(self):
        client, _ = _client_with_fake_chain()
        assert FAKE_KEY not in str(client.public_config())


class TestErrorMapping:
    """验证上游异常被正确映射为受控错误码。"""

    def _client_raising(self, exc: Exception) -> LLMClient:
        client, _ = _client_with_fake_chain()
        client._chain = _FakeChain(error=exc)
        return client

    def test_timeout_mapped(self):
        client = self._client_raising(TimeoutError("Request timed out"))
        with pytest.raises(LLMTimeoutError) as e:
            client.complete("x")
        assert e.value.code == "llm_timeout"
        assert e.value.retryable is True

    def test_rate_limit_mapped(self):
        client = self._client_raising(type("RateLimitError", (Exception,), {})("rate limit exceeded"))
        with pytest.raises(LLMRateLimitError) as e:
            client.complete("x")
        assert e.value.code == "llm_rate_limited"

    def test_auth_failure_mapped(self):
        client = self._client_raising(
            type("AuthenticationError", (Exception,), {})("401 unauthorized")
        )
        with pytest.raises(LLMError) as e:
            client.complete("x")
        assert e.value.code == "llm_auth_failed"
        assert e.value.retryable is False

    def test_generic_error_mapped_to_upstream(self):
        client = self._client_raising(RuntimeError("boom"))
        with pytest.raises(LLMUpstreamError) as e:
            client.complete("x")
        assert e.value.code == "llm_upstream_unavailable"
        assert e.value.retryable is True

    def test_upstream_message_is_sanitized(self):
        """原始上游消息不得直接透给用户。"""
        client = self._client_raising(RuntimeError("secret-key-leak: invalid request body"))
        with pytest.raises(LLMUpstreamError) as e:
            client.complete("x")
        assert "secret-key-leak" not in e.value.user_message

    def test_detail_keeps_exception_type_only(self):
        """detail 只保留异常类型名，不含原始内容。"""
        client = self._client_raising(RuntimeError("敏感内容 ABC123"))
        with pytest.raises(LLMUpstreamError) as e:
            client.complete("x")
        assert e.value.detail == "RuntimeError"
        assert "ABC123" not in (e.value.detail or "")

    def test_timeout_is_subclass_of_upstream(self):
        assert issubclass(LLMTimeoutError, LLMUpstreamError)
        assert issubclass(LLMRateLimitError, LLMUpstreamError)


class TestResponseExtraction:
    def test_plain_string(self):
        assert LLMClient._extract_text(_FakeResponse("文本")) == "文本"

    def test_content_list_with_text_blocks(self):
        """兼容 OpenAI 多模态返回格式。"""
        blocks = [{"type": "text", "text": "第一段"}, {"type": "text", "text": "第二段"}]
        assert LLMClient._extract_text(_FakeResponse(blocks)) == "第一段第二段"

    def test_content_list_with_str(self):
        assert LLMClient._extract_text(_FakeResponse(["甲", "乙"])) == "甲乙"

    def test_dict_response(self):
        assert LLMClient._extract_text({"content": "来自字典"}) == "来自字典"

    def test_empty_content_raises(self):
        """空回复必须报错，不能让上层把空串当成功。"""
        with pytest.raises(LLMError) as e:
            LLMClient._extract_text(_FakeResponse(""))
        assert e.value.code == "llm_empty_response"

    def test_none_content_raises(self):
        with pytest.raises(LLMError):
            LLMClient._extract_text(_FakeResponse(None))


class TestPromptBaseline:
    def test_system_prompt_requires_citation_honesty(self):
        """基线提示词须要求"无来源时明确说明"，对应 product-scope §6。"""
        assert "无来源" in BASE_SYSTEM_PROMPT or "确有来源" in BASE_SYSTEM_PROMPT
        assert "编造" in BASE_SYSTEM_PROMPT

    def test_system_prompt_requires_scope_boundary(self):
        """须要求说明超纲边界，对应 product-scope §5。"""
        assert "边界" in BASE_SYSTEM_PROMPT

    def test_system_prompt_distinguishes_verification(self):
        """须区分检索事实、工具校验与推断，对应 architecture §5。"""
        assert "推断" in BASE_SYSTEM_PROMPT
        assert "工具" in BASE_SYSTEM_PROMPT


class TestFactory:
    def test_create_client_requires_config(self, monkeypatch):
        """远端端点无密钥时应抛配置错误；本地端点无需密钥。"""
        monkeypatch.delenv("MAAS_API_KEY", raising=False)
        monkeypatch.setenv("MAAS_BASE_URL", MAAS_BASE_URL)
        with pytest.raises(LLMConfigError):
            create_client()

    def test_create_client_with_explicit_config(self):
        cfg = LLMConfig(model=DEFAULT_MODEL, base_url=MAAS_BASE_URL, api_key=FAKE_KEY)
        client = create_client(cfg)
        assert client.model == DEFAULT_MODEL


# ----------------------------------------------------------------------
# 工具调用绑定参数（2026-11-23 本地 qwen 兼容性回归）
# ----------------------------------------------------------------------
class _FakeToolsChain(_FakeChain):
    """记录 bind_tools 参数的替身链。"""

    def __init__(self, *, streaming: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.streaming = streaming
        self.bind_calls: list[tuple[list[dict], dict]] = []

    def bind_tools(self, tools, **kwargs):
        self.bind_calls.append((list(tools), kwargs))
        return self

    def stream(self, messages):
        self.calls.append(messages)
        if self._error is not None:
            raise self._error
        yield _FakeResponse(self._content)


def _client_with_tools_chain(**kwargs) -> tuple[LLMClient, _FakeToolsChain]:
    cfg = LLMConfig(model=DEFAULT_MODEL, base_url=LOCAL_QWEN_BASE_URL, api_key="local")
    client = LLMClient.__new__(LLMClient)
    client._config = cfg
    chain = _FakeToolsChain(**kwargs)
    client._chain = chain
    return client, chain


_TOOL = {
    "type": "function",
    "function": {
        "name": "calc_molecular_weight",
        "description": "根据 SMILES 计算分子量",
        "parameters": {"type": "object", "properties": {"smiles": {"type": "string"}}},
    },
}


class TestToolBinding:
    def test_invoke_passes_explicit_auto(self):
        """必须显式传 tool_choice=auto：本地 llama.cpp 缺省时不解析工具。"""
        client, chain = _client_with_tools_chain()
        client.invoke_with_tools([{"role": "user", "content": "乙醇分子量？"}], [_TOOL])
        assert len(chain.bind_calls) == 1
        assert chain.bind_calls[0][1].get("tool_choice") == "auto"
        assert chain.bind_calls[0][0] == [_TOOL]

    def test_stream_passes_explicit_auto(self):
        """流式路径同样必须显式传 auto（两条路径共用同一约束）。"""
        client, chain = _client_with_tools_chain(streaming=True)
        chunks = list(client.stream_with_tools([{"role": "user", "content": "x"}], [_TOOL]))
        assert len(chunks) == 1
        assert chain.bind_calls[0][1].get("tool_choice") == "auto"

    def test_never_names_specific_function(self):
        """只传 'auto'，不点名具体函数（openPangu 不支持 object 形式）。"""
        client, chain = _client_with_tools_chain()
        client.invoke_with_tools([{"role": "user", "content": "x"}], [_TOOL])
        choice = chain.bind_calls[0][1]["tool_choice"]
        assert isinstance(choice, str) and choice == "auto"
