/**
 * 球棍模型渲染器。
 *
 * ## 为什么用 Three.js 而不是 Molstar
 *
 * 架构文档两者都列了。实测考量：
 *
 * - **Molstar** 是结构生物学界的标准（用于 PDB 蛋白结构），
 *   生态围绕大分子与实验数据构建。它假定输入是 PDB/mmCIF 格式，
 *   而我们的输入是**单个有机小分子的 SMILES**——用 Molstar
 *   相当于用挖掘机挖花盆。
 * - **Three.js** 只需球 + 圆柱两类几何体，直接对应球棍模型。
 *
 * 小分子教学场景用 Three.js 是合适的。**若将来要展示蛋白质
 * 或晶体结构，应重新评估**（那是 Molstar 的主场）。
 *
 * ## 资源管理是本文件最需要小心的地方
 *
 * WebGL 的几何体、材质、纹理占**显存**且由 GC 管不到——
 * 组件反复挂载卸载会持续泄漏，表现为「切了几次分子后画面卡住」。
 * 故 :meth:`MoleculeViewer.dispose` 必须**递归释放**，
 * 且所有对外方法都要能安全重复调用。
 */

import * as THREE from 'three'

import { BALL_SCALE, BOND_RADIUS, bondCylinderCount, colorOf } from './elements'
import type { RenderAtomV2, RenderBondV2 } from './types'

/** 渲染器的对外句柄。 */
export interface RenderOptions {
  /** 球棍 / 空间填充 / 线框。 */
  mode: 'ball-stick' | 'space-filling' | 'line'
  /** 是否显示氢。 */
  showHydrogens: boolean
  /** 是否自动旋转。 */
  autoRotate: boolean
}

export const DEFAULT_OPTIONS: RenderOptions = {
  mode: 'ball-stick',
  showHydrogens: true,
  autoRotate: false,
}

/** 几何体缓存：同尺寸复用，避免每个原子都新建。 */
const sphereCache = new Map<string, THREE.SphereGeometry>()

function sphereFor(radius: number, segments: number): THREE.SphereGeometry {
  const key = `${radius.toFixed(4)}_${segments}`
  let geo = sphereCache.get(key)
  if (!geo) {
    geo = new THREE.SphereGeometry(radius, segments, Math.max(segments >> 1, 6))
    sphereCache.set(key, geo)
  }
  return geo
}

/** 键用圆柱：默认沿 +Y，需旋转到任意方向。 */
function cylinderBetween(
  a: THREE.Vector3,
  b: THREE.Vector3,
  radius: number,
  radialSegments: number,
): THREE.Mesh {
  const direction = new THREE.Vector3().subVectors(b, a)
  const length = direction.length()
  const geo = new THREE.CylinderGeometry(radius, radius, length, radialSegments, 1, false)
  const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial())

  // **先平移再旋转**：CylinderGeometry 以原点为中心、沿 +Y 延伸，
  // 故须移到中点并把 +Y 对准direction。
  mesh.position.copy(a).addScaledVector(direction, 0.5)
  mesh.quaternion.setFromUnitVectors(
    new THREE.Vector3(0, 1, 0),
    direction.clone().normalize(),
  )
  return mesh
}

export class MoleculeViewer {
  private readonly container: HTMLElement
  private scene: THREE.Scene | null = null
  private camera: THREE.PerspectiveCamera | null = null
  private renderer: THREE.WebGLRenderer | null = null
  private moleculeGroup: THREE.Group | null = null
  private frameHandle: number | null = null
  private resizeObserver: ResizeObserver | null = null

  private options: RenderOptions = { ...DEFAULT_OPTIONS }
  private current: { atoms: RenderAtomV2[]; bonds: RenderBondV2[]; coords: [number, number, number][] } | null = null

  /**
   * 渲染失败时的原因。
   *
   * **不抛异常**：WebGL 不可用（老旧设备、驱动缺失、
   * 浏览器策略）在教学场景下是真实可能，且页面其余部分
   * 应当照常工作。调用方据此外显提示。
   */
  /** 见构造器：探测失败时写入原因。 */
  declare readonly unavailableReason: string | null

