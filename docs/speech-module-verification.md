#语音模块实现与验证（backend-speech）

本文记录 `backend-speech` 的实现范围、**实测到的外部契约**、三个被测试抓出的真实缺陷，以及未验证事项。

日期：2026-10-04。所有结论均为实跑得出，不含推测值。

---

## 1. 实现范围与边界

| 能力 | 实现 | 状态 |
| --- | --- | --- |
| Edge-TTS 语音合成 | `speech/tts.py` | 实测通过（真实联网合成） |
| Fay 数字人文本推送 | `speech/fay.py` | 实测通过（对着模拟服务跑通协议） |
| 统一降级编排 | `speech/service.py` | 实测通过 |
| 错误码与文案 | `speech/errors.py` | 已登记，含状态码 |
| API 暴露 | **未做** | 见 §7 |

**明确不做**（`docs/product-scope.md` §5）：

- 不做 ASR（语音识别）——产品范围未列，且会引入音频上传这一隐私面。
- 不接 10002 的 WebSocket——那是给数字人**渲染端**（Live2D/UE/Unity）用的，
  本项目前端不直接驱动形象，单向推文本足够。
- 不在语音失败时阻断文本答复——见 §3。

---

## 2. 依赖选型（联网核实，非凭记忆）

### edge-tts 7.2.8

| 项 | 实测值 | 来源 |
| --- | --- | --- |
| 包类型 | **纯 Python wheel**（`edge_tts-7.2.8-py3-none-any.whl`） | PyPI JSON API |
| 许可证 | **LGPLv3** | PyPI classifier |
| requires_python | `>=3.7` | PyPI JSON API |
| 运行时依赖 | aiohttp / certifi / tabulate / typing-extensions | PyPI JSON API |

**选它的关键理由**：纯 Python wheel 意味着**无原生扩展**，
不受本机应用控制策略拦截。这与 RDKit 恰好相反——
RDKit 的 `.pyd` 被拦导致本机完全不可用（`chem-engine-verification.md` §2）。
同属"被系统策略卡的第三方库"，edge-tts 反而能跑。

**代价**：它调用**微软在线服务**，断网必然失败。
故本项目**不得**据此宣称"离线可用"——断网演示须换本地 TTS，
已登记为决策 B5。

---

## 3. 降级设计（本模块的核心）

`docs/architecture.md` §6 定下「文本回答为主交付，语音视为可降级能力」。
本模块的降级阶梯：

1. TTS + Fay 都成 → 完整体验
2. 仅 TTS 成 → 有音频，无数字人
3. 都不成 → **纯文本**（主交付始终可用）

### 为什么用「结构化结果」而非「抛异常」

多数实现会把"没有语音"表达成抛异常或返回 `None`，
但那两种表达都把**降级变成了异常状态**——前端要写 try/except 才能正常显示。

本模块改为：`speak()` **保证不抛异常**，返回
:class:`SpeechOutcome`，用 `stage` 标明实际状态：

| stage | 含义 | 前端应做的事 |
| --- | --- | --- |
| `ready` | 有音频 | 正常播放 |
| `disabled` | 用户/配置显式关闭 | **不显示错误样式**（这是正常选择） |
| `not_configured` | 缺配置 | 提示但不报错 |
| `unavailable` | 服务故障 | 提示"语音暂时不可用" |

四态而非两态，是为了区分**"用户主动关掉"**与**"服务坏了"**——
前者是正常选择，显示错误样式是错的。

---

## 4. Fay 契约（读源码核实，非猜测）

**核实过程**：Fay 的飞书文档需登录才能访问，而 CSDN 等二手资料
已出现与源码不符之处（如把 10002 说成 HTTP 接口）。
故直接拉取官方仓库 `github.com/xszyou/Fay` 的 `gui/flask_server.py` 核对。

### 实测契约

```text
POST {base}/transparent-pass
Content-Type: application/json

{"user": "User", "text": "要播报的内容", "audio": null}
```

成功响应：`{"code": 200, "message": "成功"}`

### 三个文档没记、但影响实现的事实

#### ① 业务失败仍返回 HTTP 200

源码的失败分支是：

```python
return jsonify({'code': 500, 'message': '未知原因出错'})
```

**没有传第二个参数**，故真实 HTTP 状态码是 **200**。
只看状态码的实现会把失败判成成功——必须读 body 的 `code`。

这是本适配器最容易写错的地方，已加测试
`test_http_200_with_code_500_is_failure` 锁定。

