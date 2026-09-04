/** 打字明细采集（detail_json 契约 §3.1 DBD）+ WPM/准确率（附录 C）。 */

export interface KeyLog {
  i: number;
  expected: string;
  typed: string;
  t: number; // 相对该题首键 ms
  ok: boolean;
}

export class TypingSession {
  private logs: KeyLog[] = [];
  private totalKeys = 0;
  private correctKeys = 0;
  private firstTs: number | null = null;
  private lastTs: number | null = null;
  private idx = 0;

  constructor(private expected: string) {}

  /** 每次可打印字符 keydown 调用；退格不产生条目（分母口径一致） */
  key(typed: string): boolean {
    const now = performance.now();
    if (this.firstTs === null) this.firstTs = now;
    this.lastTs = now;
    const expected = this.expected[this.idx] || '';
    const ok = typed === expected;
    this.logs.push({ i: this.idx, expected, typed, t: Math.round(now - this.firstTs), ok });
    this.totalKeys += 1;
    if (ok) this.correctKeys += 1;
    this.idx += 1;
    return ok;
  }

  get position(): number {
    return this.idx;
  }

  detail(): { v: number; duration_ms: number; keys: KeyLog[] } {
    return {
      v: 1,
      duration_ms: this.lastTs !== null && this.firstTs !== null ? Math.round(this.lastTs - this.firstTs) : 0,
      keys: this.logs.slice(0, 500), // 截断防超 16KB
    };
  }

  /** 有效输入分钟 = 首键→末键墙钟（口径13） */
  wpm(): number {
    const minutes = this.lastTs !== null && this.firstTs !== null ? (this.lastTs - this.firstTs) / 60000 : 0;
    if (minutes <= 0) return 0;
    return Math.round((this.correctChars() / 5 / minutes) * 10) / 10;
  }

  accuracy(): number {
    return this.totalKeys ? Math.round((this.correctKeys / this.totalKeys) * 1000) / 1000 : 0;
  }

  correctChars(): number {
    return this.expected.split('').filter((_, i) => this.logs.find((l) => l.i === i)?.ok).length;
  }
}
