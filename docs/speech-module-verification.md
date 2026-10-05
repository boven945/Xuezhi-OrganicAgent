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
| 前端语音播放 |未做 | 后端已就绪，前端尚未消费 |
| 真实 Fay 服务联调 | **未做** | 本机无 Fay 实例。用复刻源码行为的模拟服务验证协议，字段名与源码一致，但**未在真实 Fay 上跑过** |
| 断网演示 | **不可用** | edge-tts 依赖外网。须换本地 TTS（决策 B5） |
| 字幕/时间戳 | 未做 | edge-tts 支持 `metadata_fname` 输出 WebVTT，本模块未用 |

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

---

## H21：语音 API 端点（2026-10-04晚）

### 用户决策

- 交付方式：**两阶段**（JSON 返 `audio_id` + 独立音频端点）
- 过期策略：**两者都要**（惰性清理 + 定时清扫）

### 为什么不用base64 内联（联网核实）

| 维度 | base64 |独立端点 |
| --- | --- | --- |
| 体积 | **+33%** | 原大小 |
| 浏览器缓存 | **无法单独缓存**（在 JSON 里） | 可缓存、可 range |
| 拖动进度条 | 不支持 | 支持 |

实测 26KB 音频 → base64 约 35KB。短语音可接受，长语音明显劣化。

### 关键实现约束：不挂 BackgroundTask

**实测踩到（差点照搬官方做法）**：FastAPI 官方文档推荐用
`BackgroundTask` 在响应后删临时文件。但——

```text
starlette 1.7.0 的 BackgroundTask.__call__ 源码里无 CancelScope/shield
本项目正好用了 BaseHTTPMiddleware
```

这正是 starlette #1438 报告的组合：**客户端断开连接时后台任务被取消**。
若把清理挂上去，学生一关页面音频就永久残留，磁盘持续增长。

故改为两条路径（用户决策的"两者都要"恰好对应）：

- **惰性清理**（`store.fetch`）：取用时判超龄 → 删 → 404。
  **这是正确性保证**：不依赖后台机制，进程重启也不影响。
- **定时清扫**（`store.sweep`）：lifespan 起 asyncio 任务，
  周期 300s。**这是磁盘保证**：演示长时间挂机时没有音频被取用，
  惰性清理永远不触发，磁盘会满。

>周期须**明显小于 TTL**（300 vs 600秒），
> 否则极端情况下文件可能在过期后很久才被清走。

### 过期与不存在统一 404

区分二者会让「曾经存在过」成为可观测信息，而前端不需要知道
（`security-privacy.md` §3）。

### 三个实测抓出的缺陷

#### ① Dockerfile 漏 COPY scripts/（影响已有测试）

契约测试用 `python -m scripts.export_openapi`，而镜像里
**根本没有 `scripts/` 目录**——报`No module named 'scripts'`，
看起来像脚本路径写错，实则是 Dockerfile 的疏漏。
已补`COPY scripts /work/scripts`。

#### ② 双向检查的采集范围漏了 speech 与 api

新增 `app/speech/errors.py` 后，采集器仍只扫
`llm/rag/chem/agent`——**speech 完全不在检查范围内**，
双向一致性检查对它**失效**。补上后立刻报出 5 个缺失码。

补`api` 是因为 `_AudioGoneError` 定义在 `app/api/errors.py`
（与 `_RateLimited` 同处），原先也被漏掉。

#### ③ classify 只查 API_ERROR_SPECS，导致状态码与文案矛盾

端到端实测拿到：`HTTP 404` + `api_internal_error` + "服务内部错误"。

**两个字段自相矛盾**：学生看到 404 却收到"服务内部错误"，
会以为重试有用，而实际上重试同一个 id 仍会 404。

根因：`classify` 里`own_code in API_ERROR_SPECS` 只覆盖
``api_`` 前缀，而 `speech_audio_gone` 在 `DOMAIN_CODE_SPECS`。
已改为查两张表，并加 2 项回归测试。

> 这类缺陷**结构上抓不到**：码登记了、文案也存在，只是查找路径没走到。

### 一次自己的断言写错

`test_empty_text_rejected_by_validation` 断言 422，
实际 400。查证后确认**项目一贯用 400**
（`api_invalid_input`，自定义校验处理器把Pydantic 错误映射为 400
以替换英文文案）。既有 `test_app.py` 全部断言 400。

> 跟随项目约定，而不是框架默认值。

### 端到端实测（真实起服务）

