/**
 * 反应机理动画播放器（复用 `viewer.ts` 的 Three.js 渲染基元）。
 *
 * ## 与 `MoleculeViewer` 的关系
 *
 * `MoleculeViewer` 面向**单个静态分子**：每次 `setMolecule` 都**重建**整个
 * group（球+键全部 new 一遍）。那在静态场景下没问题，但反应动画要在
 * 24+ 帧之间连续插值——若每帧重建，不仅浪费、还会在帧间重创几何体、
 * 触发 GC 抖动。
 *
 * 故本播放器**只在 `load()` 时建一次**持久网格（原子球按 index、
 * 键圆柱按 (a,b) 各一个），之后每帧只改：
 * - 原子球：`mesh.position`（按帧间线性插值）
 * - 键圆柱：`orientUnitCylinder` 重定位 + `material.opacity`
 *
 * 这正是 "风格化插值" 的落地（决策 D-R3）：后端已给出逐帧坐标，
 * 播放器再在两帧之间补一次线性插值，让运动更顺滑。
 *
 * ## 复用而非重写
 *
 * 场景底色、主题跟随、ResizeObserver、OrbitControls、灯光、
 * 高亮琥珀色/脉冲速度、球缓存——全部从 `viewer.ts` 复用，
 * 保证与单分子视图同一套视觉语言与同一套资源释放纪律。
 */

import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

import {
  BALL_SCALE,
  BOND_RADIUS,
  colorOf,
} from './elements'
import type { MechanismFrame, ReactionMechanismData } from '../types/api'
import {
  HIGHLIGHT_COLOR,
  PULSE_SPEED,
  computeCenter,
  computeExtent,
  isSharedSphere,
  sphereFor,
} from './viewer'

/** 关键元素共价半径（Å），机理帧不携带半径，前端按元素查表。 */
const COVALENT_RADII: Record<string, number> = {
  H: 0.31,
  C: 0.76,
  N: 0.71,
  O: 0.66,
  F: 0.57,
  P: 1.07,
  S: 1.05,
  Cl: 1.02,
  Br: 1.2,
  I: 1.39,
}
const DEFAULT_RADIUS = 0.75

/** 单根键圆柱用「单位长度 + 每帧 scale.y」实现，避免逐帧重建几何体。 */
const UNIT_CYLINDER_HEIGHT = 1

/** 一个原子的持久渲染对象。 */
interface AtomMesh {
  index: number
  mesh: THREE.Mesh
  mat: THREE.MeshStandardMaterial
}

/** 一根键的持久渲染对象（始终单圆柱，靠 opacity 表现断/成）。 */
interface BondMesh {
  key: string
  a: number
  b: number
  mesh: THREE.Mesh
  mat: THREE.MeshStandardMaterial
}

export class ReactionPlayer {
  private readonly container: HTMLElement
  private scene: THREE.Scene | null = null
  private camera: THREE.PerspectiveCamera | null = null
  private renderer: THREE.WebGLRenderer | null = null
  private group: THREE.Group | null = null
  private frameHandle: number | null = null
  private resizeObserver: ResizeObserver | null = null
  private controls: OrbitControls | null = null
  private themeQuery: MediaQueryList | null = null
  private themeListener: (() => void) | null = null

  private atomMeshes = new Map<number, AtomMesh>()
  private bondMeshes = new Map<string, BondMesh>()

  private frames: ReactionMechanismData['frames'] = []
  private fps = 12
  private highlight = new Set<number>()
  private pulsePhase = 0
  private lastFrameAt = 0

  private progress = 0 // 连续帧进度（0 ~ frameCount-1）
  private playing = false
  private speed = 1
  private loop = false

  /** 播放进度回调（归一化 0~1），供 UI 同步滑块。 */
  onTick: ((progress01: number, frameIndex: number) => void) | null = null
  /** 播放到末尾回调。 */
  onEnded: (() => void) | null = null

  declare readonly unavailableReason: string | null

  constructor(container: HTMLElement) {
    this.container = container
    try {
      const probe = document.createElement('canvas')
      const gl = probe.getContext('webgl2') ?? probe.getContext('webgl')
      if (!gl) throw new Error('浏览器不支持 WebGL')
      this.init()
    } catch (err) {
      this.unavailableReason =
        err instanceof Error ? err.message : '三维渲染不可用（可能缺少 WebGL 支持）。'
    }
  }

