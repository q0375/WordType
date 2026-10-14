<script setup lang="ts">
import { ref, computed } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { useAuthStore } from '../stores/auth';
import { getThemeMode, cycleThemeMode, type ThemeMode } from '../core/theme';

const router = useRouter();
const route = useRoute();
const auth = useAuthStore();
const mobileMenuOpen = ref(false);
const themeMode = ref<ThemeMode>(getThemeMode());

const themeIcon = computed(() =>
  themeMode.value === 'light' ? 'Sunny' : themeMode.value === 'dark' ? 'Moon' : 'Monitor',
);
const themeLabel = computed(() =>
  themeMode.value === 'light' ? '浅色模式' : themeMode.value === 'dark' ? '深色模式' : '跟随系统',
);

function toggleTheme() {
  themeMode.value = cycleThemeMode();
}

interface NavItem {
  id: string;
  path: string;
  title: string;
  admin?: boolean;
}

const navItems: NavItem[] = [
  { id: 'dashboard', path: '/', title: '仪表盘' },
  { id: 'study', path: '/study', title: '学习' },
  { id: 'practice', path: '/practice', title: '练习' },
  { id: 'review', path: '/review', title: '复习' },
  { id: 'dictation', path: '/dictation', title: '听写' },
  { id: 'exam', path: '/exam', title: '考核' },
  { id: 'game', path: '/game', title: '游戏' },
  { id: 'books', path: '/books', title: '词库' },
  { id: 'wrongbook', path: '/wrongbook', title: '错题本' },
  { id: 'statistics', path: '/statistics', title: '统计' },
  { id: 'profile', path: '/profile', title: '个人中心' },
  { id: 'settings', path: '/settings', title: '设置' },
  { id: 'admin', path: '/admin', title: '管理后台', admin: true },
];

const visibleRoutes = computed(() =>
  navItems.filter((r) => !r.admin || auth.isAdmin),
);

function go(path: string) {
  mobileMenuOpen.value = false;
  router.push(path);
}

async function onLogout() {
  mobileMenuOpen.value = false;
  // 登出时序（2026-09-22 修复）：考核进行中退出有组件级路由守卫（提前交卷/继续作答），
  // 全局守卫又会把已登录用户访问的 guest 页弹回仪表盘，两者需同时满足：
  //   1) beginLogout 标记 → 全局守卫放行 /login；
  //   2) 先导航后登出 → 考核守卫弹「提前交卷」时会话仍有效，交卷可成功；
  //      选「继续作答」→ 导航被取消（failure）→ 不登出，保持登录态继续作答。
  //   3) 导航成功才真正 logout（吊销 jti + 清本地态），finally 复位标记。
  auth.beginLogout();
  try {
    const failure = await router.push('/login');
    if (!failure) auth.logout();
  } catch {
    /* 导航异常：保守起见保持登录态留在当前页 */
  } finally {
    auth.endLogout();
  }
}
</script>

<template>
  <header class="top-nav">
    <div class="nav-inner">
      <div class="brand" @click="go('/')">
        <el-icon size="28" color="var(--wt-primary)"><Reading /></el-icon>
        <span>WordType</span>
      </div>

      <nav class="desktop-nav">
        <a
          v-for="item in visibleRoutes"
          :key="item.id"
          class="nav-link"
          :class="{ active: route.path === item.path }"
          href="#"
          @click.prevent="go(item.path)"
        >
          {{ item.title }}
        </a>
      </nav>

      <div class="nav-actions">
        <el-tooltip :content="`当前：${themeLabel}（点击切换）`" placement="bottom">
          <el-button text circle class="theme-toggle" @click="toggleTheme">
            <el-icon size="18"><component :is="themeIcon" /></el-icon>
          </el-button>
        </el-tooltip>
        <template v-if="auth.isLoggedIn">
          <span class="username clickable" @click="go('/profile')">{{ auth.displayName }}</span>
          <el-button type="danger" text @click="onLogout">退出</el-button>
        </template>
        <el-button class="menu-toggle" text @click="mobileMenuOpen = !mobileMenuOpen">
          <el-icon size="22"><Expand v-if="!mobileMenuOpen" /><Fold v-else /></el-icon>
        </el-button>
      </div>
    </div>

    <div v-show="mobileMenuOpen" class="mobile-menu">
      <a
        v-for="item in visibleRoutes"
        :key="item.id"
        href="#"
        class="mobile-link"
        @click.prevent="go(item.path)"
      >
        {{ item.title }}
      </a>
      <el-button v-if="auth.isLoggedIn" type="danger" text @click="onLogout">退出</el-button>
    </div>
  </header>