| 场景 | 结果 |
| --- | --- |
| 正常合成 | `stage=ready`，audio_id + audio_url |
| 取音频 | 200，13248 字节，`audio/mpeg` |
| 未知 id | 404 + `speech_audio_gone` + "语音已失效，请重新生成。" |
| 路径穿越 `..%2F..%2Fetc%2Fpasswd` | **404 而非 500** |
| 关掉 TTS | `stage=disabled`，`available=false` |
| 分子接口（降级保证） | 正常返回 CCO |

### 测试

**742 passed, 36 skipped**（新增 23 项语音端点测试，零回归）

### 仍未做

| 事项 | 原因 |
| --- | --- |
| 前端语音播放 | 后端已就绪，前端尚未消费 |
| 多 worker 目录共享 | 已登记 H23——多 worker 须挂载同一数据目录 |


---

## H22：Fay 服务容器化与连接验证（2026-10-05 凌晨）

### 起因

用户要求「开始跑 Fay 服务」。目的**不是让它发音**（那需要 TTS 密钥），
而是验证 10002 WebSocket 连接与 `Action` 的真实形态。

### 读源码核实：推翻了三处（含我自己上轮的说法）

#### ① 我上轮说「Fay 推音素时间轴」——那是文档示例，不是实现

上轮我依据官方文档说Fay 推 `Lips`。读源码后发现：
`core/wsa_server.py` 与全部 `tts/*.py` 里都搜不到 `Lips`，
直到在 `core/fay_core.py:2281` 找到真正的产生点：

```python
if platform.system() == "Windows":              # ← 仅 Windows
    lip_sync_generator = LipSyncGenerator()
    viseme_list = lip_sync_generator.generate_visemes(wav_path)
    content["Data"]["Lips"] = consolidate_visemes(viseme_list)
```

口型靠`test/ovr_lipsync/ovr_lipsync_exe/ProcessWAV.exe`
从 WAV **离线分析**得出。

> **结论**：**Linux 容器内 `Lips` 恒为空**，口型只能走本地近似。
> 好消息是格式 `{Lip, Time}` 与我们前端写的一致（`Time` 为毫秒，
> 由 `count*33` 累加得出），那部分工作没白做。
> **"真实音素同步口型"这个能力我们没有**，已从卖点里撤掉。

#### ② `Action.behavior` 的真实取值

来自 `config/action_rules.csv`（实测导出 **20 条规则 / 18 种 behavior /
9 种 affect**），由**关键词匹配**得出，例如：

| code | behavior | affect | 触发关键词 |
| --- | --- | --- | --- |
| `dialogue.think` | think | neutral | 让我想想 / 想一想 |
| `dialogue.question` | question | curious | 为什么 / 怎么回事 |
| `guidance.warn` | warn | serious | 注意 / 小心 / 警告 |
| `emotion.celebrate` | celebrate | excited | 太好了 / 成功 / 真棒 |

**我先前只映射 7 个取值**（照文档示例猜的），
而真实场景会大量落待机——学生看到的是"老师不动"。
**已按真实表重做映射，并加 `affect` 兜底**（behavior 18 种覆盖不全，
例如 `warn` + `serious` 只靠 affect 才能命中）。
化学课高频场景全部可命中。

#### ③ 换 TTS 不会带来音素

针对"华为 SIS TTS 可直接喂给 Fay 做对口型"的说法：
音素由上述本地 exe 分析 WAV 得出，**与 TTS 无关**。
且实测 `huaweicloudsdksis==3.1.216` **只有 v1**（无 v2），
`CustomResult` 只有 `data: str`（Base64 音频），无时间戳无音素。

### 容器化：四道坎

| # | 问题 | 处置 |
| --- | --- | --- |
| 1 | apt **502 Bad Gateway** | 包名与源都没问题，是网络抖动。加三次重试 |
| 2 | pip **ResolutionImpossible** | Fay 写死 `websockets~=10.4`，而其 `langgraph-sdk` 要求 >=14——**上游清单没跟上自己的新依赖** |
| 3 | **放宽 websockets 反而把服务改坏** | 10002 起不来：`websockets.serve()` 同步 API 在 v14 已移除 → `no running event loop` |
| 4 | pyaudio **无 Linux wheel** | 实测 PyPI 0.2.14 只有 win32/win_amd64，必须装 portaudio19-dev + 工具链编译 |

第 3 条是本轮最重要的教训：

> **修A弄坏 B**：我为了解决 pip 冲突而放宽 websockets，
> 却不知道 Fay 用的是那个版本的**同步 API**。
> 正确解法是**降 langgraph-sdk**——实测 <= 0.3.15 的SDK **不依赖 websockets**，
> 冲突自然消失，Fay 的同步 API 也保持可用。
>
> **遇到依赖冲突时，改"约束较松的那一方"通常比改"被依赖的那一方"安全**——
> 因为后者往往是别人代码的接口契约。

