/** 双端判定同源引擎（与后端 domain/judge.py 规则逐字对齐）。
 *
 * 两个口径并存：
 * - judge()：三档 correct / near / wrong —— 学习 / 练习 / 复习使用（宽松模式容差固定 1）；
 * - partialRatio()：连续得分比 = 序列相似度（difflib SequenceMatcher.ratio，autojunk=False）
 *   —— 考核 / 游戏使用（2026-09-20 换算法，区分大小写、空格等一切字符参与比较）。
 *
 * normalize：仅去首尾空白，**大小写敏感**（"Apple" ≠ "apple"）。
 */

export function normalize(s: string): string {
  // 仅去首尾空白；大小写敏感（2026-09-19：大写/小写字母算错误字符）
  return (s || '').trim();
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

export function judge(typed: string, spelling: string, loose = true, tolerance?: number): 'correct' | 'near' | 'wrong' {
  const t = normalize(typed);
  const s = normalize(spelling);
  if (t === s) return 'correct';
  if (loose && t) {
    const tol = tolerance ?? 1;
    if (tol > 0 && levenshtein(t, s, tol) <= tol) return 'near';
  }
  return 'wrong';
}

/** 连续得分比 = 序列相似度（考核 / 游戏口径，2026-09-20 换算法）。
 *
 * 等价于 Python ``difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()``：
 * 相似度 = 2 × 最长匹配块覆盖总长 ÷ 两串长度之和。区分大小写、空格原样参与、不做 normalize；
 * 差异只拉低局部（"Hello World" vs "ello World" → 0.952），不再像逐位比较那样一个错位全错。
 * 空串 vs 空串 → 1.0、单边为空 → 0.0（difflib 原生语义）。
 * 下面 sequenceRatio 是 CPython difflib 的逐位等价复刻（b2j + find_longest_match + 相邻块合并），
 * 改动必须与后端同步 + 跑 verify_judge_boundary 对拍（含随机模糊串矩阵）。 */
export function partialRatio(typed: string, spelling: string): number {
  const a = typed || '';
  const b = spelling || '';
  const total = a.length + b.length;
  if (!total) return 1.0; // difflib：两串皆空 ratio=1.0
  return (2 * matchingBlockTotal(a, b)) / total;
}

/** difflib.get_matching_blocks 的等价实现：递归找最长匹配块 → 排序 → 合并相邻块 → 返回覆盖总长。 */
function matchingBlockTotal(a: string, b: string): number {
  const la = a.length;
  const lb = b.length;
  // b2j：b 中每个字符出现的索引列表（autojunk=False，不做流行元素剔除）
  const b2j = new Map<string, number[]>();
  for (let j = 0; j < lb; j++) {
    const arr = b2j.get(b[j]);
    if (arr) arr.push(j);
    else b2j.set(b[j], [j]);
  }
  const blocks: [number, number, number][] = [];
  const queue: [number, number, number, number][] = [[0, la, 0, lb]];
  while (queue.length) {
    const [alo, ahi, blo, bhi] = queue.pop()!;
    const m = findLongestMatch(a, b, b2j, alo, ahi, blo, bhi);
    if (m[2] > 0) {
      blocks.push(m);
      if (alo < m[0] && blo < m[1]) queue.push([alo, m[0], blo, m[1]]);
      if (m[0] + m[2] < ahi && m[1] + m[2] < bhi) queue.push([m[0] + m[2], ahi, m[1] + m[2], bhi]);
    }
  }
  blocks.sort((x, y) => x[0] - y[0] || x[1] - y[1] || x[2] - y[2]);
  let total = 0;
  let i1 = -1;
  let j1 = -1;
  let k1 = 0;
  for (const [i2, j2, k2] of blocks) {
    if (i1 + k1 === i2 && j1 + k1 === j2) {
      k1 += k2; // 相邻块合并（与 CPython collapse 一致）
    } else {
      total += k1;
      i1 = i2;
      j1 = j2;
      k1 = k2;
    }
  }
  return total + k1;
}

/** difflib.find_longest_match 的等价实现（无 junk 变体）：j2len 动态规划找最长连续匹配块，
 *  再向两侧延伸（autojunk=False 时延伸理论上是 no-op，保留以与 CPython 逐位一致）。 */
function findLongestMatch(
  a: string,
  b: string,
  b2j: Map<string, number[]>,
  alo: number,
  ahi: number,
  blo: number,
  bhi: number,
): [number, number, number] {
  let besti = alo;
  let bestj = blo;
  let bestsize = 0;
  let j2len = new Map<number, number>();
  for (let i = alo; i < ahi; i++) {
    const newj2len = new Map<number, number>();
    const js = b2j.get(a[i]);
    if (js) {
      for (const j of js) {
        if (j < blo) continue;
        if (j >= bhi) break;
        const k = (j2len.get(j - 1) ?? 0) + 1;
        newj2len.set(j, k);
        if (k > bestsize) {
          besti = i - k + 1;
          bestj = j - k + 1;
          bestsize = k;
        }
      }
    }
    j2len = newj2len;
  }
  while (besti > alo && bestj > blo && a[besti - 1] === b[bestj - 1]) {
    besti--;
    bestj--;
    bestsize++;
  }
  while (besti + bestsize < ahi && bestj + bestsize < bhi && a[besti + bestsize] === b[bestj + bestsize]) {
    bestsize++;
  }
  return [besti, bestj, bestsize];
}
