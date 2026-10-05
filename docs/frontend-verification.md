# 前端实现与实测记录

> 模块：`frontend-web` ｜ 分支：`feat/frontend-web-vue-app`
> 基线 commit：`ad24fa5`
> 实测日期：2026-10-04

本文件记录前端模块的实现依据、**实测得到的关键事实**，
以及为使其在本机完整运行所做的设计取舍。

---

## 1. 技术选型：每一项都联网核实 + 在本机验证

**没有一条是凭训练记忆写的。** 版本号取自 `npm view` 实测，
能力判断取自官方文档与官方发布说明。

| 依赖 | 版本 | 为什么是它 |
| --- | --- | --- |
| vue | **3.5.43** | 3.6 仍是 RC（`3.6.0-rc.10`），Vapor Mode 未稳。3.5 是当前稳定线|
| vite | **8.3.2** | 2026-03 发布（Rolldown 引擎）。要求 Node `^20.19.0 \|\| >=22.12.0` |
| @vitejs/plugin-vue | 6.0.9 | peer 声明 `vite ^5 \|\| ^6 \|\| ^7 \|\| ^8`，与Vite 8 匹配 |
| typescript | **6.0.3** | 见§2 的关键决策 |
| vue-tsc | 3.3.12 | peer 为 `typescript >=5.0.0` |

| pinia | 4.0.3 | Vuex 已进维护模式|
| vitest | 5.0.3 | peer 声明 `vite ^6.4 \|\| ^7 \|\| ^8` |
| happy-dom | 20.14.5 | 比 jsdom 体积小约 8 倍 |

**本机 Node 22.22.2**满足 Vite 8 的 `>=22.12.0` 要求（实测确认）。

### 为什么 npm 装的是 npmmirror

本机 `npm config get registry` 为 `https://registry.npmmirror.com/`。
所有版本号均从该registry 实测，**未从二手文档抄**——
两个版本的偏差是实测发现的（见 §6）。

---

## 2. 关键决策：TypeScript 用6.0.3 而非最新的 7.0.2

`npm view typescript version` 返回 **7.0.2**（Go 重写的原生编译器，
微软宣称完整构建快 8–12 倍）。**但本项目不能用它。**

### 实测与查证结论

| 工具 | TS 7 是否可用 | 原因 |
| --- | --- | --- |
| `tsc` 命令行 | ✅ | 只调用命令行接口 |
| `vue-tsc` | ❌ | **以库的方式嵌入编译器** |
| `typescript-eslint` | ❌ | 同上（peer 范围写死 `<6.1.0`） |

**分界线是**：工具是「shell out 调 tsc」还是「import 编译器的 API」。
Vue SFC 的检查需要把 `.vue` 拆成 script / template / 合成渲染函数
再喂给检查器——这要求**稳定的编程式API**，
而 TS 7.0 不提供（官方说明该API 在 7.1 落地）。

**这也印证了「Vite 构建成功 ≠ 类型检查通过」**——
`vite build` 走 esbuild/Rolldown，只转译不检查类型，
它成功不能证明 `vue-tsc` 跑过。故 `package.json` 的 `build` 脚本
强制先跑 `vue-tsc --build --force`。

### 实测踩到的一个弃用

TS 6 **弃用 `baseUrl`**（报错 `TS5101`）。官方给出的迁移是
「把前缀内联进 `paths`」而不是加 `ignoreDeprecations` 掩盖：

```diff
- "baseUrl": ".",
- "paths": { "@/*": ["./src/*"] }
+ "paths": { "@/*": ["./src/*"] }
```

本项目正是这种情形（`baseUrl` 只用作 `paths` 的前缀），故直接删除。

### 注意 baseUrl 的一个隐蔽副作用

即使项目**没用** `@/` 别名，`baseUrl` 仍会让裸导入悄悄解析
（如 `import x from 'foo'` 命中 `src/foo.ts`）。删掉它顺带
消除了这个debugging 陷阱。

---

## 3. 前端与后端契约的对齐方式

**类型定义不是凭记忆写的**，而是从后端实时导出 OpenAPI 后逐字段抄录：

```bash
PYTHONPATH=backend python -c "
import json
from app.api.app import create_app
spec = create_app().openapi()
print(json.dumps(spec['components']['schemas'], ensure_ascii=False))
"
```

实测确认的关键事实：