#### ② `user` 字段必须区分

源码里 `data.get('user', 'User')` 虽有默认值，但
**非队列模式会清空该用户之前的文本流与音频队列**。
多学生共用 `"User"` 会互相打断。

故 `speak()` 暴露 `user` 参数，默认 `"User"` 但允许调用方区分。

#### ③ 没有认证

源码无 `@auth.login_required`。本模块因此**不携带任何凭据**，
并建议部署时不要把 Fay 端口暴露到公网（`security-privacy.md` §3）。

### 许可证：GPL-3.0（更正决策登记 A4）

**决策登记表 A4 原记载「完全开源，商用免责」——这句不准确。**
核实结果：官方仓库的 README 里有"完全开源，商用免责"这句**功能描述**，
而仓库的**许可证是 GPL-3.0**（gitee 与 github 均标注）。

两者不是一回事，须区分：

- GPL-3.0 的 copyleft 在**分发**时触发。
- 本项目只**HTTP 调用**它的 `/transparent-pass` 接口，
  **不修改、不分发**其代码，不触发 copyleft。
- 这是业界通行理解，**非法律意见**；若要用于对外分发或商业交付，
  应请法务确认。

用户已确认按"只做 HTTP 调用，不改 Fay 代码"实施。

---

## 5. 实测记录

### 5.1 真实 edge-tts 合成

```text
输入：'乙醇的化学式是CCO，它含有羟基。'（rate=-10）
结果：stage=ready, available=True
产物：26352 字节，magic=b'\xff\xf3d\xc4'（有效 MP3 帧头）
char_count=17, truncated=False
```

### 5.2 端到端 API 验证（对着模拟的 Fay 服务）

用复刻源码行为的模拟服务（业务失败同样返回 HTTP 200）验证：

| 场景 | 结果 |
| --- | --- |
| 成功路径 | `delivered=True` |
| 业务失败（HTTP 200 + `code:500`） | `delivered=False`，`reason=数字人未能接收播报内容。` |
| Fay 未启动 | `delivered=False`，`reason=数字人服务未启动或不可达。` |

Fay 侧实际收到的请求（模拟服务打印）：

```text
[FaySim] path=/transparent-pass payload={'user': 'student-01', 'text': '乙醇的化学式是CCO。', 'audio': None}
```

字段名与源码一致。

### 5.3 降级链

Fay 指向未启动端口时：

```text
speech.stage: ready | available: True
fay.delivered: False | reason: 数字人服务未启动或不可达。
```

**语音仍然可用**——这正是设计目标。

### 5.4 `/health` 组件

```json
{"name": "speech", "ready": true, "detail": "tts=就绪 fay=就绪"}
```

**刻意让 speech 恒为 `ready`**：它是纯增强能力，
不可用时文本照常交付，故不该让就绪判定失败。
真实可用性由 `stage` 与 `reason` 表达。

`probe()` **不实际合成音频**——`/health` 会被编排系统高频轮询，
真合成会把健康检查变成网络基准测试。

---

## 6. 被测试抓出的三个真实缺陷

这三个都不是"写错了"，是**设计意图与实现不一致**，
且都不会让任何测试自然失败。

### 6.1 配置从未被读到（最严重）

**现象**：`/health` 报 `fay=未启用`，但容器里确实设了
`XUEZHI_FAY_ENABLED=1` 和 `XUEZHI_FAY_URL`。

**根因**：`SpeechService.__init__` 写作 `settings or SpeechSettings()`。
`SpeechSettings()` 用的是**默认值**而非环境变量，
于是环境变量完全没被读取。

**为什么难以发现**：

- 默认值**完全合法**，故不会有任何校验报错；
- 单元测试都显式注入配置，故全绿；
- 只有"设了环境变量再实跑"才暴露。

**修复**：改为 `settings if settings is not None else SpeechSettings.from_env()`，
`TTSEngine` 与 `FayClient` 同样修正。

**新测试**：`TestConfigIsActuallyRead`（4 项），用 `monkeypatch.setenv` 锁住
"None ⇒ 读环境变量"这一约定。

> 这条是本项目最容易重犯的错误类型：**默认值合法 ⇒ 静默失效**。
> 与 `api/errors.py` 的"文案表缺失"同源——
> 都是"缺了也不报错"。

### 6.2 配置笔误会让整个服务起不来

**现象**：`from_env` 把非法数值原样传给构造器，
`__post_init__` 直接抛 `ValueError`。

