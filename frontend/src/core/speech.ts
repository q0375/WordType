import { ElMessage } from 'element-plus';

/**
 * 发音方案：微软 edge-tts（后端合成 + 磁盘缓存），接口 /api/v1/tts
 *  - accent: us（en-US-AriaNeural）/ uk（en-GB-SoniaNeural）
 *  - 兜底：浏览器 SpeechSynthesis（离线可用，音质依赖系统）
 */

let currentAudio: HTMLAudioElement | null = null;

export type Accent = 'us' | 'uk';

function playApi(word: string, accent: Accent): Promise<void> {
  return new Promise((resolve, reject) => {
    try {
      if (currentAudio) {
        currentAudio.pause();
        currentAudio.src = '';
      }
      const url = `/api/v1/tts?word=${encodeURIComponent(word)}&accent=${accent}`;
      const audio = new Audio(url);
      currentAudio = audio;
      audio.onended = () => resolve();
      audio.onerror = () => reject(new Error('tts api failed'));
      audio.play().catch(reject);
    } catch (e) {
      reject(e);
    }
  });
}

function playTts(word: string, a: Accent): boolean {
  try {
    if (!('speechSynthesis' in window)) return false;
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(word);
    u.lang = a === 'uk' ? 'en-GB' : 'en-US';
    u.rate = 0.9;
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

/** 播报单词：edge-tts 优先，失败自动降级浏览器 TTS。 */
export function speakWord(word: string, a?: Accent): void {
  if (!word) return;
  playApi(word, a ?? accent).catch(() => {
    if (!playTts(word, a ?? accent)) ElMessage.warning('当前环境无法播放发音');
  });
}

/** 听音题专用：返回是否至少一条通道可用。 */
export async function speakWordOk(word: string, a?: Accent): Promise<boolean> {
  try {
    await playApi(word, a ?? accent);
    return true;
  } catch {
    return playTts(word, a ?? accent);
  }
}