| 项 | 值 | 说明 |
| --- | --- | --- |
| 路由 | 5 个 | `/health`、`/ready`、`/api/v1/ask`、`/api/v1/ask/stream`、`/api/v1/molecule` |
| schema | 13 个 | 见 `frontend/src/types/api.ts` |
| `RequestStatus` | `processing` / `completed` / `partial` / `failed` | 枚举，非自由文本 |
| `SourceLocatorKind` | `section` / `page` / `chapter` / `unknown` | 枚举 |
| `question` 长度上限 | 4096 | 前端常量与之对齐 |

### 三个必须如实呈现的「不知道」

**① `review_status` 恒为 `unknown`，前端不得渲染成「已认证」。**

后端刻意不写审核结论——那是人的判断，不是系统能断言的事实。
测试 `SourceList.spec.ts` 断言输出**不含**「已认证」三字。

**② `sources: []` 有三种含义，学生需要区分。**

后端在「检索失败」「检索了但没命中」「根本没调用检索」三种情况下
都返回空数组，但对学生意味着完全不同的结果：

- 检索失败 → 答复可能不完整
- 没命中 → 模型凭自身知识答的，**未经讲义核实**
- 未检索 → 同上，但更明确

组件据 `tool_invocations` 是否有 `search_knowledge` 及其 `ok` 区分。
**若一律显示「无来源」，学生会以为所有答复都同样不可靠。**

**③ `verification` 字段告诉用户「能信到什么程度」。**

后端如实返回它达到的最深层级（`syntax` / `validity` / `properties` / `groups`），
前端翻译成「仅通过语法检查，化学上可能有效」这类提示——
**比直接给个结果更诚实**。

---

## 4. 未引入 vue-router

只有两个互斥视图，用路由反而要处理「刷新后落在哪个视图」这类无意义状态。
**决策登记表保持该项待决策**——真有多页面需求时再引入。

本项目遵循「不装不用的东西」：依赖清单里**没有** `vue-router`。
（最初写入过，随后移除——留着不用它会让 `npm audit` 报出与项目无关的漏洞。）

---

## 5. SSE 客户端：三处实测才发现的行为

这是前端最容易写错的部分。**所有结论都用 `repr()` 打印过真实字节**。

### ① 中文被转义为 `\uXXXX`

实测收到的字节：

```
'data: {"text": "\\u82ef\\u915a"}'
```

后端 FastAPI 的 `ServerSentEvent` 用 `json.dumps` 默认
`ensure_ascii=True` 编码 `data` 字段。

**后果**：直接显示 `event.data` 会看到一串转义码。
必须 `JSON.parse` 才能还原。测试用例
`parseSseChunk > 还原被转义的中文` 锁定此行为。

### ② 事件块以空行分隔，且可能跨 chunk

一个 chunk 可能只含半个事件块，故解析器**必须自己缓冲**。
用「连接关闭」作为事件边界是错的。

### ③ `event:` 与 `data:` 的顺序不保证

多数实现先写 `event:`，但 SSE 规范**未强制**。
解析器必须容忍任意顺序——否则换服务端就崩。
测试用例 `容忍 event 与 data 的顺序颠倒` 锁定。

### ④ 未知事件必须静默忽略

后端将来加事件（如 `progress`）不应弄坏旧前端。
`dispatch` 的 `default` 分支只忽略不报错。

### ⑤ 流内错误只能走事件通道

**实测关键行为：后端一旦发出响应头，就无法再改 HTTP 状态码。**
所以即使 Agent 装配失败，流式请求返回的**仍是 200**，
错误只能藏在事件流里。

**故只判 `response.ok` 会漏掉所有业务错误。**
必须同时处理 `error` 事件与「流结束但既无 `done` 也无 `error`」
（后者按「连接意外中断」处理，**不假装成功**——
那会让用户以为答完了）。

### ⑥ 为什么不用 `EventSource`

`EventSource` **只支持 GET，不能带请求体**。
而问题文本必须放 body（`security-privacy.md`：不得放 URL）。
实测后端返回 `Content-Type: text/event-stream; charset=utf-8`，
是标准 SSE 线格式，故用 `fetch` + `ReadableStream` 自行解析完全可行。

---

## 6. 我自己的失误（3 项，均由实测抓出）

### ① 依赖版本是猜的

`@vue/test-utils` 我写了 `2.4.6`、`happy-dom` 写了 `20.0.11`——
实际 latest 分别是 **2.5.1** 与 **20.14.5**。
`npm view` 一查即知。**凭印象写版本号是这类项目最容易犯且最难自查的错误。**

### ② `toSourceItem` 里`unknown` 是死分支

原写法：