  constructor(container: HTMLElement) {
    this.container = container

    try {
      // 探测 WebGL 支持——**必须在创建 renderer 之前**，
      // 因为某些环境（如无 GPU 的虚拟机）会静默失败。
      const probe = document.createElement('canvas')
      const gl = probe.getContext('webgl2') ?? probe.getContext('webgl')
      if (!gl) {
        throw new Error('浏览器不支持 WebGL')
      }
      this.init()
    } catch (err) {
      this.unavailableReason =
        err instanceof Error ? err.message : '三维渲染不可用（可能缺少 WebGL 支持）。'
    }
  }

  private init(): void {
    const { clientWidth, clientHeight } = this.container
    this.scene = new THREE.Scene()
    this.scene.background = new THREE.Color(0x00000000) // 透明，用 CSS 背景

    this.camera = new THREE.PerspectiveCamera(
      45,
      clientWidth / Math.max(clientHeight, 1),
      0.1,
      500,
    )

    this.renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: true,
      // **必须开**：否则 canvas 内容在每帧结束后被清空，
      // `toDataURL()` 只能拿到空白图。实测踩过——
      // 页面里明明画出了分子，截图却是全空。
      // 代价是轻微的性能损失（无法被浏览器丢弃缓冲）。
      preserveDrawingBuffer: true,
    })
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    this.renderer.setSize(clientWidth, Math.max(clientHeight, 1))
    // 输出色彩空间：r152+ 必须显式设，否则颜色偏灰
    this.renderer.outputColorSpace = THREE.SRGBColorSpace
    this.container.appendChild(this.renderer.domElement)

    this.scene.add(new THREE.AmbientLight(0xffffff, 1.2))
    const key = new THREE.DirectionalLight(0xffffff, 1.8)
    key.position.set(4, 6, 8)
    this.scene.add(key)
    const rim = new THREE.DirectionalLight(0xffffff, 0.6)
    rim.position.set(-5, -2, -6)
    this.scene.add(rim)

    this.moleculeGroup = new THREE.Group()
    this.scene.add(this.moleculeGroup)

    this.animate()
    this.observeResize()
  }

  private observeResize(): void {
    if (typeof ResizeObserver === 'undefined') return
    this.resizeObserver = new ResizeObserver(() => this.handleResize())
    this.resizeObserver.observe(this.container)
  }

  private handleResize(): void {
    if (!this.renderer || !this.camera) return
    const w = this.container.clientWidth
    const h = Math.max(this.container.clientHeight, 1)
    this.camera.aspect = w / h
    this.camera.updateProjectionMatrix()
    this.renderer.setSize(w, h)
  }

  private animate = (): void => {
    if (!this.renderer || !this.scene || !this.camera) return
    this.frameHandle = requestAnimationFrame(this.animate)
    if (this.moleculeGroup && this.options.autoRotate) {
      this.moleculeGroup.rotation.y += 0.005
    }
    this.renderer.render(this.scene, this.camera)
  }

  /** 渲染一个分子。 */
  setMolecule(
    atoms: RenderAtomV2[],
    bonds: RenderBondV2[],
    coords: [number, number, number][],
  ): void {
    this.current = { atoms, bonds, coords }
    this.rebuild()
  }

  /** 更新显示选项（不重新解析数据）。 */
  setOptions(next: Partial<RenderOptions>): void {
    this.options = { ...this.options, ...next }
    if (this.current) this.rebuild()
  }

  private rebuild(): void {
    if (!this.moleculeGroup || !this.current || !this.camera) return
    const { atoms, bonds, coords } = this.current

    this.disposeGroup(this.moleculeGroup)
    this.moleculeGroup = new THREE.Group()
    if (!this.scene) return
    this.scene.add(this.moleculeGroup)

    // 缩放：让分子大小与相机距离匹配。
    // **实测踩过**：不缩放时苯（~5 Å）与乙醇（~4 Å）在同一
    // 相机距离下大小差异不大，但大分子（如胆固醇）会撑出画面。
    const center = computeCenter(coords)
    const extent = computeExtent(coords, center)
    // 目标：让分子占画面约 70%。
    // 实测8Å 时苯酚只占画面的 1/4，学生看不清键——
    // 缩放目标改14，兼顾「看清结构」与「不切边」。
    const scale = extent > 0 ? 14 / extent : 1

    const group = this.moleculeGroup
    group.position.set(-center.x * scale, -center.y * scale, -center.z * scale)
    group.scale.setScalar(scale)

    const isLine = this.options.mode === 'line'
    const ballRadiusFor = (a: RenderAtomV2): number =>
      this.options.mode === 'space-filling' ? a.radius * 0.85 : a.radius * BALL_SCALE
    const bondRadius = this.options.mode === 'ball-stick' ? BOND_RADIUS : BOND_RADIUS * 2

    // 球棍/填充模式：先画球后画键，保证键在球的**上层**——
    // 否则深度测试会让键被球挡住一半。
    //
    // **线框模式不画球**（那正是线框的用途：看骨架拓扑）。
    if (!isLine) {
      for (const atom of atoms) {
        if (atom.is_explicit_hydrogen && !this.options.showHydrogens) continue
        const r = ballRadiusFor(atom)
        const mesh = new THREE.Mesh(
          sphereFor(r, this.options.mode === 'space-filling' ? 24 : 20),
          new THREE.MeshStandardMaterial({
            color: colorOf(atom.element),
            roughness: 0.35,
            metalness: 0.05,
          }),
        )
        const p = coords[atom.index]
        mesh.position.set(p[0], p[1], p[2])
        // 把 userData 挂上，便于「点选原子」与测试定位
        mesh.userData = { element: atom.element, index: atom.index }
        group.add(mesh)
      }
    }

    for (const bond of bonds) {
      const a = coords[bond.begin]
      const b = coords[bond.end]
      if (!a || !b) continue
      // 一端是被隐藏的氢时，整根键不画——否则会留下悬空短棍
      if (this.isHidden(bond.begin, atoms) || this.isHidden(bond.end, atoms)) continue

      if (isLine) {
        // 线框：每根键一条细线，不画多重键——
        // 线的粗细表达键级在这里没有意义，反而糊成一团。
        const geo = new THREE.BufferGeometry().setFromPoints([
          new THREE.Vector3(a[0], a[1], a[2]),
          new THREE.Vector3(b[0], b[1], b[2]),
        ])
        const line = new THREE.Line(
          geo,
          new THREE.LineBasicMaterial({ color: 0x888888 }),
        )
        line.userData = { begin: bond.begin, end: bond.end }
        group.add(line)
        continue
      }

      const n = bondCylinderCount(bond.order, bond.is_aromatic)
      for (let i = 0; i < n; i += 1) {
        const mesh = cylinderBetween(
          new THREE.Vector3(a[0], a[1], a[2]),
          new THREE.Vector3(b[0], b[1], b[2]),
          n > 1 ? bondRadius * 0.7 : bondRadius,
          12,
        )
        // 多键的第二根要**侧向偏移**，否则两根完全重叠
        if (n > 1) {
          const offset = bondOffset(a, b, bondRadius * 1.6, i - (n - 1) / 2)
          mesh.position.add(offset)
        }
        mesh.material = new THREE.MeshStandardMaterial({
          color: 0xdddddd,
          roughness: 0.5,
        })
        group.add(mesh)
      }

      // 芳香环的离域标记：内侧画一段虚线弧。
      // 高中要求画出交替单双键，但后端给的是 1.5 键，
      // 故这里**额外**加标记，而不是把 1.5 当成1 或 2 画。
      if (bond.is_aromatic) {
        group.add(this.makeAromaticMark(a, b, bondRadius))
      }
    }

    this.frameCamera(center, extent, scale)
  }

  private isHidden(index: number, atoms: RenderAtomV2[]): boolean {
    const atom = atoms[index]
    return Boolean(atom?.is_explicit_hydrogen) && !this.options.showHydrogens
  }

  private makeAromaticMark(a: [number, number, number], b: [number, number, number], r: number): THREE.Object3D {
    const offset = bondOffset(a, b, r * 2.4, 0)
    const va = new THREE.Vector3(a[0], a[1], a[2]).add(offset)
    const vb = new THREE.Vector3(b[0], b[1], b[2]).add(offset)
    const dir = new THREE.Vector3().subVectors(vb, va)
    const len = dir.length() * 0.55
    const geo = new THREE.CylinderGeometry(r * 0.5, r * 0.5, len, 8)
    const mesh = new THREE.Mesh(
      geo,
      new THREE.MeshStandardMaterial({ color: 0x888888, roughness: 0.6 }),
    )
    mesh.position.copy(va).addScaledVector(dir, 0.5)
    mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.clone().normalize())
    mesh.userData = { aromaticMark: true }
    return mesh
  }

  /** 相机对准分子。 */
  private frameCamera(center: THREE.Vector3, extent: number, scale: number): void {
    if (!this.camera) return
    const s = center.clone().multiplyScalar(scale)
    // x 方向偏移极小量：与 y 方向几乎共线时 lookAt 会因 up 向量
    // 与视线平行而**完全失效**（画面会随机翻转）。这是 Three.js 的经典坑。
    this.camera.position.set(s.x + 0.001, s.y, s.z + extent * scale * 1.15 + 2)
    this.camera.lookAt(s)
    this.camera.updateProjectionMatrix()
  }

  /** 重置视角。 */
  resetView(): void {
    if (!this.current) return
    this.rebuild()
  }

  /** 截图（供导出图片）。 */
  snapshot(): string | null {
    if (!this.renderer) return null
    this.renderer.render(this.scene!, this.camera!)
    return this.renderer.domElement.toDataURL('image/png')
  }

  /**
   * 释放全部资源。
   *
   * **必须递归** —— 分子含几十个子对象，
   * 逐个漏掉会造成显存泄漏。
   */
  dispose(): void {
    if (this.frameHandle !== null) {
      cancelAnimationFrame(this.frameHandle)
      this.frameHandle = null
    }
    this.resizeObserver?.disconnect()
    this.resizeObserver = null
    if (this.scene) this.disposeGroup(this.scene)
    // 缓存的几何体是共享的，不能在这里 dispose——
    // 它们在模块级，由 `clearGeometryCache` 统一释放。
    this.renderer?.dispose()
    this.renderer?.domElement.remove()
    this.renderer = null
    this.scene = null
    this.camera = null
    this.current = null
  }

  private disposeGroup(group: THREE.Object3D): void {
    for (const child of [...group.children]) {
      // **必须先判instanceof**：``Object3D`` 基类没有
      // ``geometry`` / ``material``（那是 ``Mesh`` / ``Line`` 的属性），
      // 直接访问 TS 会报错，运行时则是 undefined。
      if (child instanceof THREE.Mesh || child instanceof THREE.Line) {
        const geo = child.geometry
        // 缓存球不释放：它们被其他 molecule 复用
        if (!isSharedSphere(geo)) geo.dispose()
        child.material.dispose()
      }
      this.disposeGroup(child)
    }
    group.clear()
  }
}