> 另：第 4 条源于我上一轮推断「pyaudio 只在 ASR 里 import，应该不需要它」。
> **推断错了**。应该先查那个包在目标平台有没有 wheel，再决定装不装工具链。

### 配置注入：不能用环境变量直传密钥

Fay 的 `simulation_engine` 在**模块导入时**就构造 `openai.OpenAI()`，
缺 key 直接崩。而 `utils/config_util.py:540` 显示：

```python
key_gpt_api_key = system_config.get('key', 'gpt_api_key', ...)   # 只读文件，不读环境变量
```

**我第一版方案挂 `MAAS_API_KEY` 是无效的**，实测才发现。

正确机制是它官方支持的 `FAY_SYSTEM_CONF_JSON`（`config_util.py:105`），
可把整份配置以 JSON 传入，优先级高于文件。
故 compose 里注入：

```yaml
FAY_SYSTEM_CONF_JSON: >-
  {"key":{"gpt_api_key":"${MAAS_API_KEY:-}",
  "gpt_base_url":"https://api.modelarts-maas.com/openai/v1",
  "gpt_model_engine":"openpangu-2.0-flash",
  "tts_module":"edge_tts"}}
```

密钥经 `.env` 注入，**不写进任何仓库文件**。

> 顺带一个发现：Fay 的 TTS 清单里**没有 edge-tts**（五种全是付费 TTS），
> 但写进配置不报错——**配置项存在不代表该值有效**，
> 与本项目此前「配置项形同虚设」是同一类问题（那次是没人读，这次是读了但不支持）。

### compose 用 profile 隔离

Fay 是**可选增强**（`architecture.md` §6），默认不启动：

```bash
docker compose --profile fay up -d fay
```

理由：默认启动会让「一条命令跑起演示环境」变成拖起 8GB 第三方服务，
而它对问答、分子、3D、语音朗读**全都不是必需**。
实测 `docker compose config --services` 默认只列 `api` 与 `web`。


### 连接验证的实测结果（服务已跑起来）

```
10002: LISTENING      ← WebSocket 渲染端（宿主访问返回 426 Upgrade Required，正确）
10003: LISTENING      ← UI 数据
5000:  LISTENING      ← Flask HTTP
8765:  LISTENING      ← MCP
5010:  LISTENING      ← MCP service
```

`POST /transparent-pass` → `{"code":200,"message":"成功"}`（HTTP 200）

**但 10002 收不到消息**（探测 45 秒，0 条）。日志显示
`connection open` → `connection closed`，连接被正常接受，只是无数据。

**根因**：Fay 收到文本后要经TTS 产出音频才推给渲染端，
而**五种 TTS 全需密钥**（`ali` / `azure` / `gptsovits` / `gptsovits_v3` / `volcano`），
我们一个都没有。

>顺带一个**配置陷阱**：我一度填了 `tts_module=edge_tts`——
> **不报错但也不生效**。`fay_core.py:101-119` 是 if/elif 链，
> 不匹配任何分支 → TTS 根本没被初始化。
> **配置项被接受 ≠ 该值有效**——与本项目此前「配置项形同虚设」同类，
> 只是那次是没人读，这次是读了但不支持。
> 已加测试把这条固化成断言。

### 结论：连接层已验证，音频层需TTS 密钥

| 层 | 状态 | 依据 |
| --- | --- | --- |
| 容器构建与启动 | ✅ | 8.03GB 镜像，五个端口全监听 |
| 10002 WebSocket 握手 | ✅ | 宿主 `curl` 得 426 Upgrade Required |
| `/transparent-pass` 接口 | ✅ | `{"code":200,"message":"成功"}` |
| 消息推送（Action / Lips） | ⚠️ **未验证** | TTS 无密钥，产不出音频故不推 |
| 数字人联动 | ⚠️ **未验证** | 同上 |

**下一步需要什么**：任一 TTS 密钥（阿里云语音合成有免费额度，
Azure Speech 亦有）。有了之后：
1. 填进 `system.conf` 的 `ali_*` 三项
2. 重新探测 10002，应能收到 `{Key:audio, Action:{...}}`
3. 前端 `XUEZHI_FAY_ENABLED=1` 后即可看到真实动作

**没有密钥时的现状**：系统**完全可用**（问答 / 分子 / 3D / 语音朗读），
数字人恒为待机。Fay 是纯增强，不是必需项——这正是我们把它
放在 compose profile 里默认不启用的原因。