  private init(): void {
    this.scene = new THREE.Scene()
    this.scene.background = new THREE.Color(this.stageColor())
    this.watchTheme()

    this.camera = new THREE.PerspectiveCamera(
      45,
      this.container.clientWidth / Math.max(this.container.clientHeight, 1),
      0.1,
      500,
    )

    this.renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: true,
      preserveDrawingBuffer: true,
    })
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    this.renderer.setSize(this.container.clientWidth, Math.max(this.container.clientHeight, 1))
    this.renderer.outputColorSpace = THREE.SRGBColorSpace
    this.container.appendChild(this.renderer.domElement)

    this.scene.add(new THREE.AmbientLight(0xffffff, 1.2))
    const key = new THREE.DirectionalLight(0xffffff, 1.8)
    key.position.set(4, 6, 8)
    this.scene.add(key)
    const rim = new THREE.DirectionalLight(0xffffff, 0.6)
    rim.position.set(-5, -2, -6)
    this.scene.add(rim)

    this.group = new THREE.Group()
    this.scene.add(this.group)

    this.controls = new OrbitControls(this.camera, this.renderer.domElement)
    this.controls.enableDamping = true
    this.controls.dampingFactor = 0.08
    this.controls.minDistance = 2
    this.controls.maxDistance = 120

    this.animate()
    this.observeResize()
  }

  private observeResize(): void {
    if (typeof ResizeObserver === 'undefined') return
    this.resizeObserver = new ResizeObserver(() => this.handleResize())
    this.resizeObserver.observe(this.container)
  }

  private stageColor(): number {
    if (typeof window === 'undefined' || !window.matchMedia) return 0xf5f5f4
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 0x292524 : 0xf5f5f4
  }

  private watchTheme(): void {
    if (typeof window === 'undefined' || !window.matchMedia) return
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (): void => {
      if (this.scene) this.scene.background = new THREE.Color(this.stageColor())
    }
    if (typeof mq.addEventListener === 'function') {
      mq.addEventListener('change', onChange)
      this.themeQuery = mq
      this.themeListener = onChange
    }
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
    this.controls?.update()

    const now = performance.now()
    const delta = this.lastFrameAt === 0 ? 0 : (now - this.lastFrameAt) / 1000
    this.lastFrameAt = now
    const dt = Math.min(delta, 0.1)

    if (this.playing && this.frames.length >= 2) {
      const last = this.frames.length - 1
      this.progress += dt * this.fps * this.speed
      if (this.progress >= last) {
        if (this.loop) {
          this.progress = 0
        } else {
          this.progress = last
          this.pause()
          this.onEnded?.()
        }
      }
    }

    if (this.frames.length >= 2) this.applyProgress(this.progress)
    this.advancePulse(dt)
    this.renderer.render(this.scene, this.camera)
  }

  /**
   * 载入机理数据并建立持久网格。
   *
   * 多次调用会先清掉旧网格再建新的（切换反应时）。
   */
  load(data: ReactionMechanismData): void {
    this.frames = data.frames
    this.fps = data.fps
    this.progress = 0
    this.playing = false
    this.highlight = new Set(data.frames[0]?.highlight_atoms ?? [])

    this.clearMeshes()

    // 稳定取景：用**所有帧**原子的并集算中心与跨度，避免播放途中画面跳动。
    const allCoords: [number, number, number][] = []
    for (const f of data.frames) {
      for (const a of f.atoms) allCoords.push([a.x, a.y, a.z])
    }
    const center = computeCenter(allCoords)
    const extent = computeExtent(allCoords, center)
    const scale = extent > 0 ? 14 / extent : 1
    if (this.group) {
      this.group.position.set(-center.x * scale, -center.y * scale, -center.z * scale)
      this.group.scale.setScalar(scale)
    }

    // 原子：每个 index 一个持久球。
    const seenAtoms = new Set<number>()
    for (const a of data.frames[0].atoms) {
      if (seenAtoms.has(a.index)) continue
      seenAtoms.add(a.index)
      this.addAtom(a.index, a.element)
    }
    // 键：每个 (a,b) 一个持久圆柱（单圆柱，靠 opacity 表现断/成）。
    const seenBonds = new Set<string>()
    for (const b of data.frames[0].bonds) {
      const key = bondKey(b.a, b.b)
      if (seenBonds.has(key)) continue
      seenBonds.add(key)
      this.addBond(key, b.a, b.b)
    }

    this.applyProgress(0)
    this.frameCamera(center, extent, scale)
  }

  private addAtom(index: number, element: string): void {
    if (!this.group) return
    const radius = (COVALENT_RADII[element] ?? DEFAULT_RADIUS) * BALL_SCALE
    const mat = new THREE.MeshStandardMaterial({
      color: colorOf(element),
      roughness: 0.35,
      metalness: 0.05,
    })
    const mesh = new THREE.Mesh(sphereFor(radius, 20), mat)
    mesh.userData = { element, index }
    this.group.add(mesh)
    this.atomMeshes.set(index, { index, mesh, mat })
  }

  private addBond(key: string, a: number, b: number): void {
    if (!this.group) return
    const geo = new THREE.CylinderGeometry(BOND_RADIUS, BOND_RADIUS, UNIT_CYLINDER_HEIGHT, 12)
    const mat = new THREE.MeshStandardMaterial({
      color: 0xdddddd,
      roughness: 0.5,
      transparent: true,
      opacity: 1,
    })
    const mesh = new THREE.Mesh(geo, mat)
    // 单位圆柱沿 +Y、高 1；每帧由 orientUnitCylinder 重定位+缩放。
    this.group.add(mesh)
    this.bondMeshes.set(key, { key, a, b, mesh, mat })
  }

  /**
   * 把单位圆柱定位到 a→b：中点平移、+Y 对齐方向、scale.y=长度。
   * 比 `viewer.ts` 的 `cylinderBetween`（烘焙长度）更适合逐帧变形。
   */
  private orientUnitCylinder(mesh: THREE.Mesh, a: THREE.Vector3, b: THREE.Vector3): void {
    const direction = new THREE.Vector3().subVectors(b, a)
    const length = direction.length()
    mesh.position.copy(a).addScaledVector(direction, 0.5)
    if (length > 1e-6) {
      mesh.quaternion.setFromUnitVectors(
        new THREE.Vector3(0, 1, 0),
        direction.clone().normalize(),
      )
    }
    mesh.scale.set(1, Math.max(length, 1e-4), 1)
  }

  /** 按连续进度 p 在两帧间插值，更新所有网格的位置与不透明度。
   *
   * 纯插值数学（坐标/透明度）已抽到模块级 `interpolateAtomPositions` /
   * `interpolateBondOpacities`，与 Three.js 解耦，**可在 happy-dom 下独立单测**
   * （与 `viz.spec.ts` 同口径：几何计算必须可独立测，真实渲染交给 E2E）。
   */
  private applyProgress(p: number): void {
    if (this.frames.length < 2) return
    const last = this.frames.length - 1
    const atomPos = interpolateAtomPositions(this.frames, p)
    const bondOpac = interpolateBondOpacities(this.frames, p)

    for (const { index, mesh } of this.atomMeshes.values()) {
      const pos = atomPos.get(index)
      if (!pos) continue
      mesh.position.set(pos[0], pos[1], pos[2])
    }

    for (const bond of this.bondMeshes.values()) {
      const a = atomPos.get(bond.a)
      const b = atomPos.get(bond.b)
      if (!a || !b) {
        bond.mesh.visible = false
        continue
      }
      this.orientUnitCylinder(
        bond.mesh,
        new THREE.Vector3(a[0], a[1], a[2]),
        new THREE.Vector3(b[0], b[1], b[2]),
      )
      const o = bondOpac.get(bondKey(bond.a, bond.b)) ?? 0
      bond.mat.opacity = o
      bond.mesh.visible = o > 0.02
    }

    this.onTick?.(last > 0 ? p / last : 0, Math.floor(p))
  }

  private advancePulse(delta: number): void {
    if (this.highlight.size === 0) return
    this.pulsePhase = (this.pulsePhase + delta * PULSE_SPEED) % 1
    const wave = 1 - Math.abs(this.pulsePhase * 2 - 1)
    for (const { index, mat } of this.atomMeshes.values()) {
      if (this.highlight.has(index)) {
        mat.emissive.setHex(HIGHLIGHT_COLOR)
        mat.emissiveIntensity = 0.45 + wave * 0.75
      } else {
        mat.emissive.setHex(0x000000)
        mat.emissiveIntensity = 1
      }
    }
  }

  private frameCamera(center: THREE.Vector3, extent: number, scale: number): void {
    if (!this.camera) return
    const s = center.clone().multiplyScalar(scale)
    this.camera.position.set(s.x + 0.001, s.y, s.z + extent * scale * 1.15 + 2)
    this.camera.lookAt(s)
    this.camera.updateProjectionMatrix()
    this.controls?.target.copy(s)
    this.controls?.update()
  }

  /** 播放。已在末尾则从头开始。 */
  play(): void {
    if (this.frames.length < 2) return
    if (this.progress >= this.frames.length - 1) this.progress = 0
    this.playing = true
  }

  /** 暂停。 */
  pause(): void {
    this.playing = false
  }

  /** 切换播放/暂停。 */
  toggle(): void {
    if (this.playing) this.pause()
    else this.play()
  }

  /** 从头重播。 */
  restart(): void {
    this.progress = 0
    this.playing = true
  }

  /** 设置速度倍率（0.5 慢放 / 1 / 2）。 */
  setSpeed(multiplier: number): void {
    this.speed = multiplier
  }

  /** 是否循环播放。 */
  setLoop(enabled: boolean): void {
    this.loop = enabled
  }

  /** 跳转到归一化进度（0~1）。 */
  seek(progress01: number): void {
    if (this.frames.length < 2) return
    const p = Math.max(0, Math.min(1, progress01)) * (this.frames.length - 1)
    this.progress = p
    this.applyProgress(p)
  }

  private clearMeshes(): void {
    for (const { mesh, mat } of this.atomMeshes.values()) {
      // 共享球（sphereFor 缓存）不得 dispose——否则会破坏其他分子实例。
      if (!isSharedSphere(mesh.geometry)) mesh.geometry.dispose()
      mat.dispose()
      this.group?.remove(mesh)
    }
    for (const { mesh, mat } of this.bondMeshes.values()) {
      mesh.geometry.dispose()
      mat.dispose()
      this.group?.remove(mesh)
    }
    this.atomMeshes.clear()
    this.bondMeshes.clear()
  }

  dispose(): void {
    if (this.frameHandle !== null) {
      cancelAnimationFrame(this.frameHandle)
      this.frameHandle = null
    }
    this.resizeObserver?.disconnect()
    this.resizeObserver = null
    if (this.themeQuery && this.themeListener) {
      this.themeQuery.removeEventListener('change', this.themeListener)
    }
    this.themeQuery = null
    this.themeListener = null
    this.controls?.dispose()
    this.controls = null
    this.clearMeshes()
    this.renderer?.dispose()
    this.renderer?.domElement.remove()
    this.renderer = null
    this.scene = null
    this.camera = null
    this.group = null
  }
}

