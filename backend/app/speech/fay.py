"""Fay 数字人适配。

## 契约来源：读源码而非猜文档

本文档依据的是Fay 官方仓库 `github.com/xszyou/Fay` 的
`gui/flask_server.py` 源码（2026-10-04 实测拉取核对），
不是二手博客。**Fay 的飞书文档需要登录才能访问**，
而 CSDN 等二手资料已出现过与源码不符之处，故以源码为准。

### 实测确认的契约

```text
POST /transparent-pass
Content-Type: application/json

{"user": "User", "text": "要播报的内容", "audio": null}
```

- **``user``**：Fay 侧的会话标识，默认 ``"User"``。
  该字段不是可选项——源码里``data.get('user', 'User')``虽有默认值，
  但多用户场景下不同学生共用 ``"User"`` 会**互相打断**
  （非队列模式下会清空该用户之前的文本流与音频队列）。
- **``text``**：要播报的文本。与 ``audio`` 至少有一个为真值才处理。
- **``audio``**：音频 URL。传相对路径时 Fay 会按
  ``Origin``/``Referer``/``host_url`` 拼成绝对地址——
  **这意味着我们本地合成的音频若放在临时目录，Fay 拉不到**。
  故本模块默认只送 ``text``，由 Fay 自己合成语音。

### 两个文档没记、但影响实现的事实

1. **失败时仍返回 HTTP 200**。源码里业务失败走
   ``jsonify({'code': 500, 'message': '未知原因出错'})``，
   **没有传第二个参数**，故实际 HTTP 状态码仍是 200。
   只看 HTTP 状态码会把失败判成成功——必须读 body 里的 ``code``。
2. **没有认证**。源码无 ``@auth.login_required``。
   本模块因此**在请求里不携带任何凭据**，也建议部署时
   不把Fay 端口暴露到公网（``security-privacy.md`` §3）。

## 为什么不接10002 的 WebSocket

10002（``HumanServer``）是给**数字人渲染端**（Live2D / UE / Unity）用的，
消息含口型数据（``Lips``）与动作语义（``Action``）。
本项目的 Web 前端不直接驱动数字人形象，故只需单向推送文本，
用 ``/transparent-pass`` 足够——**不引入 WebSocket 长连接**，
也就没有断线重连与心跳这两个失败面。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from .config import SpeechSettings
from .models import DigitalHumanResult

logger = logging.getLogger(__name__)

#: Fay 侧默认的会话标识。
DEFAULT_FAY_USER = "User"


class FayClient:
    """Fay 透明通道客户端。

    刻意用 ``urllib`` 而非 ``requests``/``httpx``：本项目已在
    ``requirements.txt`` 锁了 ``requests``，但**语音是可选能力**——
    少一个运行时依赖就少一处版本冲突与供应链面
    （``security-privacy.md`` §5）。标准库够用。

    无状态，可安全复用。
    """

    def __init__(self, settings: SpeechSettings | None = None) -> None:
        # None 时从环境变量读，不留默认值——否则 XUEZHI_FAY_ENABLED
        # 与 XUEZHI_FAY_URL 完全无效（详见 service.py 同处说明）
        self._settings = settings if settings is not None else SpeechSettings.from_env()

    @property
    def settings(self) -> SpeechSettings:
        return self._settings

    def _build_payload(self, text: str, user: str) -> dict[str, Any]:
        """构造请求体。

        ``audio`` 显式送 ``None``：源码里 ``audio`` 为假值时
        会走纯文本分支，行为与不送该字段一致。显式送是为了
        让请求体自解释——读代码的人不必去查 Fay 源码才知道
        我们不用它的音频通道。
        """
        return {"user": user, "text": text, "audio": None}

    @staticmethod
    def _build_opener(timeout: float) -> urllib.request.OpenerDirector:
        """构造**不走代理**的 opener。

        ## 实测踩到的坑（重要，部署必读）

        本机环境设了 ``HTTP_PROXY`` / ``http_proxy``，
        而 ``urllib.request.urlopen`` **默认读这些环境变量**。
        结果是：请求 ``http://127.0.0.1:5000`` 这个**本机**地址时，
        也被丢给代理，代理返回 **HTTP 502**，
        表现为「Fay 明明在跑却说不可用」。

        实测记录：设 ``NO_PROXY=127.0.0.1,localhost`` 后立即成功。
        但**不能依赖部署环境恰好设了 NO_PROXY**——
        演示机的环境不可控。故代码里显式清空代理。

        只对**回环地址**这样做才是安全的，但本模块的 Fay 地址
        本就应当是本机或内网（Fay 是本地服务），
        故无条件清空。若将来要接远程 Fay，需改为按地址判断。
        """
        # ProxyHandler({}) 表示"不使用任何代理"
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def push_text(self, text: str, *, user: str = DEFAULT_FAY_USER) -> DigitalHumanResult:
        """推送文本，让数字人播报。

        **本方法不抛异常**：任何失败都转成 ``delivered=False``。
        数字人是纯增强，失败时必须能继续交付文本答案。

        Args:
            text: 要播报的内容。
            user: Fay 侧会话标识。

        Returns:
            :class:`DigitalHumanResult`。
        """
        settings = self._settings
        if not settings.fay_enabled:
            return DigitalHumanResult(
                delivered=False, reason="数字人功能未启用。"
            )
        if not text.strip():
            return DigitalHumanResult(delivered=False, reason="没有可播报的内容。")

        endpoint = settings.fay_endpoint
        body = json.dumps(
            self._build_payload(text, user), ensure_ascii=False
        ).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 - 端点由配置指定且已校验
            endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            #显式不走代理：见 _build_opener 的实测说明
            opener = self._build_opener(settings.fay_timeout)
            with opener.open(request, timeout=settings.fay_timeout) as response:  # noqa: S310
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            # 仍读body：Fay 在500 分支里带了具体异常信息，
            # 但那是**上游原文**，只进日志不进响应
            detail = f"HTTP {exc.code}"
            logger.warning("Fay 推送失败：%s", detail)
            return DigitalHumanResult(
                delivered=False, reason="数字人服务返回错误。", detail=detail
            )
        except (urllib.error.URLError, OSError) as exc:
            # 连接被拒/超时是最常见情形（演示前忘启动 Fay）
            logger.warning("Fay 不可达：%s", type(exc).__name__)
            return DigitalHumanResult(
                delivered=False, reason="数字人服务未启动或不可达。", detail=type(exc).__name__
            )

        return self._interpret(raw)

    @staticmethod
    def _interpret(raw: str) -> DigitalHumanResult:
        """解析响应体。

        **必须读 body 的 ``code``，不能只看 HTTP 状态码**——
        实测确认 Fay 的业务失败分支返回的是 HTTP 200 +
        ``{"code": 500}``（源码没给 ``jsonify`` 传第二个参数）。
        这是本适配器最容易写错的地方。
        """
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Fay 响应非JSON，按失败处理")
            return DigitalHumanResult(
                delivered=False, reason="数字人服务响应异常。", detail="non-json"
            )

        if not isinstance(payload, dict):
            return DigitalHumanResult(
                delivered=False, reason="数字人服务响应异常。", detail="not-dict"
            )

        code = payload.get("code")
        # 只认 200/0/200 三种成功表示；Fay 成功时返回 200
        if code in (200, 0, "200"):
            return DigitalHumanResult(delivered=True)

        # 失败：把message 记进日志（上游原文可能含内部细节），
        # 但给学生的说明用本层撰写的话术
        detail = f"code={code}"
        logger.warning("Fay 返回业务失败：%s", detail)
        return DigitalHumanResult(
            delivered=False, reason="数字人未能接收播报内容。", detail=detail
        )


__all__ = ["DEFAULT_FAY_USER", "FayClient"]