### 镜像构建的完整代价

| 项 | 实测值 |
| --- | --- |
| 镜像大小 | **8.03 GB** |
| 首次构建 | **28 分钟**（apt + pyaudio 编译 + 150+ 包） |
| 后续重建（仅改 pip 层） | 7 分钟 |
| dry-run 验证依赖解析 | 10 分钟（依赖树庞大） |

> 8GB 里大头是 torch 与 CUDA 库（Fay 依赖 chromadb → sentence-transformers → torch）。
> 若要瘦身，可考虑`--no-deps` 装部分包，**但收益与风险需另行评估**，
> 未实测前不轻易改依赖树。


---

## H23：Fay 端到端打通（2026-10-05 下午）

### 用户提示纠正了我的一个错误结论

用户说「我之前使用过 fay，他本身拥有大量功能」并附上功能清单。
**我此前说「Fay 的五种 TTS 全需密钥」是错的**——漏看了
`requirements.txt` 第19 行的 `edge_tts`，以及 `tts/ms_tts_sdk.py:47` 的实现：

```python
# tts/ms_tts_sdk.py:18
if config_util.key_ms_tts_key and ...:   # 有 Azure key 才走 Azure
    ...
# 没有 key → 回落 edge_tts（第 113 行调用）
```

**实测确认**（容器内直接调）：

```text
ms_tts（有 Azure key 才 True）: False
合成结果: ./samples/sample-1791186394920.wav
耗时: 1.9 秒    音频大小: 237122 字节
```

**结论：Fay 内置 TTS，不需要任何密钥。**

### `tts_module` 不是枚举校验，是 if/elif 链

`core/fay_core.py:101-128`：

```python
if   cfg.tts_module == 'ali':        from tts.ali_tss import Speech
elif cfg.tts_module == 'gptsovits':  ...
elif cfg.tts_module == 'volcano':    ...
else:                                from tts.ms_tts_sdk import Speech   # ← 兜底就是它
```

**任意非那三个的值都命中 `else`**（含 `ms_tts_sdk` 与 `edge_tts`）。
我先前判定「`edge_tts` 是不存在的值」——**判断依据错了**：
它不是枚举校验，而是 if/elif 链。已改用 `ms_tts_sdk`（语义明确）。

### 收到推送的三个必要条件（违反时不报错）

这是我白查两轮的原因——**连接正常、HTTP 200 正常，就是没有数据**。

| # | 条件 | 源码依据 |
| --- | --- | --- |
| 1 | 连接后**主动发** `{"Username":..., "Output":true}` | `wsa_server.py:28-45`；不发则不登记为接收端 |
| 2 | **`Username` 必须与推送时的 `user` 完全一致** | `get_client_output(user)` 按 username 精确匹配，不一致则**静默过滤** |
| 3 | 非 `queue:true` 才走完整合成 | `queue:true` 带 `no_reply:true`，**只播已合成音频**，不合成 |

条件 2 最隐蔽：我用 `Username=probe` 推送时 `user=p8`，
两者不一致 → 全部被丢弃。**改成一致后立刻收到。**

### 实测收到的消息形态

```json
{ "Topic": "human",
  "Data": {
    "Key": "audio",
    "Text": "注意，同分异构体很容易混淆，",
    "HttpValue": "http://.../audio/sample-xxx.wav",
    "Time": 8.9,
    "Sentiment": -0.4,
    "Lips": null,
    "Action": {"code": "guidance.warn", "behavior": "warn", "affect": "serious",
               "intensity": 0.8, "priority": 84, "matchedKeywords": ["注意"]}
  }}
```

`HttpValue` 实测可下载：`HTTP 200 / 237122 字节`，
与直接调 TTS 时的产物大小**完全一致**。

- `Lips: null` —— 印证 Linux 容器无口型数据（仅 Windows）
- `Action` 由**关键词匹配**得出（`config/action_rules.csv`）；
  纯陈述句（如"乙醇的官能团是羟基"）**不含触发词 → `Action` 为 null**，
  这是设计如此而非缺陷
- 另有 `Key: text` 的流式/结束标记，**不应据此改状态**（会打断正在播的音频）

### 端到端链路（含两个真实缺陷）

```
浏览器 → POST /api/v1/speak → 后端 FayClient
  → POST Fay:5000/transparent-pass → TTS 合成
  → WS 10002 推送 {Action, HttpValue} → 前端播 Fay 的音频 +驱动形象
```

