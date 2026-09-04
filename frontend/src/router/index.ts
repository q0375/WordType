import { createRouter, createWebHistory } from 'vue-router';
import { useAuthStore } from '../stores/auth';

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('../views/auth/LoginView.vue'), meta: { guest: true } },
    { path: '/register', name: 'register', component: () => import('../views/auth/RegisterView.vue'), meta: { guest: true } },
    { path: '/', name: 'dashboard', component: () => import('../views/dashboard/DashboardView.vue') },
    { path: '/study', name: 'study', component: () => import('../views/study/StudyView.vue') },
    { path: '/practice', name: 'practice', component: () => import('../views/practice/PracticeView.vue') },
    { path: '/review', name: 'review', component: () => import('../views/review/ReviewView.vue') },
    { path: '/exam', name: 'exam', component: () => import('../views/exam/ExamView.vue') },
    { path: '/dictation', name: 'dictation', component: () => import('../views/study/DictationView.vue') },
    { path: '/game', name: 'game', component: () => import('../views/game/GameView.vue') },
    { path: '/books', name: 'books', component: () => import('../views/library/BooksView.vue') },
    { path: '/wrongbook', name: 'wrongbook', component: () => import('../views/wrongbook/WrongBookView.vue') },
    { path: '/statistics', name: 'statistics', component: () => import('../views/statistics/StatisticsView.vue') },
    { path: '/profile', name: 'profile', component: () => import('../views/profile/ProfileView.vue') },
    { path: '/settings', name: 'settings', component: () => import('../views/settings/SettingsView.vue') },
    { path: '/admin', name: 'admin', component: () => import('../views/admin/AdminView.vue'), meta: { admin: true } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
});

// 守卫：未登录重定向（B1）；admin 路由鉴权
router.beforeEach((to) => {
  const auth = useAuthStore();
  if (to.meta.guest && auth.isLoggedIn) return { name: 'dashboard' };
  if (!to.meta.guest && !auth.isLoggedIn) return { name: 'login', query: { redirect: to.fullPath } };
  if (to.meta.admin && !auth.isAdmin) return { name: 'dashboard' };
  return true;
});

export default router;
