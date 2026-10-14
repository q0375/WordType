import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import Components from 'unplugin-vue-components/vite';
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers';

export default defineConfig({
  base: './',
  plugins: [
    vue(),
    // Element Plus 组件按需引入（含 v-loading 等指令）。
    // importStyle: false —— 样式仍由 main.ts 统一引入全量 CSS，避免逐个组件样式
    // 与现有主题覆盖顺序打架（这一步只做 JS 侧 tree-shaking）。
    Components({
      dts: false,
      resolvers: [ElementPlusResolver({ importStyle: false, directives: true })],
    }),
  ],
  server: {
    // 监听所有网卡：手机/其他设备连入本机热点或同网段 Wi-Fi 后可访问 http://<本机IP>:5173
    host: '0.0.0.0',
    port: 5173,
    // 允许任意 Host 头（局域网 IP、域名均可），否则 Vite 的 Host 校验会直接返回 403
    allowedHosts: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    chunkSizeWarningLimit: 900,
  },
});
