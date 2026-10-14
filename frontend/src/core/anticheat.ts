/**
 * 输入防作弊（D37）：在受控输入元素上检测 Ctrl / Alt / Meta / Insert 键，
 * 一旦按下立即清空该输入内容并提示。用于考核、练习、学习、复习、游戏等打字输入。
 *
 * 用法（Vue 指令）：v-anticheat 挂在 el-input 或原生 input 的宿主元素上，
 * 指令会在内部 input 上注册捕获阶段 keydown 监听。
 */
import type { Directive } from 'vue';

const FORBIDDEN = new Set(['Control', 'Alt', 'AltGraph', 'Meta', 'OS', 'Insert', 'PrintScreen']);

let warningTimer: number | null = null;
function warnOnce() {
  // 动态引入避免循环依赖（ElMessage 很轻，直接 import 也无碍，这里用全局单例提示）
  import('element-plus').then(({ ElMessage }) => {
    ElMessage.warning({ message: '检测到禁用按键（Ctrl/Alt/Insert），输入已清空', duration: 2000, showClose: true });
  });
  if (warningTimer) window.clearTimeout(warningTimer);
  warningTimer = window.setTimeout(() => (warningTimer = null), 1500);
}

function findInput(el: HTMLElement): HTMLInputElement | null {
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) return el as HTMLInputElement;
  return el.querySelector('input, textarea');
}

function clearValue(el: HTMLElement) {
  const input = findInput(el);
  if (!input) return;
  if (!input.value) return;
  input.value = '';
  // 触发 v-model 同步
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

function onKeydown(e: KeyboardEvent) {
  if (e.ctrlKey || e.altKey || e.metaKey || FORBIDDEN.has(e.key)) {
    e.preventDefault();
    e.stopPropagation();
    clearValue(e.currentTarget as HTMLElement);
    warnOnce();
  }
}

export const anticheat: Directive<HTMLElement> = {
  mounted(el: HTMLElement) {
    const target = findInput(el) ?? el;
    target.addEventListener('keydown', onKeydown as EventListener, true);
    (el as any).__anticheatHandler = onKeydown;
    (el as any).__anticheatTarget = target;
  },
  unmounted(el: HTMLElement) {
    const target = (el as any).__anticheatTarget ?? el;
    target.removeEventListener('keydown', onKeydown as EventListener, true);
    delete (el as any).__anticheatHandler;
    delete (el as any).__anticheatTarget;
  },
};

export function isForbiddenKey(e: KeyboardEvent): boolean {
  return e.ctrlKey || e.altKey || e.metaKey || FORBIDDEN.has(e.key);
}
