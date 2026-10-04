"""真实 HTTP 端到端测试（起 uvicorn，走 TCP）。

## 为什么需要这一层

`test_app.py` 等用 `TestClient`——它不经过真实 socket。
**本文件证明了二者的差别**：`TestClient` 的 64 项测试当时全绿，
但真实冒烟立刻抓到「官能团未过滤 matched=False」这个缺陷
（乙醇被报成含全部 11 个基团，见 §5.2）。

因此这一层不是重复测试，而是**覆盖 TestClient够不到的部分**：
真实 socket、真实的 uvicorn 启动/关闭、真实响应头。
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

import pytest

#: 选一个不太可能冲突的端口。固定端口在 CI 并发时会撞车。
PORT = 18123
BASE = f"http://127.0.0.1:{PORT}"

#: 等待服务就绪的上限（秒）。真实启动要建RDKit 引擎，容器内约 3-5 秒。
BOOT_TIMEOUT = 60.0


def _free_port_wait() -> None:
    """确认端口未被占用。

    端口被占用时服务会静默启动失败或绑到别的端口，
    表现为连不上——先排除这个干扰因素。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", PORT))
        except OSError as exc:  # pragma: no cover - 环境异常
            pytest.skip(f"端口 {PORT} 被占用，跳过端到端测试：{exc}")


