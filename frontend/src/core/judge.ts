/** 双端判定同源引擎（与后端 domain/judge.py 规则逐字对齐）：normalize + Levenshtein≤1。 */

export function normalize(s: string): string {
  return (s || '').trim().toLowerCase();
}

export function levenshtein(a: string, b: string, maxDist = 1): number {
  if (Math.abs(a.length - b.length) > maxDist) return maxDist + 1;
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    let rowMin = i;
    for (let j = 1; j <= b.length; j++) {
      const v = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      cur.push(v);
      rowMin = Math.min(rowMin, v);
    }
    if (rowMin > maxDist) return maxDist + 1;
    prev = cur;
  }
  return prev[b.length];
}

export function judge(typed: string, spelling: string, loose = true): 'correct' | 'near' | 'wrong' {
  const t = normalize(typed);
  const s = normalize(spelling);
  if (t === s) return 'correct';
  if (loose && t && levenshtein(t, s, 1) <= 1) return 'near';
  return 'wrong';
}
