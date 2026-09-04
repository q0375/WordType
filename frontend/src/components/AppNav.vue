<script setup lang="ts">
import { ref, computed } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { useAuthStore } from '../stores/auth';

const router = useRouter();
const route = useRoute();
const auth = useAuthStore();
const mobileMenuOpen = ref(false);

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

function onLogout() {
  mobileMenuOpen.value = false;
  auth.logout();
  router.push('/login');
}
</script>

<template>
  <header class="top-nav">
    <div class="nav-inner">
      <div class="brand" @click="go('/')">
        <el-icon size="28" color="#2563EB"><Reading /></el-icon>
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
        <template v-if="auth.isLoggedIn">
          <span class="username">{{ auth.user?.username }}</span>
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
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid #e5e7eb;
}
.nav-inner {
  max-width: 1200px;
  margin: 0 auto;
  padding: 0 16px;
  height: 60px;
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
  color: #111827;
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
  color: #4b5563;
  text-decoration: none;
  font-size: 14px;
  transition: background 0.2s, color 0.2s;
}
.nav-link:hover,
.nav-link.active {
  background: #eff6ff;
  color: #2563eb;
}
.nav-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}
.username {
  font-size: 13px;
  color: #374151;
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
  background: #fff;
  border-bottom: 1px solid #e5e7eb;
  padding: 8px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.mobile-link {
  padding: 10px 12px;
  border-radius: 6px;
  color: #374151;
  text-decoration: none;
}
.mobile-link:hover {
  background: #eff6ff;
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
  color: #e5e7eb;
}
body.game-dark .top-nav .nav-link {
  color: #9ca3af;
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
  color: #d1d5db;
}
body.game-dark .top-nav .mobile-link:hover {
  background: rgba(37, 99, 235, 0.25);
}
</style>
