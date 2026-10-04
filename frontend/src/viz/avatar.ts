/**
 * Fay 动作语义 → 前端形象状态的映射。
 *
 * ## 为什么不直接用 Fay 的 action.code
 *
 * Fay 推送的 `Action.behavior` 是**人物动作**语义
 * （挥手 / 点头 / 邀请 / 思考），它不知道"分子"是什么——
 * `Action` 的粒度到"手怎么动"，不到"手要指哪儿"。
 * 实测契约见 `docs/speech-module-verification.md`：
 *
 * ```json
 * { "Key": "audio",
 *   "Lips": [{"Lip": "sil", "Time": 180}, {"Lip": "FF", "Time": 144}],
 *   "Sentiment": 0.7,
 *   "Action": { "behavior": "invite", "affect": "warm", "intensity": 0.74 } }
 * ```
 *
 * 因此本模块只做**形象状态**的映射（三态：待机/说话/思考），
 * **不做**"手势指向某个原子"——那需要我们自己把讲解文本
 * 里的位点翻译成原子索引，与 Fay 的动作语义是两回事。
 *
 * ## 为什么映射要「宽松」
 *
 * Fay 的 `behavior` 取值远多于我们这三态（社区实测可见
 * `invite` / `wave` / `nod` / `think` / `confirm` / `reject` /
 * `question` 等）。**我们只认能明确对上的一种，其余全落待机**——
 * 认不出的行为若被猜成「说话」，学生看到的是"老师在动嘴但没声音"。
 */

/** 形象状态。刻意只有三态——图片资源就是三张。 */
export type AvatarState = 'idle' | 'speaking' | 'thinking'

/** 各状态对应的图片。放在 `public/avatar/` 下，走静态资源直链。 */
export const AVATAR_IMAGES: Record<AvatarState, string> = {
  idle: '/avatar/idle.png',
  speaking: '/avatar/speaking.png',
  thinking: '/avatar/thinking.png',
}

/**
 * Fay `Action.behavior` → 形象状态。
 *
 * **为何只认三个值**：`nod`（点头）与 `invite`（邀请）都属"讲解中"，
 * 映射成说话态——这在观感上是对的：老师说"对，就是这样"时
 * 嘴确实在动。
 *
 * `think` / `question` 映射成思考态：这两个恰好是 Fay 表格里
 * "让我想想"与"为什么"对应的行为，用思考表情比用说话表情更贴切。
 */
const BEHAVIOR_MAP: Record<string, AvatarState> = {
  // 讲解中——嘴在动
  nod: 'speaking',
  invite: 'speaking',
  wave: 'speaking',
  confirm: 'speaking',
  agree: 'speaking',
  // 思考中
  think: 'thinking',
  question: 'thinking',
  // 认不出 → 待机（由调用方兜底）
}

/**
 * 由 Fay 消息推导形象状态。
 *
 * @param payload 一条 Fay `human` 消息的 `Data` 字段。
 * @param speaking 该消息是否对应正在播放的音频。
 *
 * @returns 形象状态。**无法识别时返回 `'idle'`**，不返回 null——
 * 组件需要的是一个永远可用的值，空状态会让界面出现无图可显示的分支。
 *
 * ## 优先级：音频 > 动作
 *
 * `Lips` 有音素说明**确实在发声**，比 `Action.behavior` 更可靠
 * （动作是语义标签，可能在静音片段里也发）。
 * 故有音素时一律判为 `'speaking'`。
 */
export function resolveAvatarState(
  payload: FayHumanData | null | undefined,
  speaking: boolean,
): AvatarState {
  if (!payload) return 'idle'
  // 有音素 = 确实在出声。这是最强的信号，优先于动作语义。
  if (Array.isArray(payload.Lips) && payload.Lips.length > 0) return 'speaking'
  if (speaking) return 'speaking'
  const behavior = payload.Action?.behavior
  if (typeof behavior !== 'string') return 'idle'
  return BEHAVIOR_MAP[behavior] ?? 'idle'
}

/**
 * Fay 10002 WebSocket 的 `Data` 字段。
 *
 * **只声明我们真正读到的字段**——不把整份协议抄进来。
 * 抄全的后果是：Fay 加字段时我们毫无感知，
 * 而"没读到的字段"本就不该在类型里假装支持。
 */
export interface FayHumanData {
  Key?: string
  Text?: string
  /** 音频下载地址。Fay 自带 HTTP 服务，不经我们的后端。 */
  HttpValue?: string
  /** 音素序列：每项含音素名与持续毫秒数。 */
  Lips?: { Lip: string; Time: number }[]
  /** 情感值，范围约 [-2, 2]。0 为中性。 */
  Sentiment?: number
  /** 动作语义。Fay 只给语义，不给具体动画编号。 */
  Action?: {
    code?: string
    behavior?: string
    affect?: string
    intensity?: number
    priority?: number
  }
  IsFirst?: number
  IsEnd?: number
}

/** 一条完整的 Fay 消息。 */
export interface FayMessage {
  Topic?: string
  Data?: FayHumanData
}

/**
 * 从音素序列算出「这段音频大致在说多久」（毫秒）。
 *
 * **用途**：在没有 `Audio.duration` 的情况下决定何时切回待机。
 * 实测 Fay 的 `Lips[].Time` 之和与实际音频时长接近。
 *
 * @returns 毫秒数。无音素时返回 0。
 */
export function lipsDurationMs(lips: { Time: number }[] | undefined): number {
  if (!Array.isArray(lips)) return 0
  let total = 0
  for (const l of lips) {
    // 负数会算出负的时长，进而让 setTimeout 立即触发 → 状态抖动
    if (Number.isFinite(l.Time) && l.Time > 0) total += l.Time
  }
  return total
}
