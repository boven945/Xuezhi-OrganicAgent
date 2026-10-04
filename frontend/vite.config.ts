/**
 * 从 `vitest/config` 而非 `vite` 导入 `defineConfig`。
 *
 * 两者同名但类型不同：vite 的版本**不认识** `test` 字段，
 * 写上去会被类型检查判为未知属性。`vitest/config` 是 vite 配置
 * 与测试配置的联合类型，这是 vitest 官方推荐入口。
 */
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

/**
 * Vite 构建配置。
 *
 * ## 开发时的后端代理
 *
 * 前端代码里用的是**相对路径** `/api/...`（见 `api/client.ts`的 `API_BASE`）。
 * 开发时（5173 端口）需要转到后端（8000 端口），故配代理。
 *
 * **SSE 必须关掉缓冲**：默认的代理会等响应结束才转发，
 * 那会让流式退化成同步——学生看不到文字逐渐出现。
 * `changeOrigin` 仅为正确设置 Host 头。
 */
export default defineConfig({
  plugins: [vue()],

  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },

  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_BACKEND_URL ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      // 健康检查也在代理范围内，否则前端探测的是 Vite 自己
      '/health': {
        target: process.env.VITE_BACKEND_URL ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/ready': {
        target: process.env.VITE_BACKEND_URL ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },

  build: {
    // 教学演示场景不追求极限体积，但要有可预期的产物大小上限，
    // 避免某次误引入大依赖 unnoticed地撑大包体。
    chunkSizeWarningLimit: 700,
    sourcemap: true,
  },

  test: {
    // 用 happy-dom 而非 jsdom：前者体积小约 8 倍且启动快。
    // 本项目的组件测试不需要 jsdom 的完整 HTML 解析实现。
    environment: 'happy-dom',
    globals: true,
    include: ['tests/**/*.spec.ts'],
  },
})