```ts
if (/第\d+页/.test(locator)) kind = 'page'
else if (/^第[一二三]+章/.test(locator)) kind = 'chapter'
else if (locator) kind = 'section'   // ← 非空即 section
```

`else if (locator)` 意味着任何非空文本都被标为「小节」，
`unknown` **永远走不到**——而`unknown` 恰恰是唯一诚实的答案
（「见附录」既不是章节也不是页码）。

测试用例 `无法推断时回落 unknown，不猜` 抓出。

改为只认同可识别的模式，其余一律 `unknown`：

```ts
if (/第?\s*[\d一二三四五六七八九十百]+\s*页/.test(t) || /^\s*(p\.|page\s*)/i.test(t)) return 'page'
if (/第\s*[\d一二三四五六七八九十百]+\s*[章篇]/.test(t)) return 'chapter'
if (/(^|\s)[\d.]+\s*节/.test(t) || /第\s*[一二三四五六七八九十\d]+\s*节/.test(t)) return 'section'
return 'unknown'
```

**依据：错标页码比不标更糟**——学生会去找一个不存在的页，
找不到就以为整份讲义不可信。

### ③ 测试的 fixture 自己写错了（两次）

**多行data 用例**：我先写 `data: {"text":"第一"` + `data: "}`，
以为连接后是合法 JSON。实测`JSON.parse('{"text":"第一"\n"}')`
**成功**（JSON 允许字符串内换行），于是我以为解析器错了——
**其实是我拿 node 验证时用的字符串与实际不同**（`'..."第一"' + '\n' + '}'`，
少了个引号）。fixture 改成一个**语义上确实能被 `\n` 连接**的载荷。

**期望值类型错**：修正后的 fixture 载荷是数组 `["行","动"]`，
我却断言为对象 `{0:'行', 1:'动'}`。

**两次都是测试写错而非实现错。**这印证了后端那次的教训：
**验证代码本身也会有 bug，且只能靠实跑发现。**

### 顺带修掉的构建警告

`stores/index.ts` 里用 `await import('../api/client')`，
但同文件顶部已静态导入同一模块——既无收益又触发
`INEFFECTIVE_DYNAMIC_IMPORT` 警告（构建器明确说它不会分包）。改为静态导入。

---

## 7. 网络拓扑：为什么必须走 Vite 代理

### 实测

| 方式 | 结果 |
| --- | --- |
| 前端 → Vite(5178) → 代理 → 后端(8128) | **200正常** |
| 前端 → 后端(8128) 直连（跨源） | **预检405 Method Not Allowed** |

后端 `ServiceSettings.cors_origins` **默认为空元组**（实测），
这是刻意的安全设计（见 `security-privacy.md`）。

**故默认架构是「相对路径 + 开发代理」**：

- 开发：Vite 把 `/api`、`/health`、`/ready` 代理到后端 → 同源，无需 CORS
- 生产：前后端同源部署 → 同样无需 CORS

若要直连（前后端不同源），**必须**同时设后端
`XUEZHI_CORS_ORIGINS=http://127.0.0.1:5173`。
该约束已写进 `frontend/.env.example`。

### 一个环境坑

本机 git 与 pip 的代理 `http://127.0.0.1:7897` 常处于开启状态，
而 **Node 端的请求也会被它劫持**——`npx vite` 继承环境变量后，
它去连 `127.0.0.1:8128` 也会绕道Clash，导致 502。

起前端**必须**清掉：

```bash
unset https_proxy http_proxy HTTPS_PROXY HTTP_PROXY
export no_proxy="127.0.0.1,localhost" NO_PROXY="127.0.0.1,localhost"
```

（`npm install` 则**需要**代理——本机直连 PyPI/npm 极慢。）

---

## 8. 降级与诚实呈现

### 后端化学组件不可用时

本机实测：RDKit 的 C++ 扩展被应用控制策略拦截，
`/health` 如实返回 `status: degraded`、`chem: ready=false`。

前端**照实显示**「部分降级」并可展开看哪个组件不可用。
**理由**：否则学生会发现分子解析不能用，却不知道原因。

### 分子式取值的坑

`molecular_formula` 在 **`properties` 层**，
**不在** `structure.viz_data` —— 后端实测踩过这个。

`property()` 取不到时显示 `—` 而非 `0`：
**0 是有效值**（如氢原子数为 0），用 0 占位会让学生误读。

### 官能团名反映I5 修正

乙醇显示「**醇羟基**」而非笼统的「羟基」——
后端已拆分醇羟基与酚羟基（决策项 I5），前端类型与断言与之对齐。
苯酚则显示「**酚羟基**」。E2E 用例分别验证两者。