/**
 * 键的规范键（小 index 在前），保证 (a,b) 与 (b,a) 同一根网格。
 *
 * **导出**：这是纯函数，单测可直接验（见 `tests/reaction.spec.ts`）。
 */
export function bondKey(a: number, b: number): string {
  return a < b ? `${a}-${b}` : `${b}-${a}`
}

/**
 * 按连续进度在两帧间插值原子坐标。
 *
 * 不依赖 Three.js：返回 `index → [x,y,z]`，由 `applyProgress` 套到网格上。
 * 因此可在 happy-dom 下独立单测（无需 WebGL）。
 *
 * 每个原子 index 在所有帧都出现（后端 `build_frames` 保证），
 * 故可安全按 index 取；某帧缺失时回退到相邻帧坐标。
 */
export function interpolateAtomPositions(
  frames: MechanismFrame[],
  progress: number,
): Map<number, [number, number, number]> {
  const out = new Map<number, [number, number, number]>()
  if (frames.length === 0) return out
  const lo = Math.max(0, Math.min(Math.floor(progress), frames.length - 1))
  const hi = Math.min(lo + 1, frames.length - 1)
  const frac = progress - lo
  const fLo = frames[lo]
  const fHi = frames[hi]
  const indices = new Set<number>()
  for (const a of fLo.atoms) indices.add(a.index)
  for (const a of fHi.atoms) indices.add(a.index)
  for (const idx of indices) {
    const pa = fLo.atoms.find((a) => a.index === idx)
    const pb = fHi.atoms.find((a) => a.index === idx)
    if (pa && pb) {
      out.set(idx, [
        pa.x + (pb.x - pa.x) * frac,
        pa.y + (pb.y - pa.y) * frac,
        pa.z + (pb.z - pa.z) * frac,
      ])
    } else if (pb) {
      // 原子只在 hi 帧出现（lo 帧缺失）→ 用 hi 帧坐标（回退到相邻帧）。
      out.set(idx, [pb.x, pb.y, pb.z])
    } else if (pa) {
      out.set(idx, [pa.x, pa.y, pa.z])
    }
  }
  return out
}

