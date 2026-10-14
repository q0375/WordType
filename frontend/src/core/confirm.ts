import { ElMessageBox } from 'element-plus';

/** 危险操作统一确认框的入参（语义化字段，替代 ElMessageBox 的位置参数） */
export interface ConfirmActionOptions {
  message: string;
  title?: string;
  confirmText?: string;
  cancelText?: string;
  type?: 'success' | 'info' | 'warning' | 'error';
}

const FOCUS_CLS = 'kb-nav-focus';

function messageBoxButtons(): HTMLElement[] {
  const box = document.querySelector('.el-overlay-message-box .el-message-box');
  if (!box) return [];
  return Array.from(box.querySelectorAll<HTMLElement>('.el-message-box__btns button'));
}

function markFocus(btns: HTMLElement[], idx: number) {
  btns.forEach((b, i) => b.classList.toggle(FOCUS_CLS, i === idx));
}

/**
 * 统一确认框：破坏性操作一律走这里，不直接调用 ElMessageBox.confirm。
 *
 * 键盘约定（全站一致，见 installMessageBoxKeys）：
 *   ↑/↓/←/→ 在两个按钮间移动并高亮；Enter 触发当前高亮项；空格等同 Enter；Esc 取消。
 * 危险操作初始焦点与高亮都落在「取消」上——回车/空格不会误触破坏性动作。
 */
export async function confirmAction(opts: ConfirmActionOptions): Promise<boolean> {
  try {
    await ElMessageBox.confirm(opts.message, opts.title ?? '请确认', {
      confirmButtonText: opts.confirmText ?? '确定',
      cancelButtonText: opts.cancelText ?? '取消',
      type: opts.type ?? 'warning',
      autofocus: false, // 不自动聚焦「确定」，初始焦点由 installMessageBoxKeys 兜底到「取消」
      closeOnClickModal: false, // 点遮罩不关闭，避免误点丢数据
    });
    return true;
  } catch {
    return false; // 取消 / Esc / 关闭
  }
}

/**
 * 「取消」按钮的下标。
 *
 * 🔴 必须同时认中英文：13 处裸 `ElMessageBox.confirm` 没传 `cancelButtonText`，
 * Element Plus 会用**英文默认** `Cancel` / `OK`。只匹配「取消」会退回 0，
 * 而裸弹窗的按钮顺序不保证（实测删除词库是 ['Cancel','OK']，若换成 ['OK','Cancel'] 就点到删除上了）。
 * 故先按取消文案（中/英）定位，再按「排除确认文案」兜底，最后才退回首按钮。
 */
function cancelIndex(btns: HTMLElement[]): number {
  const text = (b: HTMLElement) => (b.innerText || b.textContent || '').trim().toLowerCase();
  const CANCEL_WORDS = ['取消', 'cancel', '再想想', '算了', '不了', '否', 'no'];
  const CONFIRM_WORDS = ['确定', '确认', 'ok', 'yes', '是', '提交', '保存', '删除', '继续', '移出', '清空'];
  let i = btns.findIndex((b) => CANCEL_WORDS.includes(text(b)));
  if (i >= 0) return i;
  // 没有取消文案：取第一个「不像确认按钮」的
  i = btns.findIndex((b) => !CONFIRM_WORDS.includes(text(b)));
  return i >= 0 ? i : 0;
}

let installed = false;

/**
 * 全局补丁：给站内所有 MessageBox（含管理端既有的 ElMessageBox.confirm 调用）
 * 补上方向键切换 + 高亮 + 空格确认，零侵入生效。在 main.ts 里安装一次。
 */
export function installMessageBoxKeys(): void {
  if (installed) return;
  installed = true;
  // 裸 ElMessageBox（未走 confirmAction 的旧调用，如管理端 13 处）没有初始高亮，
  // 这里统一兜底：弹窗出现时把焦点/高亮挪到「取消」。
  // ⚠️ 三个时序坑（都实测踩过）：
  //  1. Element Plus 在自己的 opened 钩子里异步 focus() 抢焦点 → 同一帧设的焦点会被覆盖，
  //     需 50ms 后补设一次；
  //  2. 裸弹窗没传 autofocus，EP 会把焦点放到**确认按钮**上（危险侧），
  //     所以「焦点已在按钮上」不能作为放弃接管的理由——要判断在不在取消按钮上；
  //  3. 裸弹窗用英文默认文案 Cancel/OK，cancelIndex 必须中英都认。
  // 判重用按钮元素引用（元素引用还能识别「同一弹窗内重渲染」，计数不行）。
  let seen: HTMLElement | null = null;
  let pending = 0;
  const arm = (btns: HTMLElement[]) => {
    const i = cancelIndex(btns);
    const active = document.activeElement as HTMLElement | null;
    // 焦点已在取消按钮上：只补高亮，不打断用户已建立的焦点
    if (active === btns[i]) {
      markFocus(btns, i);
      return;
    }
    btns[i].focus();
    markFocus(btns, i);
  };
  const ob = new MutationObserver(() => {
    const btns = messageBoxButtons();
    if (btns.length < 2) {
      seen = null;
      return;
    }
    if (seen && btns.includes(seen)) return; // 同一弹窗，只处理一次
    seen = btns[0];
    arm(btns); // 先同步设一次（高亮立即可见，EP 抢走也不影响视觉）
    window.clearTimeout(pending);
    pending = window.setTimeout(() => arm(btns), 50); // 50ms 后补设，覆盖 EP 的异步 focus
  });
  ob.observe(document.body, { childList: true, subtree: true });

  document.addEventListener(
    'keydown',
    (e: KeyboardEvent) => {
      const btns = messageBoxButtons();
      if (btns.length < 2) return;
      const idx = btns.findIndex((b) => b === document.activeElement);

      if (e.key === 'ArrowUp' || e.key === 'ArrowLeft' || e.key === 'ArrowDown' || e.key === 'ArrowRight') {
        e.preventDefault();
        e.stopPropagation();
        const step = e.key === 'ArrowDown' || e.key === 'ArrowRight' ? 1 : -1;
        const next = (idx + step + btns.length) % btns.length;
        btns[next].focus();
        markFocus(btns, next);
        return;
      }

      if (e.key === ' ' || e.key === 'Spacebar') {
        // 空格：触发当前高亮项；无高亮时走「取消」（按文案定位，不靠 DOM 顺序）
        e.preventDefault();
        e.stopPropagation();
        btns[idx >= 0 ? idx : cancelIndex(btns)].click();
        return;
      }

      if (e.key === 'Enter' && idx < 0) {
        // 焦点不在按钮上时回车 = 取消（安全默认）
        e.preventDefault();
        e.stopPropagation();
        btns[cancelIndex(btns)].click();
        return;
      }

      if (idx >= 0) markFocus(btns, idx);
    },
    true, // 捕获阶段：抢在页面自身的 Esc/回车快捷键之前
  );
}
