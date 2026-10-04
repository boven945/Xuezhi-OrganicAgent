/**
 * CPK 元素配色与球棍模型的几何参数。
 *
 * ## 配色来源：Jmol 的 CPKnew 表（权威，非自选）
 *
 * 取自 Jmol 官方配色表（`biomodel.uah.es/Jmol/colors`）的 CPKnew 列。
 * **不用原版 CPK 的纯黑/纯白**：那个配色在网页上几乎不可读——
 * 碳是纯黑 `#000000`，键也是深色，学生根本看不清键连的是谁。
 * Jmol 的C 改成了中灰`#909090`，是社区实践验证过的可读版本。
 *
 * 常见元素（高中范围）：

 * | 元素 | 颜色 | 元素 | 颜色 |
 * | --- | --- | --- | --- |
 * | H 氢 | `#FFFFFF` | O 氧 | `#FF0D0D` |
 * | C 碳 | `#909090` | S 硫 | `#FFFF30` |
 * | N 氮 | `#3050F8` | P 磷 | `#FF8000` |
 * | Cl 氯 | `#1FF01F` | Br 溴 | `#A62929` |
 *
 * 碳用中灰是刻意的：深色键画在中灰球上对比清晰，
 * 而若键与球同色，学生数不出「几个键」。
 */

/** CPK 配色（十六进制字符串，与 Jmol CPKnew 一致）。 */
export const CPK_COLORS: Readonly<Record<string, number>> = {
  H: 0xffffff,
  C: 0x909090,
  N: 0x3050f8,
  O: 0xff0d0d,
  F: 0x90e050,
  Si: 0xf0c8a0,
  P: 0xff8000,
  S: 0xffff30,
  Cl: 0x1ff01f,
  K: 0x8f40d4,
  Ca: 0x3dff00,
  Br: 0xa62929,
  I: 0x940094,
  Na: 0xab5cf2,
  Mg: 0x8aff00,
  Fe: 0xe06633,
  Zn: 0x7d80b0,
}

/** 未登记元素的兜底色。 */
export const FALLBACK_COLOR = 0xff69b4

/**
 * 氢是否显示。
 *
 * 默认**显示**——高中有机化学必须看到 C 上的 H，
 * 否则学生数不出 ``CH₃`` 的四个键。这是教学需求，
 * 不是显示偏好。
 */
export const HYDROGEN_COLOR = 0xffffff

/**
 * 球半径缩放。
 *
 * 共价半径是「原子大小」，直接用会让分子看起来很挤
 * （C 的 0.76 Å 与键长 1.52 Å 相比偏大）。
 * 0.32 是球棍模型常用的比例：球与键视觉上分明，
 * 又不至于遮住键。
 */
export const BALL_SCALE = 0.32

/**
 * 键的半径（Å）。
 *
 * 比球细得多——这是球棍模型（ball-and-stick）与
 * 填充模型（space-filling）的本质区别：
 * 前者要能看穿分子看到里面的键。
 */
export const BOND_RADIUS = 0.09

/**
 * 球棍模型里键的绘制半径。
 *
 * 双键要画两根圆柱，三键三根。单键一根。
 */
export function bondCylinderCount(order: number, isAromatic: boolean): number {
  if (isAromatic) {
    // 苯环的「1.5 键」：**画一根半圆柱**（细）表示离域，
    // 交替单双键由前端另加虚线/实线标记（见 `viz/bonds.ts`）。
    return 1
  }
  const n = Math.round(order)
  return Math.min(Math.max(n, 1), 3)
}

/** 取元素的 CPK 颜色。 */
export function colorOf(element: string): number {
  return CPK_COLORS[element] ?? FALLBACK_COLOR
}

/** 元素中文名（高中范围）。 */
const ELEMENT_NAMES: Readonly<Record<string, string>> = {
  H: '氢',
  C: '碳',
  N: '氮',
  O: '氧',
  F: '氟',
  Si: '硅',
  P: '磷',
  S: '硫',
  Cl: '氯',
  Na: '钠',
  Mg: '镁',
  K: '钾',
  Ca: '钙',
  Fe: '铁',
  Br: '溴',
  I: '碘',
  Zn: '锌',
}

/** 取元素中文名；未登记时返回符号本身。 */
export function elementName(element: string): string {
  return ELEMENT_NAMES[element] ?? element
}
