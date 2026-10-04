import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import './styles/main.css'

/**
 * 应用入口。
 *
 * Pinia 在 `createApp` 之后、挂载之前创建——
 * 顺序反了会导致 store 拿不到注入的 pinia 实例。
 */
createApp(App).use(createPinia()).mount('#app')
