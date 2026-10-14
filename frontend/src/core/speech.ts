import { ElMessage } from 'element-plus';

/**
 * 发音方案：微软 edge-tts（后端合成 + 磁盘缓存），接口 /api/v1/tts
 *  - accent: us（en-US-AriaNeural）/ uk（en-GB-SoniaNeural）
 *  - 兜底：浏览器 SpeechSynthesis（离线可用，音质依赖系统）
 *
 * 🔴 单通道铁律（2026-10-04 修「男声女声同时响」）：
 * 同一时刻只允许一个声源播放。旧实现掐断上一条音频（pause + src=''）会触发它的
 * onerror / play() reject，被 .catch 误判成「API 失败」→ 降级到浏览器 TTS，
 * 于是新音频（edge-tts 女声）与浏览器音色（Windows 默认常是男声）叠着念。
 * 现在用播放序号 playSeq 作废旧请求的回调：被取代的播音既不 resolve 也不 reject，
 * 降级前再校验序号，杜绝叠加。setAccent 之前从未被调用，accent 恒为 us。
 */

let currentAudio: HTMLAudioElement | null = null;
/** 播音序号：每次真正发起播音 +1；只有 seq === playSeq 的异步回调才允许生效 */
let playSeq = 0;

export type Accent = 'us' | 'uk';

/** 停掉一切发声通道（切题/连播前必须调用）。先摘回调再掐断，避免误触发降级。 */
function stopAll() {
  if (currentAudio) {
    const a = currentAudio;
    currentAudio = null;
    a.onended = null; // 关键：先摘回调，pause/src='' 引发的事件才是安全的
    a.onerror = null;
    try {
      a.pause();
    } catch {
      /* 已结束 */
    }
    a.removeAttribute('src');
    try {
      a.load(); // 真正中断请求并释放连接
    } catch {
      /* 忽略 */
    }
  }
  if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
    try {
      speechSynthesis.cancel(); // 浏览器 TTS 兜底也可能在念，一并停
    } catch {
      /* 忽略 */
    }
  }
}

function playApi(word: string, a: Accent, seq: number): Promise<void> {
  return new Promise((resolve, reject) => {
    // 发起前若已被更新的播音取代，直接放弃（不 reject，避免无谓降级）
    if (seq !== playSeq) return;
    const url = `/api/v1/tts?word=${encodeURIComponent(word)}&accent=${a}`;
    let audio: HTMLAudioElement;
    try {
      audio = new Audio(url);
    } catch (e) {
      reject(e);
      return;
    }
    currentAudio = audio;
    const isStale = () => seq !== playSeq;
    audio.onended = () => {
      if (isStale()) return;
      if (currentAudio === audio) currentAudio = null;
      resolve();
    };
    audio.onerror = () => {
      if (isStale()) return; // 已被取代的失败不触发降级
      if (currentAudio === audio) currentAudio = null;
      reject(new Error('tts api failed'));
    };
    audio.play().catch((e) => {
      if (isStale()) return;
      if (currentAudio === audio) currentAudio = null;
      reject(e);
    });
  });
}

function playTts(word: string, a: Accent, seq: number): boolean {
  try {
    if (typeof window === 'undefined' || !('speechSynthesis' in window)) return false;
    if (seq !== playSeq) return false;
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(word);
    u.lang = a === 'uk' ? 'en-GB' : 'en-US';
    u.rate = 0.9;
    // 固定音色：系统里同名语言常同时装了男声/女声，不指定时各平台挑的随机，
    // 同一段里前后两次发音可能一男一女。取首个匹配项并锁定。
    try {
      const voices = speechSynthesis.getVoices() || [];
      const want = u.lang.toLowerCase();
      const hit = voices.find((v) => (v.lang || '').toLowerCase().replace('_', '-') === want)
        || voices.find((v) => (v.lang || '').toLowerCase().startsWith('en'));
      if (hit) u.voice = hit;
    } catch {
      /* 音色列表未就绪，用系统默认 */
    }
    speechSynthesis.speak(u);
    return true;
  } catch {
    return false;
  }
}

let accent: Accent = 'us';
export function setAccent(a: Accent) {
  accent = a;
}

/** 停止当前正在播放的一切发音（组件卸载/离开模块时调用）。 */
export function stopSpeaking(): void {
  playSeq += 1; // 作废在途回调
  stopAll();
}

/** 播报单词：edge-tts 优先，失败自动降级浏览器 TTS（同一时刻只有一个声源）。 */
export function speakWord(word: string, a?: Accent): void {
  if (!word) return;
  stopAll();
  const seq = ++playSeq;
  const acc = a ?? accent;
  playApi(word, acc, seq).catch(() => {
    if (seq !== playSeq) return; // 已被新播音取代，不再降级（否则两声叠加）
    if (!playTts(word, acc, seq)) ElMessage.warning({ message: '当前环境无法播放发音', duration: 2000, showClose: true });
  });
}

/** 听音题专用：返回是否至少一条通道可用。 */
export async function speakWordOk(word: string, a?: Accent): Promise<boolean> {
  if (!word) return false;
  stopAll();
  const seq = ++playSeq;
  const acc = a ?? accent;
  try {
    await playApi(word, acc, seq);
    return true;
  } catch {
    if (seq !== playSeq) return false; // 已被取代，不再降级
    return playTts(word, acc, seq);
  }
}
