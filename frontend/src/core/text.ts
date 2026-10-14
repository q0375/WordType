/**
 * 例句字段拆分：词库 `word.example` 是「英文句 中文翻译」挤在一个字符串里
 * （词典源数据如此，如 "Jane was praised by her teacher. 简受到了老师的表扬。"）。
 * 挖空题需要英文、中文分两行展示，在第一个 CJK 字符（含全角标点）处切开。
 */
const CJK_RE = /[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]/;

export function splitSentence(example: string | null | undefined): { en: string; cn: string } {
  const s = (example || '').trim();
  if (!s) return { en: '', cn: '' };
  const i = s.search(CJK_RE);
  if (i < 0) return { en: s, cn: '' };
  return { en: s.slice(0, i).trim(), cn: s.slice(i).trim() };
}