def _request(
    path: str,
    data: Any = None,
    headers: dict[str, str] | None = None,
    *,
    timeout: float = 30.0,
) -> tuple[int, str]:
    """发一个 HTTP 请求，返回 ``(状态码, 响应体文本)``。

    刻意用标准库 urllib 而非 httpx：本文件的目的之一是验证
    **第三方测试客户端之外**的行为，且避免与 TestClient 共享代码路径。
    """
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(BASE + path, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        # 服务已响应但状态码是 4xx/5xx——这是有效结果，返回之。
        # 注意顺序：HTTPError 必须在 URLError 之前捕获，前者是后者的子类。
        return exc.code, exc.read().decode()
    except (urllib.error.URLError, OSError):
        # 服务尚未监听（URLError / Errno 111）、超时等。
        #
        # 返回 None 表示"还没就绪"，让 fixture 的轮询循环继续等。
        # 实测踩过：起初这里写的是 `raise`，结果连接失败时 pytest 报的是
        # unhandled URLError 长栈，看起来像应用崩溃，实际只是服务还在
        # 启动——极易把排查方向带偏。
        return None, ""


@pytest.fixture(scope="module")
def server() -> subprocess.Popen:
    """启动真实 uvicorn 服务。

    用模块级 fixture：启动开销大（建 RDKit + 探测组件），
    每个测试重启一次会让本文件慢十倍。
    """
    _free_port_wait()
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "app.api.app:app",
            "--host", "127.0.0.1", "--port", str(PORT),
            "--app-dir", "backend", "--log-level", "warning",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + BOOT_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            pytest.fail(f"服务启动即退出：\n{out[:2000]}")
        status, _ = _request("/health", timeout=2)
        if status is not None:
            break
        time.sleep(0.5)
    else:  # pragma: no cover - 超时路径
        proc.kill()
        pytest.fail(f"服务未在 {BOOT_TIMEOUT} 秒内就绪")

    yield proc

    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:  # pragma: no cover
        proc.kill()


class TestRealHttpEndpoints:
    """真实 socket 上的端到端行为。"""

    def test_health_over_tcp(self, server: subprocess.Popen) -> None:
        status, body = _request("/health")
        assert status == 200
        data = json.loads(body)
        assert data["status"] in {"ok", "degraded", "not_ready"}
        names = {c["name"] for c in data["components"]}
        assert {"chem", "llm", "rag"} <= names

    def test_ready_reflects_configuration(self, server: subprocess.Popen) -> None:
        """未配密钥时应返回 503——编排系统据此不接流量。"""
        import os

        status, _ = _request("/ready")
        if not os.environ.get("MAAS_API_KEY"):
            assert status == 503, "无密钥时 /ready 必须 503"
        else:  # pragma: no cover - 有密钥的环境
            assert status == 200

    def test_molecule_over_tcp(self, server: subprocess.Popen) -> None:
        """真实 HTTP 上的化学解析。"""
        status, body = _request(
            "/api/v1/molecule", {"smiles": "c1ccccc1O"},
            {"Content-Type": "application/json"},
        )
        assert status == 200
        data = json.loads(body)
        assert data["ok"] is True
        assert data["properties"]["molecular_formula"] == "C6H6O"
        assert data["properties"]["molecular_weight"] == pytest.approx(
            94.113, abs=1e-2
        )
        # 必须带"不代表机理已验证"的声明（architecture.md §5）
        assert data["notes"], "应有关于验证边界的说明"

    def test_functional_groups_over_tcp(self, server: subprocess.Popen) -> None:
        """**回归保护**：乙醇经真实 HTTP 也只能返回羟基。

        这个缺陷最初就是被真实冒烟抓到的——TestClient 当时没暴露。
        """
        status, body = _request(
            "/api/v1/molecule", {"smiles": "CCO"},
            {"Content-Type": "application/json"},
        )
        assert status == 200
        names = {g["name"] for g in json.loads(body)["functional_groups"]}
        assert names == {"醇羟基"}, f"乙醇只应含醇羟基，实得 {names}"

    def test_phenol_over_tcp(self, server: subprocess.Popen) -> None:
        """苯酚须命中**酚羟基**——决策项 I5 的端到端验证。

        若用 ``[OX2H][CX4]`` 排除羧酸，苯酚会被漏掉，
        等于把「误报羧酸」换成「漏掉苯酚」。而本项目语料明确讲苯酚。
        """
        status, body = _request(
            "/api/v1/molecule", {"smiles": "c1ccccc1O"},
            {"Content-Type": "application/json"},
        )
        assert status == 200
        names = {g["name"] for g in json.loads(body)["functional_groups"]}
        assert "酚羟基" in names, f"苯酚应命中酚羟基，实得 {names}"
        assert "醇羟基" not in names

    def test_missing_content_type_over_tcp(self, server: subprocess.Popen) -> None:
        """缺 Content-Type 经真实 HTTP 也应是 400 且不泄露输入值。

        脱敏检查必须**按 JSON 结构判定**，不能用 `"input" not in body`
        这种子串匹配——实测踩过：错误码 ``api_invalid_input`` 里就含
        "input" 子串，会假阳性。正确做法是解析后确认没有
        ``input`` 这个**键**。
        """
        req = urllib.request.Request(
            BASE + "/api/v1/molecule",
            data=b'{"smiles": "CCO"}',
            headers={"Content-Type": "text/plain"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                status, body = resp.status, resp.read().decode()
        except urllib.error.HTTPError as exc:
            status, body = exc.code, exc.read().decode()

        assert status == 400
        payload = json.loads(body)
        assert payload["error"]["code"] == "api_invalid_input"

        #逐个确认没有 input 键（结构判定，非子串匹配）
        assert "input" not in payload["error"], "error 里不应有 input 键"
        for detail in payload["details"]:
            assert "input" not in detail, "details 里不应有 input 键"
        # 用户提交的值也不得出现在响应中
        assert "CCO" not in body, "不应回显用户提交的内容"

    def test_openapi_over_tcp(self, server: subprocess.Popen) -> None:
        status, body = _request("/openapi.json")
        assert status == 200
        spec = json.loads(body)
        assert len(spec["paths"]) == 5

    def test_sse_over_tcp_when_llm_unavailable(self, server: subprocess.Popen) -> None:
        """无密钥时SSE 仍返 200 并走 error 事件通道。

        这是 SSE 的固有约束的**真实网络层证据**：响应头已发出，
        无法再改状态码，故错误只能塞进事件流。
        """
        import os

        if os.environ.get("MAAS_API_KEY"):  # pragma: no cover
            pytest.skip("已配置密钥，不在缺配置路径上")

        req = urllib.request.Request(
            BASE + "/api/v1/ask/stream",
            data=json.dumps({"question": "苯酚的酸性"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=40) as resp:
            status = resp.status
            ctype = resp.headers.get("content-type", "")
            raw = resp.read().decode()

        assert status == 200
        assert ctype.startswith("text/event-stream")
        names = [
            line[7:].strip()
            for line in raw.split("\n")
            if line.startswith("event: ")
        ]
        # 首事件必须是 meta（前端靠它立即渲染加载态）
        assert names[0] == "meta"
        # 失败时不得有 result，否则前端会误以为成功
        assert "result" not in names
        assert names[-1] == "error"


__all__ = ["TestRealHttpEndpoints"]