---

## 9. 测试

### 单元与组件测试（38 项，`npm test`，逐类实测）

| 文件 | 项数 | 覆盖 |
| --- | --- | --- |
| `tests/sse.spec.ts` | 17 | 解析器（跨 chunk、转义还原、顺序颠倒、CRLF、坏 JSON 隔离）+ 来源转换 |
| `tests/askStore.spec.ts` | 13 | 状态机五态、取消保留文本、意外结束按错误处理、历史上限 |
| `tests/SourceList.spec.ts` | 8 | 三种「无来源」的区分、审核状态不伪造、定位标签 |

`askStore` 的13 项是补上本轮自认的缺口——状态机的几条边界
（取消保留文本、意外结束不假装成功）此前只有 E2E 间接覆盖。

### 端到端验证（14 项，`npm run e2e`，真实 Chromium）

用**真实 Chromium**跑完整流程并截图。**这不是单元测试的重复**——
happy-dom 没有真实的 `fetch` / `ReadableStream` / `TextDecoder`，
而 SSE 解析、逐 token 追加、代理转发这三件事只能在真浏览器里验证。

**实测 14/14 通过**（本机 RDKit 被拦截的环境下）。覆盖：首屏 →
健康徽章 → 化学降级提示与按钮禁用 → 流式问答（**中途态 + 完成态**）
→ 来源区（标题/审核状态/定位推断）→ 字符计数 → 明暗主题 →
控制台无错误。

关键断言是**「逐token 追加（中途短于最终）」**：
第一次跑时我只断言「中途有文本」，结果 `03-streaming.png` 与
`04-answer-done.png` **字节完全相同**——等待时间过长，
拿到的是完成态，那个结果与同步返回无区别，测不出流式是否在工作。
改为断言中途 7 字< 最终 37 字，才真正验证了流式。

截图在 `frontend/e2e/shots/`（已入库，作为验证证据）。

---

## 10. 复现命令

```bash
# 1. 起后端（仓库根，无密钥也能起）
./scripts/run-local.sh --port 8000

# 2. 起前端（frontend/，注意清代理）
cd frontend
npm install
unset https_proxy http_proxy HTTPS_PROXY HTTP_PROXY
export no_proxy="127.0.0.1,localhost" NO_PROXY="127.0.0.1,localhost"
npm run dev            # http://127.0.0.1:5173

# 3. 自检（类型 + 构建 + 测试）
npm run selfcheck

# 4. 端到端验证（需另两个终端起着服务）
npx playwright install chromium   # 首次
node e2e/verify.mjs
```

### 构建产物（实测）

```
dist/index.html                  1.09 kB │ gzip:  0.78 kB
dist/assets/index-*.css         12.70 kB │ gzip:  2.72 kB
dist/assets/index-*.js          91.23 kB │ gzip: 35.68 kB
```

未做代码分割：单页应用只有一个入口且总量< 100 KB，
拆 chunk 只会增加请求数。**若将来加入 3D 可视化（`frontend-viz`
用了 Three.js / Molstar），必须重新评估** —— 那些包体积在MB 级。

---

## 11. 未验证与已知缺口

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 真实模型下的流式 | **未验证** | 本轮 E2E 用替身模拟事件序列。真实 openPangu 的首 token 延迟与分片方式仍需 MaaS 密钥实测 |
| 移动端布局 | 未测 | 只在 1280×900 验证。窄屏未做专门适配 |
| 屏幕阅读器 | 未测 | 有 `aria-live` / `role` 标注但未实际验证播报 |
| 3D 可视化 | 未实现 | 属`frontend-viz` 模块（`frontend/src/viz/`） |
| 错误码本地化 | 未做 | 直接显示后端返回的中文文案（后端已脱敏） |
| 单元测试对 store 的覆盖 | 不足 | 目前只测了纯函数与组件，未测 store 的状态机转换 |

**最后一项是本轮最该补的**——`phase` 状态机有
`idle → streaming → done/error/cancelled` 五态与若干边界
（如「流结束但既无 done 也无 error」），
这些转换目前只有 E2E 间接覆盖。

---

## 语音播放（frontend-web，2026-10-05）

### 实现范围

| 文件 | 职责 |
| --- | --- |
| `src/composables/useSpeechPlayback.ts` | 合成 → 取音频 → 播放 → 资源释放 |
| `src/components/SpeechButton.vue` | 按钮与四态提示 |
| `src/types/api.ts` | `SpeechStage` / `SpeechResponse` |
| `src/api/client.ts` | `speak()` |

