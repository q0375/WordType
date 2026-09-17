// 全局主题（浅色/深色/跟随系统），类名挂在 <html> 上（html.dark），
// 与 Element Plus 官方 dark css-vars 以及 app.css 中的 --wt-* 变量配合使用。
export type ThemeMode = 'light' | 'dark' | 'auto';

const STORAGE_KEY = 'wt-theme';

let currentMode: ThemeMode = 'light';
const media = window.matchMedia('(prefers-color-scheme: dark)');

export function getThemeMode(): ThemeMode {
  return currentMode;
}

export function isDarkActive(): boolean {
  return currentMode === 'dark' || (currentMode === 'auto' && media.matches);
}

function apply() {
  document.documentElement.classList.toggle('dark', isDarkActive());
}

export function setThemeMode(mode: ThemeMode, persist = true) {
  currentMode = mode;
  if (persist) localStorage.setItem(STORAGE_KEY, mode);
  apply();
}

/** light -> dark -> auto 循环切换，返回新模式 */
export function cycleThemeMode(): ThemeMode {
  const order: ThemeMode[] = ['light', 'dark', 'auto'];
  const next = order[(order.indexOf(currentMode) + 1) % order.length];
  setThemeMode(next);
  return next;
}

export function initTheme() {
  const saved = localStorage.getItem(STORAGE_KEY) as ThemeMode | null;
  currentMode = saved === 'light' || saved === 'dark' || saved === 'auto' ? saved : 'auto';
  apply();
  // 系统主题变化时，auto 模式跟随
  media.addEventListener('change', () => {
    if (currentMode === 'auto') apply();
  });
}