即 `XUEZHI_TTS_MAX_CHARS=-5` 这一个笔误就让 API 网关启动失败——
**与"语音可降级"的定位直接矛盾**。

同类问题：`XUEZHI_TTS_VOICE="   "`（纯空格）。
`"   "` 是真值故不走 `or DEFAULT_VOICE` 分支，
strip 后成空串再被构造器拒绝。

**修复**：`from_env` 内在读入处**夹取/回落**，不冒到构造器：

- 越界数值夹到边界（`-5`→1，`9999`→100）
- 纯空白音色回落默认

**为什么夹取而非报错**：夹取不会带来危险行为——
上限只会让请求更早被拒，下限只会让文本更早被截断。

**新测试**：`test_out_of_range_env_is_clamped_not_rejected` +
`test_from_env_never_raises_on_bad_config`。

> **配置来源不同，容错策略也应不同**：
> 构造器收到 `-5` 该抛错（编程错误），
> 环境变量读到 `-5` 该夹取（笔误）。
> 两者用同一个 `or` 表达式处理，必然有一边错。

### 6.3 本机 HTTP 代理劫持本地请求

**现象**：Fay 明明在跑，却报"数字人服务返回错误"，
细节是 **HTTP 502**。

**根因**：本机设了 `HTTP_PROXY`，而 `urllib.request.urlopen`
**默认读这些环境变量**——于是请求 `http://127.0.0.1:5000`
这个本机地址也被丢给代理，代理返回 502。

**为什么危险**：演示机环境不可控，
**不能依赖"恰好设了 `NO_PROXY`"**。
这个故障的表现是"Fay 在跑却说不通"，极难定位。

**修复**：显式用空 `ProxyHandler` 构造 opener。

```python
urllib.request.build_opener(urllib.request.ProxyHandler({}))
```

**实测验证**：设 `HTTP_PROXY=http://127.0.0.1:54510` 后
推送仍 `delivered=True`（修复前是 502）。

**新测试**：`test_bypasses_http_proxy_env`，并**反向验证**过——
把 `ProxyHandler({})` 改回默认后该测试立即失败。

---

## 7. 未完成与未验证事项

| 事项 | 状态 | 原因 |
| --- | --- | --- |
| API 端点（`POST /api/v1/speak`） | **未做** | 需先定契约：音频如何返回给前端（临时文件路径？base64？静态文件服务？）。临时文件在跨进程部署下不可用，须先设计 |
| 前端语音播放 | 未做 | 依赖上面的 API 契约 |
| 真实 Fay 服务联调 | **未做** | 本机无 Fay 实例。用复刻源码行为的模拟服务验证协议，字段名与源码一致，但**未在真实 Fay 上跑过** |
| 断网演示 | **不可用** | edge-tts 依赖外网。须换本地 TTS（决策 B5） |
| 字幕/时间戳 | 未做 | edge-tts 支持 `metadata_fname` 输出 WebVTT，本模块未用 |

### 接入 API 前必须先定的事

临时音频文件的生命周期是**真正的设计问题**，不是实现细节：

- 合成产物写在服务进程的临时目录，前端如何取到？
- 进程重启后临时文件消失，已返回的 URL 是否要处理？
- 多学生并发时如何隔离？

**这些问题不解决就写端点，只会写出一个演示能用、部署即坏的接口。**
故本轮刻意不实现，登记为待决策。

---

## 8. 测试统计

```text
661 passed, 36 skipped
```

其中 speech 模块 43 项（新增）。跳过项需真实外部服务（模型 API），非回归。

**替身与真实包的关系**：`TestAgainstRealEdgeTTS` 一类**对着真实
`edge_tts` 包**验证接口契约（签名、参数形态、异常层次），
不真调合成（那会依赖外网）。这遵循本项目的既有教训——
已三次因替身与真实依赖不一致而误判（`docs/development-log-2026-10.md`）。

---

## 9. 关联文档

- `architecture.md` §6 —— 语音可降级的原始要求
- `product-scope.md` §5 —— 「不保证第三方服务始终可用」的非目标声明
- `chem-engine-verification.md` §2 —— 对比：RDKit 被策略拦截 vs edge-tts 纯 wheel
- `container-runtime-verification.md` —— 容器化运行与错误文案补齐
- `decision-register.md` A4 / B5 / H3 —— Fay 版本协议、离线依赖、Python 版本约束
- `interface-contract.md` §5 —— 错误码分类约定
- `security-privacy.md` §3 —— 不回显内部细节、不暴露端口
