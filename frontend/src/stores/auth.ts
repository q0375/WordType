import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { authApi } from '../api';

export interface AuthUser {
  id: number;
  username: string;
  nickname?: string | null; // 2026-09-22：昵称（展示名），未设置回落用户名
  role: 'user' | 'admin';
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem('wt_token') || '');
  const user = ref<AuthUser | null>(JSON.parse(localStorage.getItem('wt_user') || 'null'));

  const isLoggedIn = computed(() => !!token.value && !!user.value);
  const isAdmin = computed(() => user.value?.role === 'admin');
  // 展示名：昵称优先，未设置回落用户名（2026-09-22）
  const displayName = computed(() => user.value?.nickname?.trim() || user.value?.username || '');

  function setToken(t: string) {
    token.value = t;
    localStorage.setItem('wt_token', t);
  }

  function setSession(t: string, u: AuthUser) {
    setToken(t);
    user.value = u;
    localStorage.setItem('wt_user', JSON.stringify(u));
  }

  // 登出进行中标记：全局路由守卫据此放行 guest 页（/login）。
  // 退出流程是「先导航后登出」（AppNav.onLogout）：导航期间会话还活着，
  // 考核的路由守卫要靠它完成「提前交卷」；没有标记的话 guest 守卫会把人弹回仪表盘。
  let loggingOut = false;
  function beginLogout() {
    loggingOut = true;
  }
  function endLogout() {
    loggingOut = false;
  }
  function isLoggingOut() {
    return loggingOut;
  }

  function logout() {
    // 先用当前 token 通知后端作废会话（token 已过期时由拦截器静默忽略 401）。
    // 必须显式传 token：本函数同步清空 token，而 axios 拦截器微任务里才读 store。
    const t = token.value;
    if (t) authApi.logout(t).catch(() => undefined);
    // 清空本用户 localStorage 前缀缓存（D14）
    if (user.value) {
      const prefix = `${user.value.id}:`;
      const keys: string[] = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith(prefix)) keys.push(k);
      }
      keys.forEach((k) => localStorage.removeItem(k));
    }
    token.value = '';
    user.value = null;
    localStorage.removeItem('wt_token');
    localStorage.removeItem('wt_user');
  }

  return { token, user, isLoggedIn, isAdmin, displayName, setToken, setSession, logout, beginLogout, endLogout, isLoggingOut };
});
