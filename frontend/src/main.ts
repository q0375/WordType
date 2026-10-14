import { createApp } from 'vue';
import { createPinia } from 'pinia';
import {
  ArrowLeft,
  Expand,
  Fold,
  Monitor,
  Moon,
  Reading,
  Sunny,
  UploadFilled,
  VideoPause,
  VideoPlay,
} from '@element-plus/icons-vue';
import 'element-plus/dist/index.css';
import 'element-plus/theme-chalk/dark/css-vars.css';
import App from './App.vue';
import router from './router';
import './styles/app.css';
import { bindReplayTriggers } from './core/offline';
import { initTheme } from './core/theme';
import { anticheat } from './core/anticheat';
import { installMessageBoxKeys } from './core/confirm';

initTheme();
// 全站确认框键盘导航（方向键切换 + 空格确认 + 高亮），对既有 ElMessageBox.confirm 调用同样生效
installMessageBoxKeys();

// 仅注册项目实际用到的图标（模板里以 <Reading /> 或 :is="'Sunny'" 形式引用）。
// 注意：AppNav 的主题图标是动态名字（Sunny/Moon/Monitor），必须全局注册。
const ICONS = {
  ArrowLeft,
  Expand,
  Fold,
  Monitor,
  Moon,
  Reading,
  Sunny,
  UploadFilled,
  VideoPause,
  VideoPlay,
};

const app = createApp(App);
for (const [key, component] of Object.entries(ICONS)) {
  app.component(key, component);
}
app.directive('anticheat', anticheat);
app.use(createPinia());
app.use(router);
app.mount('#app');

bindReplayTriggers();