</template>

<style scoped>
.top-nav {
  position: sticky;
  top: 0;
  z-index: 100;
  background: var(--wt-nav-bg);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--wt-border);
}
.nav-inner {
  max-width: 1200px;
  margin: 0 auto;
  padding: 0 16px;
  /* 顶栏总高由 --wt-nav-h 定义（含 1px 底边框），内容区扣掉边框保持 60px 视觉不变。
     若这里直接用完整变量，实际总高会比变量多出边框 1px，页面 min-height 扣减后仍留残差 */
  height: calc(var(--wt-nav-h, 61px) - 1px);
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 20px;
  font-weight: 700;
  color: var(--wt-text);
  cursor: pointer;
}
.desktop-nav {
  display: none;
  gap: 4px;
}
@media (min-width: 1024px) {
  .desktop-nav {
    display: flex;
  }
}
.nav-link {
  padding: 8px 12px;
  border-radius: 6px;
  color: var(--wt-text-2);
  text-decoration: none;
  font-size: 14px;
  transition: background 0.2s, color 0.2s;
}
.nav-link:hover,
.nav-link.active {
  background: var(--wt-primary-bg);
  color: var(--wt-primary);
}
.nav-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}
.username {
  font-size: 13px;
  color: var(--wt-text-2);
}
.username.clickable {
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 6px;
  transition: background 0.2s, color 0.2s;
}
.username.clickable:hover {
  background: var(--wt-primary-bg);
  color: var(--wt-primary);
}
.menu-toggle {
  display: flex;
}
@media (min-width: 1024px) {
  .menu-toggle {
    display: none;
  }
}
.mobile-menu {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  background: var(--wt-card-bg);
  border-bottom: 1px solid var(--wt-border);
  padding: 8px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.mobile-link {
  padding: 10px 12px;
  border-radius: 6px;
  color: var(--wt-text-2);
  text-decoration: none;
}
.mobile-link:hover {
  background: var(--wt-primary-bg);
}
</style>

<style>
/* 游戏沉浸模式：导航默认上收只留 4px 细边提示，鼠标移到顶部浮现（深色适配） */
body.game-dark .top-nav {
  transform: translateY(calc(-100% + 4px));
  background: rgba(18, 20, 26, 0.92);
  border-bottom-color: rgba(255, 255, 255, 0.12);
  transition: transform 0.25s ease, background 0.25s ease;
}
body.game-dark .top-nav:hover {
  transform: translateY(0);
}
body.game-dark .top-nav .brand,
body.game-dark .top-nav .username {
  color: var(--wt-border);
}
body.game-dark .top-nav .nav-link {
  color: var(--wt-text-4);
}
body.game-dark .top-nav .nav-link:hover,
body.game-dark .top-nav .nav-link.active {
  background: rgba(37, 99, 235, 0.25);
  color: #93b4ff;
}
body.game-dark .top-nav .mobile-menu {
  background: #1a1d26;
  border-bottom-color: rgba(255, 255, 255, 0.1);
}
body.game-dark .top-nav .mobile-link {
  color: var(--wt-border-strong);
}
body.game-dark .top-nav .mobile-link:hover {
  background: rgba(37, 99, 235, 0.25);
}
</style>