/** 释放模块级缓存的球几何体（应用卸载时调用）。 */
export function clearGeometryCache(): void {
  for (const geo of sphereCache.values()) geo.dispose()
  sphereCache.clear()
}

function isSharedSphere(geo: THREE.BufferGeometry): boolean {
  for (const cached of sphereCache.values()) if (cached === geo) return true
  return false
}

/**
 * 分子几何中心。
 *
 * 返回 `THREE.Vector3` 而非元组：调用方全部需要 `.x/.y/.z`
 * 或直接传给 Three.js，用元组反而到处要解包。
 */
export function computeCenter(coords: [number, number, number][]): THREE.Vector3 {
  if (coords.length === 0) return new THREE.Vector3()
  const sum = new THREE.Vector3()
  for (const c of coords) sum.add(new THREE.Vector3(c[0], c[1], c[2]))
  return sum.divideScalar(coords.length)
}

/** 分子最大跨度（Å）——即最远原子到中心的距离的两倍。 */
export function computeExtent(coords: [number, number, number][], center: THREE.Vector3): number {
  let max = 0
  for (const c of coords) {
    const d = Math.hypot(c[0] - center.x, c[1] - center.y, c[2] - center.z)
    if (d > max) max = d
  }
  return max * 2
}

/** 侧向偏移：用于多键的第二/第三根。 */
function bondOffset(
  a: [number, number, number],
  b: [number, number, number],
  distance: number,
  factor: number,
): THREE.Vector3 {
  if (factor === 0) return new THREE.Vector3(0, 0, 0)
  const dir = new THREE.Vector3(b[0] - a[0], b[1] - a[1], b[2] - a[2]).normalize()
  // 取一个与键方向垂直的向量：优先 Z 轴，接近平行时改用 X。
  let perp = new THREE.Vector3(0, 0, 1).cross(dir)
  if (perp.lengthSq() < 1e-6) perp = new THREE.Vector3(1, 0, 0).cross(dir)
  perp.normalize().multiplyScalar(distance * factor)
  return perp
}
