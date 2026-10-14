<script setup lang="ts">
import AppNav from './components/AppNav.vue';
import { useRoute } from 'vue-router';
import { computed } from 'vue';

const route = useRoute();
const showNav = computed(() => !route.meta.guest);
// 全局提示（ElMessage）弹出位置：固定顶栏下方（60px + 12px 间距），不再遮住菜单。
// 不能按当前路由有无导航动态切换：「登录成功」这类提示在登录页创建（无导航），2 秒存活期
// 内会跨路由进入有导航的页面，创建时刻的 offset 已固化 → 统一 72 才能全程避开顶栏；
// 登录 / 注册页本身无导航，提示稍低一点无碍。单次调用显式传 offset 时仍以调用为准。
const messageCfg = { offset: 72 };
</script>

<template>
  <el-config-provider :message="messageCfg">
    <AppNav v-if="showNav" />
    <router-view v-slot="{ Component }">
      <!-- 学习/听写/练习/复习 会话保活：切到其他页面再回来不丢进度 -->
      <keep-alive include="StudyView,DictationView,PracticeView,ReviewView">
        <component :is="Component" />
      </keep-alive>
    </router-view>
  </el-config-provider>
</template>

<style>
/* 游戏沉浸模式：整页深色渐变（由 GameView 在 body 上挂 game-dark 类控制） */
body.game-dark {
  background: linear-gradient(180deg, #1e212a 0%, #12141a 100%) fixed #12141a;
}
</style>