**缺陷①：`XUEZHI_FAY_URL` 指向错误。**
`.env` 里原本是 `http://127.0.0.1:5000`，
但**容器内的 `127.0.0.1` 指向容器自己**，不是宿主 → `Fay 不可达：URLError`。
须用 `http://host.docker.internal:5000`。**这类错误只在容器化后暴露**，
本机跑时两者都是 127.0.0.1 看不出差别。

**缺陷②：前端会开两个 WebSocket。**
组件里 `watch(immediate: true)` 与 `onMounted(connect)` **都会连一次**。
两者都被Fay 登记为接收端 → 重复播放、状态互相覆盖。
已删掉 `onMounted(connect)`（watch 已覆盖"初始为 true"）。

**附带确认**：`push_digital_human` 属**`/api/v1/speak`** 而非 `/api/v1/ask`
（在 ask 上传该字段会得 `extra_forbidden`）。
且后端日志显示本地 TTS 30 秒超时，但 `digital_human_delivered: true`
——**推送确实独立于本地 TTS**，与 `service.py` 的注释一致。

### 当前能力边界（诚实记录）

| 能力 | 状态 |
| --- | --- |
| Fay 服务与 WebSocket 推送 | ✅ 端到端实测通过 |
| TTS 合成（edge_tts 回落） | ✅ 1.9 秒 / 237KB |
| `Action` 驱动形象动作 | ✅ 18 种 behavior + 9 种 affect |
| 音素级口型同步 | ❌ **仅 Windows**，Linux 容器内 `Lips` 恒空 |
| 真实音素驱动口型 | ❌ 只能本地近似 |

> **不能声称"音素同步口型"** —— 那需要 Windows 上直接跑 Fay
> （受本机应用控制策略影响，未实测）。
> 当前口型是本地近似：播放期间在待/说话两态间切换。


---

## H24：音量包络口型（替代缺失的音素同步）

### 起因与约束

Fay 的 `Lips`（音素时间轴）**仅在 Windows 生成**，
Linux 容器内实测恒为 `null`——所以"音素同步口型"这个能力我们没有。

用户要求做第二条路：**不依赖 Fay 私有能力，用浏览器原生手段**。

### 方案：音量包络驱动

用 `AudioContext` + `AnalyserNode` 拿实时 RMS，
映射到嘴部张开度，写进 CSS 变量驱动 `scaleY`。

**为什么这不是瞎猜**：音量包络本身随音节起伏，
中文朗读的音量峰谷与音节边界大致同步——
观感上比"定时在待/说话两态间切换"自然得多（后者像随机闪烁）。

### 放大系数是三步实测定的

用真实 Fay 音频在真实浏览器里采样，每改一次常数重跑：

| 系数 | 实测峰值 | 观感 |
| --- | --- | --- |
| 1.6（初版） | 0.18 | 几乎看不出在动 |
| 4.2 | 0.37 | 仍偏小 |
| **8.0** | **0.565** | **合适** |

真实音频的原始 RMS 只到0.05~0.1，故需大幅放大。
**三档都记进注释**，便于换音色时直接跳到合适档位。

实测采样（真实 Fay 音频，25 次/80ms）：
```
0.06 0.06 0.06 0.06 0.06 0.06 0.06 0.085 0.244 0.315 0.418 0.508 0.554
0.506 0.546 0.477 0.508 0.565 0.486 0.492 0.408 0.45 0.511 0.438 0.338
```
18 个不同取值，**随语音节奏起伏**。

### 三个自己犯的错（都被测试或实测抓出）

**① 对象字面量里的自引用**
```ts
return {
  start() { try { ... } catch { stop() } },   // ← stop 还没定义
  stop() { ... },
}
```
`start` 定义在 `stop` 之前，构造后立即 `start()` 会得到
`stop is not a function`。已改为**具名函数 `stopAll`**。
> 对象字面量方法互相引用时，定义顺序决定可用性。

**② 构造时就求值 `window.AudioContext`**
```ts
const Ctor = window.AudioContext ?? window.webkitAudioContext  // ← 立即求值
if (!Ctor) return { value: 0, ... }// 于是"暂时没有"被固化成"一直没有"
```
已改为 `resolveCtor()` **在 start 时才问**。
> 环境会变（策略放开、页面恢复、测试桩），
> 提前求值把瞬时状态固化成永久结论。

**③ 测试替身用箭头函数**
```ts
vi.stubGlobal('AudioContext', vi.fn(() => ctx))   // ← 箭头函数不能 new
```
抛 `() => ctx is not a constructor`，
**而这个异常被 `start()` 的 catch 吞掉**，
表现为"什么都没连上"——极难定位。
真实浏览器里 `AudioContext` 是 class，只有替身写错才暴露。

