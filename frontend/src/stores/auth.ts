import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { authApi } from '../api';

export interface AuthUser {
  id: number;
  username: string;
  role: 'user' | 'admin';
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem('wt_token') || '');
  const user = ref<AuthUser | null>(JSON.parse(localStorage.getItem('wt_user') || 'null'));

  const isLoggedIn = computed(() => !!token.value && !!user.value);
  const isAdmin = computed(() => user.value?.role === 'admin');

  function setToken(t: string) {
    token.value = t;
    localStorage.setItem('wt_token', t);
  }

  function setSession(t: string, u: AuthUser) {
    setToken(t);
    user.value = u;
    localStorage.setItem('wt_user', JSON.stringify(u));
  }

  function logout() {
    // 先用当前 token 通知后端作废会话（token 已过期时由拦截器静默忽略 401）
    if (token.value) authApi.logout().catch(() => undefined);
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

  return { token, user, isLoggedIn, isAdmin, setToken, setSession, logout };
});