### 关键设计：先 fetch 校验 Content-Type，再给`<audio>`

后端音频过期时返回的是 **JSON 错误体**（404 + `speech_audio_gone`），
不是音频。若把 `audio_url` 直接塞进 `<audio src>`，浏览器会报
`MEDIA_ELEMENT_ERROR: Format error` —— 学生看到的是技术文案，
而不是"语音已失效，请重新生成"。

故流程是：合成 → fetch 验`Content-Type` → 才是 Blob → 才播。
非`audio/*` 一律明确拒绝并给出可读说明。

### 关键设计：Blob URL 必须 revoke

`URL.createObjectURL` 创建的 URL **不会被浏览器自动回收**。
连续合成 10 次就有 10 份音频驻留内存。换新音频前先 revoke 旧的，
组件卸载时也 revoke。已加测试锁住（断言第二次合成时第一个 URL 已被 revoke）。

### 四态的界面处理

| stage | 界面 | 理由 |
| --- | --- | --- |
| `disabled` | 按钮隐藏 | 用户主动关的，显示按钮等于"可以点但没反应" |
| `not_configured` | warning 色提示 | 缺配置可修，值得说一声 |
| `unavailable` | danger 色 + 保留重试 | 服务故障，重试可能成功 |
| `ready` | 正常播放 | — |

统一显示"播放失败"是**误导**：学生无法据此判断该重试、找老师，
还是接受现实。

### 两个实测抓出的缺陷

#### ① `instanceof DOMException` 判不出来

自动播放被拒时浏览器抛 `DOMException{name:"NotAllowedError"}`（MDN 确认）。
我最初用 `err instanceof DOMException` 判断，**测试里始终为 false**——
happy-dom 提供了**自己的** DOMException，与全局的不是同一个构造函数。

改为读 `err.name`（MDN 官方示例也是这么做的）。
> **`instanceof` 在跨 realm 时不可靠**——测试环境、iframe、
> worker 里都可能拿到不同的构造函数。

#### ② `onBeforeUnmount` 在无组件上下文时报警告

单测直接调 composable 时会打 Vue 警告。加`getCurrentInstance()` 守卫：
组件外调用时静默跳过——单测正是要这么用。

### 顺手清掉的冗余

`SpeechButton.vue` 里有个 `probed` 变量：**只写不读**
（原本想在挂载时探一次 stage，但那样会真调一次 TTS 外网请求，
不划算，于是逻辑删了变量没删）。已移除，同时删掉空的 `onMounted`。

### 验证

- 前端测试 **73 passed**（新增 10 项语音测试）
- `npm run typecheck` 通过
- 生产构建通过，3D 块仍为懒加载（542KB 独立 chunk）
- **经前端代理端到端实测**：合成 → 23760 字节 MP3 → 播放链路通

### 测试里替身踩的坑

`vi.stubGlobal()` 返回值**没有** `mockRestore` 方法
（实测报 `audioStub.mockRestore is not a function`）。
改用 `beforeAll(() => vi.stubGlobal(...))` +
`afterAll(() => vi.unstubAllGlobals())`。


---

## H29：前端走查——两个**静默失效**的样式缺陷

### 为什么要走查

人设实测（H28）证明「代码就绪」不等于「效果成立」。既然选B
（先打磨已验证可用的功能），那就得知道**现在到底有什么问题**——
不能凭印象列清单。

选择的做法是**用真实浏览器读浏览器的答案**：
playwright已在 `frontend/node_modules` 里（不装新的），
`getComputedStyle` / `getBoundingClientRect` 都是浏览器自己的数据。

### 缺陷一：CSS 嵌套错误，10 条排版规则全部失效

`AskView.vue` 的 `.answer__body {` 开了块但没在正确位置闭合，
后面 10 条 `:deep()` 规则全被**吞成嵌套规则**：

```css
.answer__body {
  color: var(--text);
  .answer__body :deep(p) {      /* ←嵌进去了 */
```

浏览器把嵌套选择器解析成 `.answer__body .answer__body p`，
需要父元素里再套一层自己——**永远匹配不到**。

**证据在编译产物里**（不是推测）：

```text
.answer__body {
&[data-v-d969868d] {         ← 本该是并列规则，却成了嵌套
  font-size: 0.9375rem;
```

修复后 13 条规则全部编译成独立顶层规则，零残留 `&`。