> 另：替身的 `close` 写在 class 字段里时，外部持有的 `ctx.close`
> 是**另一个** mock，断言永远不成立。已提到 class 外声明。

### 能力边界（仍不能说"音素同步"）

| 能力 | 状态 |
| --- | --- |
| 嘴随语音音量开合 | ✅ 实测（真实浏览器 + 真实音频） |
| 音素级精确对应 | ❌ 仍不具备 |

> 音量包络是**近似**。发音时长/音素种类不可得，
> 故不能说"音素同步口型"。PPT 里应写
> "口型随讲解语音动态变化"，而非"音素级同步"。

### 通读 Fay 的结论：可复用与不可复用

**可复用（已用）**：
- `/transparent-pass` 推送通道
- 10002 WebSocket（含握手协议）
- `Action` 规则表（18 behavior + 9 affect）
- 内置 edge_tts 回落

**发现但判定不适用**：
- `__analyze_sentiment_by_keywords`（关键词情感分析）——
  关键词表含`'不'`、`'是'` 这类词，在**化学讲解**里几乎每句都含
  （"是"、"不是"），会大量误判。**且我们已经在用 Fay 推来的
  `Sentiment`**，不必重造。
- `yueshen_rag`（自带 RAG + 切块）——与我们已有的检索重叠，
  其切块策略可对照但暂不引入。

**重要发现**：
- Fay 在 5000 端口提供 **OpenAI 兼容的 `/v1/chat/completions`**
  与 `/v1/models`（实测可用，列出全部模型名）。
  这意味着它可作为我们后端的 LLM 备选。
- 但 `/v1/embeddings` 实测 **404**——它是**转发上游**，
  而 MaaS 无该端点。故 embeddings 仍缺一个支持的服务。
  → **这是"记忆/仿生"模块不可用的根因**，待解决。


### 排查记录：MaaS 突然不可达（外部故障，非代码问题）

通读 Fay 期间发现**后端问答突然 503**，逐层排查：

| 层 | 现象 |
| --- | --- |
| 后端 `/api/v1/ask` | **503** `llm_upstream_unavailable` |
| 后端容器 → MaaS | `SSL: UNEXPECTED_EOF_WHILE_READING`（urllib 与 requests 均失败） |
| Fay 容器 → MaaS | **同样失败**（故排除代码差异） |
| 宿主 → MaaS（直连） | **HTTP 000**（连接失败） |
| 宿主 → MaaS（走代理） | **HTTP 000** |

**结论：MaaS 服务端当前不可达**，与本项目代码无关。

> 排查要领：先**比较两个容器的行为**。若两者都失败，
> 就不该在自己的代码里找原因——那是省时间的关键一步。

**一个易误判的点**：`POST /v1/chat/completions {"model":"fay"}`
此时仍返回 **HTTP 200**，内容是"抱歉，我的大脑暂时开小差了"——
那是Fay 的**人设化兜底话术**，`model=fay` 走的是它自己的对话流程，
LLM 失败时不会报错而是说这句。
**看到 200 不等于 LLM 通了**，须看 `usage` 与内容是否正常。


### 用户指出"画面明显崩了"——目测坐标定位的教训

用户看截图后直接指出问题：**黑块盖在眼睛上，不是嘴**。

### 根因：我用目测定的百分比

原代码写的是 `top: 46%`，注释里还写着「形象图里的嘴约在**下方 46%** 处」——
**那是我目测的，从来没量过**。

而 1024px 的图上，5 个百分点的偏差（46% vs 实际 51%）
正好把黑块从嘴移到眼睛。**偏一点没事，偏 5% 就盖错器官。**

### 正确的定位方法（可复用）

1. **裁头部放大 2 倍** → 肉眼确认嘴在哪
2. **按颜色精确定位** → 嘴腔是暗红/粉红，在 `speaking.png` 的
   `y485-560, x459-547`，中心 `(503, 522)`
3. 换算比例：`x = 503/1024 = 0.491`、`y = 522/1024 = 0.510`
4. **再裁一次那 120×80 的框放大 6 倍** → 确认框住的确实是嘴

> 第4 步不可省：我第一次算出的框偏了，
> 是靠「再裁一次放大看」才发现嘴在框的右下角而非中心。
> **算出坐标 ≠ 坐标对，必须回看。**

### 第二个发现：三张图各自都画了嘴

| 图 | 嘴区深色像素 |
| --- | --- |
| idle | 538 |
| thinking | 532 |
| **speaking** | **1306** |

`speaking.png` **自带一张大张嘴**。原先说话时用它作底，
再叠加动态黑块——于是"闭合"那档看着仍半张着，**像没闭上**。