/**
 * 按连续进度在两帧间插值键的不透明度。
 *
 * 断键 1→0、成键 0→1 的淡入淡出由此决定；与坐标插值同口径、可独立单测。
 */
export function interpolateBondOpacities(
  frames: MechanismFrame[],
  progress: number,
): Map<string, number> {
  const out = new Map<string, number>()
  if (frames.length === 0) return out
  const lo = Math.max(0, Math.min(Math.floor(progress), frames.length - 1))
  const hi = Math.min(lo + 1, frames.length - 1)
  const frac = progress - lo
  const fLo = frames[lo]
  const fHi = frames[hi]
  const keys = new Set<string>()
  for (const b of fLo.bonds) keys.add(bondKey(b.a, b.b))
  for (const b of fHi.bonds) keys.add(bondKey(b.a, b.b))
  for (const key of keys) {
    const [aStr, bStr] = key.split('-').map(Number)
    const oLo = bondOpacityAt(fLo, aStr, bStr)
    const oHi = bondOpacityAt(fHi, aStr, bStr)
    out.set(key, oLo + (oHi - oLo) * frac)
  }
  return out
}

function bondOpacityAt(frame: MechanismFrame, a: number, b: number): number {
  const key = bondKey(a, b)
  const hit = frame.bonds.find((bd) => bondKey(bd.a, bd.b) === key)
  return hit ? hit.opacity : 0
}