**为什么测试全绿**：这类缺陷属于「元素在、只是没样式」，
功能测试测不到；`:deep()` 编译成功也不报错。

### 缺陷二：`white-space: pre-wrap` 让列表凭空多一整行

修复嵌套后截图上仍能看到列表项之间有异常空隙。

**我先猜错了两次**：

1. 先怀疑 `marked.setOptions({ breaks: true })` 把换行变成 `<br>`。
   **实测推翻**：`li` 内 `<br>` 数 = 0、换行符数 = 0，两种设置完全一样。
2. 再怀疑是 `li` 内的文本节点带 `\n`。**也推翻**：
   `childNodes` 只有 `STRONG` + 纯文本，无换行。

真凶用**单变量对照**测出来——同一份 HTML，只改这一个属性：

| white-space | 相邻 `li` 间隙 | 空行数 |
| --- | --- | --- |
| `pre-wrap` | 24px | **1.0 行** |
| `normal` | 0px | 0 行 |

原因：marked 会在块级标签之间输出源码换行
（`<ol>\n<li>…</li>\n<li>…`），`pre-wrap` 把它们**保留成真实行盒**。
模型输出里的换行本就该由 Markdown 结构表达，保留源码缩进只会制造噪声。

修复前后端到端对比（真提问、真测量）：

| | 相邻列表项间隙 |
| --- | --- |
| 修复前 | 1.22 / 1.22 空行 |
| 修复后 | 0.28 / 0.11 / 0.11 / 0.11 / 0.11 |

### 验证方法本身的两次失误（都记下来）

**① 探针验证法无效。** 我手工往页面里插 `.answer__body` 探针元素，
读出 `whiteSpace: normal`，一度以为修复没生效。实际上探针**不在组件模板里**，
没有 scoped 属性标记，规则当然不匹配。真实渲染的答案是对的
（`pre-wrap` + 全部后代规则都生效）。**教训：注入探针验证 scoped 样式，
必须带上真实的 `data-v-xxx` 属性，否则测的是不存在的东西。**

**② 跨运行的回答不能互相印证。** 前一次脚本量到「高 27px 但文本 4 行」
这种自相矛盾的数据，我据此判成「有溢出」——其实是因为**两次调用模型
返回了不同结构**（一次有序列表、一次无序列表）。
**教训：同一次运行内测量+ 截图；跨运行只能对比稳定的不变量。**

### 检查器自己也踩了两次坑（都写了反向验证）

写完断言后跑，**失败 5 条**——但那些全是误报：

1. **没区分 at-rule 与普通规则**。`@media (max-width:560px)` 里的
   `.teacher {` 是**合法的**，第一版检查器把它报成缺陷。
   **误报会让测试失去意义**——人只会习惯性忽略它。
2. **判定顺序错了**。`.foo {` 这行自身开括号、闭合在后续行；
   若「先判定后计数」，则处理完这行 depth 才+1，
   下一个选择器行就被误判成嵌在 `.foo` 里。改用栈，且**先判定再入栈**。

最终检查器（栈式）双向验证：

| 输入 | 期望 | 实测 |
| --- | --- | --- |
| 合法 `@media` + 多行规则 | 不报| 不报 ✓ |
| 真实嵌套（选择器在未闭合块内） | 报 | 报 ✓ |
| 合成嵌套样本（单元级） | 报 | 报 ✓ |

**注入真缺陷的验证**（确保不是恒真断言）：

| 注入 | 失败的断言 | 精准度 |
| --- | --- | --- |
| 加回 `white-space: pre-wrap` | 仅「不用 pre-wrap」 | ✓ |
| 选择器嵌进未闭合块 | 「括号平衡」+「不嵌套」 | ✓ |

**测试：前端 146 passed**（原 111 + 新增 35）。

### 附带发现（未修，已登记）

某次回答里模型输出了 **LaTeX 原文**：
`\text{p}K_a \approx 10`、`\mathrm{p}K_a`、`\text{CO}_2`——
学生看到的是公式源码而不是公式。

这**不是样式问题**而是渲染层缺能力：marked 默认不解析 LaTeX。
修法有两条（引 KaTeX / 后处理正则转上下标），
都需要实测才知道哪种对化学式更合适，故**未凭推测实施**。
已登记为待办，见下一节。

### 遗留待办

| 项 | 状态 |
| --- | --- |
| LaTeX 公式渲染（`\text{CO}_2` 等） | 待决策，**未实施** |
| 其余组件（DigitalHuman / MoleculeView 等）的同类样式缺陷 | 未走查 |