改为**说话时用 `idle.png` 作底**（嘴最小），叠加才干净。
思考态仍用 `thinking.png`（它的表情本身有信息量，不该被覆盖）。

### 教训

> **凡是「把某个元素贴到图的某处」，都必须量坐标，不能目测。**
>
> 目测在1024px 图上偏 5% 就足以盖错器官——
> 而这类错误**不会报错**：元素照样渲染、测试全绿、
> 布局"看起来正常"，只有人眼看得出不对。

> **算出坐标 ≠ 坐标对。** 算完必须裁图放大回看一遍。
> 我第一次算的框就偏了，靠回看才发现。


---

## H25：数字人「化学老师」人设

### 为什么不在 Fay 那边改

实测Fay 的人设（`llm/nlp_cognitive_stream.py:1805`）是**陪伴型**：

```text
请始终以符合以上人设的身份和语气与用户交流，以下是一些合适话题的示例：
- 过往经历：引导用户选一段开心/痛苦/高光/绝望的经历
- 恋爱旅程：把我们当作在谈一场恋爱
- 虚拟旅游：充当贴身导游
```

那是**陪聊**人设，与化学课堂完全冲突。且它属于 Fay 内部
`genagents` 仿生记忆链的一部分，改它会牵动记忆与情感模块。

**我们不去改第三方内部行为**（GPL-3.0，且本就不该改）——
改为在自己的提示词层做角色化。

### 分层：功能契约 vs 人设

`BASE_SYSTEM_PROMPT` 里的四条（区分检索事实与推断、不编造答案等）
是**正确性要求**，无论讲什么都必须成立。
人设是**表达方式**。两者正交，故分层：

```python
compose_system_prompt(persona)
  → f"{BASE_SYSTEM_PROMPT}\n\n{persona}"   # 契约在前，权重更高
```

**为什么必须拼接而不是二选一**：
`system_prompt` 参数是**覆盖**语义，传了它就丢掉契约。
这正是我最初差点写错的地方。

**契约放前**：提示词靠前权重更高，契约是硬要求应占先。

### 顺带修掉一个严重静默失效

实测发现：`invoke_with_tools` 把messages 原样传给模型，
而 `AgentLoop` 构造的列表里**只有 user/assistant/tool，没有system**。

**即`BASE_SYSTEM_PROMPT` 里「不要编造答案」等四条契约，
在真实问答链路上从未生效过。**

这类缺陷不报错：调用成功、200、测试全绿，
只是"要求写在常量里但模型从没见过"。
已加 `LLMClient._with_system` 修掉。

### 人设内容刻意不含化学知识

人设里**不写**「乙醇的官能团是羟基」这类内容——
那些应由知识库检索 + RDKit 校验提供（`architecture.md` §6）。
写进提示词会造成「模型自带知识绕过检索」，破坏可溯源性。

人设只回答"用什么口吻讲"。有测试锁住这一点
（`test_不含化学知识`）。

### 一个连带修的测试问题

给 `invoke_with_tools` 加 `persona` 参数后，
6 个既有测试报`TypeError: unexpected keyword argument 'persona'`。

**修法是改替身，不是改生产代码**——
为了迁就旧替身而从生产代码里删参数是本末倒置。

过程中还发现替身 `_ScriptedClient` 缺 `supports_streaming` 属性，
而 `AgentLoop.stream` 用
`getattr(self._client, "supports_streaming", True)` 判断——
**缺该属性时默认 True** → 会去调不存在的 `stream_with_tools`。
已显式置 `False`。

> 教训：替身与生产代码的接口约定**要显式声明**，
> 靠 `getattr` 的默认值兜底会掩盖问题。

**测试：745+18 = 763 passed（后端）**


---

## H26：模型可用性实测——「11 个模型」是虚数

### 用户提问促使核实

用户问「这些模型使用有限制吗，是对应的 api 吗」——
**问得对**。我此前说「11 个模型现成可用」是**只看了 `/v1/models`
的返回就下的结论**，没实际逐个调用。

### 实测结果：11 个里只有 1 个能用

逐个 POST `/v1/chat/completions`：

```text
deepseek-v4-flash → ModelArts.81004: Invalid request because you do not have access to it
qwen3-32b         → 同上
openpangu-2.0-pro → 同上
glm-5.3→ 同上
kimi-k2.6         → 同上
openpangu-2.0-flash → ✅ 正常返回（答案"羟基"，usage 完整）
```

