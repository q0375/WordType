/** D19 断网重放队列 + D12 练习进度（{userId}: 前缀隔离）。 */
import { useAuthStore } from '../stores/auth';
import http from '../api/http';
import { ElMessage } from 'element-plus';

interface QueueItem {
  request_id: string;
  url: string;
  body: any;
  failed_at: number;
}

const REPLAY_ENDPOINTS = ['/study/self-rate', '/study/dictation', '/practice/answer', '/review/answer'];

function queueKey(): string {
  const auth = useAuthStore();
  return `${auth.user?.id ?? 'anon'}:pending`;
}

export function enqueueFailed(payload: { request_id: string; url: string; body: any }) {
  const items: QueueItem[] = JSON.parse(localStorage.getItem(queueKey()) || '[]');
  items.push({ ...payload, failed_at: Date.now() });
  localStorage.setItem(queueKey(), JSON.stringify(items));
}

let replaying = false;

/** online 事件 / 页面 focus / 30s 兜底 触发；Web Locks 互斥防双标签页 */
export async function replayPending(): Promise<void> {
  const items: QueueItem[] = JSON.parse(localStorage.getItem(queueKey()) || '[]');
  if (!items.length || replaying) return;
  replaying = true;
  try {
    // @ts-ignore Web Locks（不可用时退化为进程内互斥）
    if (navigator.locks) {
      // @ts-ignore
      await navigator.locks.request('wt-replay', async () => doReplay(items));
    } else {
      await doReplay(items);
    }
  } finally {
    replaying = false;
  }
}

async function doReplay(items: QueueItem[]) {
  items.sort((a, b) => a.failed_at - b.failed_at);
  const remain: QueueItem[] = [];
  for (const item of items) {
    if (Date.now() - item.failed_at > 24 * 3600 * 1000) continue; // 过期剔除
    if (!REPLAY_ENDPOINTS.some((p) => item.url.includes(p))) continue;
    try {
      await http.post(item.url, item.body);
      // 2xx（含幂等重放）→ 剔除
    } catch (e: any) {
      if (e?.status >= 400 && e?.status < 500) {
        // 4xx 业务错 → 剔除 + 提示
        ElMessage.warning(`有 ${items.length} 条离线作答未能同步：额度或状态已变化`);
      } else {
        remain.push(item); // 网络错误 → 保留
      }
    }
  }
  localStorage.setItem(queueKey(), JSON.stringify(remain));
}

export function bindReplayTriggers() {
  window.addEventListener('online', () => replayPending());
  window.addEventListener('focus', () => replayPending());
  setInterval(() => replayPending(), 30000);
}

/** D12：练习进度 30s 防抖本地持久化 */
export function savePracticeProgress(userId: number | undefined, sessionId: string, progress: unknown) {
  if (!userId) return;
  localStorage.setItem(`${userId}:practice:${sessionId}`, JSON.stringify(progress));
}

export function loadPracticeProgress<T>(userId: number | undefined, sessionId: string): T | null {
  if (!userId) return null;
  const raw = localStorage.getItem(`${userId}:practice:${sessionId}`);
  return raw ? (JSON.parse(raw) as T) : null;
}