**根因**：`/v1/models` 返回的是 **ModelArts 服务端的模型目录**，
不是"你的账号可用的模型"。Fay 只是把它透传出来
（`gui/flask_server.py:783`的 `model not in ('fay','fay-streaming')` 分支
直接转发上游）。**列得出来 ≠ 调得动**——
权限在华为云账号侧，我们无法自行开通。

> **教训**：`/v1/models` 列出什么 ≠ 你能用什么。
> 判断可用性必须**实际发一次请求**。
> 这与本项目反复出现的「配置项存在 ≠ 该值有效」同类。

### 故「模型切换下拉框」不可行

原计划做11 个模型的下拉选择。**实测后判定不可行**——
只有盘古可用，做个单选项的下拉框毫无意义。

若要真正实现多模型切换，需先在华为云 ModelArts 控制台
开通对应模型的访问权限。**这是账号侧操作，我无法代办。**

### 顺带修一个我自己引入的真实 bug

给 `ServiceRegistry` 加 `_llm_config` 字段时，我用 `str.replace`
替换 `self._llm_client = None`，但真实代码里那是**类属性声明**
（`_llm_client: Any = None`）——替换没匹配上，**字段没被声明**。

后果：`/health` 的 llm 组件变成 `ready=False`，
`detail` 是 `AttributeError`。

**为何既有测试没抓到**：它们大多直接构造 `AgentLoop`，
不经过 `ServiceRegistry.probe()`。这类"字段没声明"
只在**真实调用路径**上暴露。

已修 + 加 2 项测试（断言 `probe()` 的 detail 不含 AttributeError）。
> 教训：**新增 dataclass 字段后要立刻过一遍真实调用路径**，
> 而不只是跑单测。

### 人设效果实测：暂时无法验证

MaaS 恢复后实测一次，但**检索工具超时**
（`工具执行超时: tool=search_knowledge`），
模型被检索失败打断，没机会按人设口吻作答。

待检索性能问题解决后再做开/关对照。


---

## H27：检索工具超时（实测修复）

### 现象

答案里出现「很抱歉，检索教材知识库的工具（search_knowledge）执行超时」——
**演示时这句话直接毁掉效果**。

### 定位：把两类失败区分开

第一次实测时看到的是 `LLMUpstreamError`（MaaS 不可用），
**不是**检索超时。两者混在一起会误导排查方向。
先看日志再动手：

```text
工具执行超时: tool=search_knowledge← 检索问题
app.llm.errors.LLMUpstreamError          ← 上游问题
```

### 实测数据（容器内直接测，绕过 LLM）

| 次序 | 耗时 | 说明 |
| --- | --- | --- |
| 首次检索 | **12.7 秒** | 访问 HuggingFace 校验权重 + 加载模型 |
| 第二次 | 0.02 秒 | 模型已驻留 |
| 第三次 | 0.02 秒 | — |

**纯冷启动问题**。而工具超时阈值是
`app/agent/tools.py` 的 `timeout: float = 15.0`——
12.7 秒已逼近15 秒，**再慢一点就超时**。

### 修复：后台预热

在 `create_app.lifespan` 里起`daemon` 线程做一次检索：

```python
threading.Thread(target=_warmup_embedding, args=(registry,),
                 name="embedding-warmup", daemon=True).start()
```

**为何不用同步加载**：lifespan 里同步加载会拖慢启动，
且配置缺失时容器反复重启（该函数 docstring 已写明此顾虑）。
后台线程不阻塞启动，失败也只记日志——**预热是优化，不是必需项**。

### 效果实测

| | 修复前 | 修复后 |
| --- | --- | --- |
| 首次问答耗时 | 66 秒 | **12 秒** |
| 答案含「超时」 | ✅ | ❌ |
| 答案含教材引用 | — | ✅ |

修复后实测答案：
>乙醇的官能团是**羟基（—OH）**，具体称为**醇羟基**。
>
> 依据教材原文：
> - "乙醇俗称酒精……分子中含有羟基，属于醇类化合物。"
>   （来源：高中有机化学自编讲义，第三章 / 代表物乙醇）
> - "醇是羟基与饱和碳原子相连的烃的衍生物……"
>   （来源：高中有机化学自编讲义，第三章 / 第二节 醇 酚）

**引用溯源与人设都能正常工作了**——它们此前被检索失败挡在后面。

### 顺带一个排查要点

`docker compose restart` **不会重新加载已导入的 Python 模块**——
预热代码在位却看不到日志时，先用 `up -d --force-recreate`。
同理，`docker exec ... grep` 的路径会被 Git Bash 展平
（`/work/...` → `C:/Users/.../PortableGit/...`），
须用 `MSYS_NO_PATHCONV=1` 或改用 `python -c` 读文件。
