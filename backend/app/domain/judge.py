"""判定引擎（与前端 core/judge 同规则）。

两个判定口径并存，各模块按需取用：
- ``judge()``：三档判定 correct / near / wrong —— 学习 / 练习 / 复习使用
  （宽松模式容差固定 DEFAULT_JUDGE_TOLERANCE，不再可配）。
- ``partial_ratio()``：连续得分比 = 序列相似度（difflib SequenceMatcher）—— **考核与游戏使用**，
  区分大小写，空格等一切字符参与比较，不做「近似」判定、不再有得分比配置。

``normalize``：仅去首尾空白，**大小写敏感**（2026-09-19 定稿：
"Apple" ≠ "apple"，大写/小写字母一律按错误字符计）。

常量统一取自 domain/defaults.py（口径唯一真相源），不要在本文件或别处写字面量。
配套文档：docs/口径总纲.md
"""

from difflib import SequenceMatcher

from .defaults import DEFAULT_JUDGE_TOLERANCE

__all__ = ["judge", "partial_ratio", "normalize", "levenshtein", "DEFAULT_JUDGE_TOLERANCE"]


def normalize(s: str) -> str:
    """仅去首尾空白；**大小写敏感**（2026-09-19：大写/小写字母算错误字符）。"""
    return (s or "").strip()


def levenshtein(a: str, b: str, max_dist: int = 1) -> int:
    if abs(len(a) - len(b)) > max_dist:
        return max_dist + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > max_dist:
            return max_dist + 1
        prev = cur
    return prev[-1]


def judge(typed: str, spelling: str, loose: bool = True, tolerance: int | None = None) -> str:
    """返回 correct | near | wrong（学习 / 练习 / 复习口径）。

    normalize 仅去首尾空白，大小写敏感（"Apple" ≠ "apple"）；宽松模式容差固定
    DEFAULT_JUDGE_TOLERANCE（1 个字符）。loose=False 时只认完全匹配。
    """
    t, s = normalize(typed), normalize(spelling)
    if t == s:
        return "correct"
    if loose and t:
        tol = tolerance if tolerance is not None else DEFAULT_JUDGE_TOLERANCE
        if tol > 0 and levenshtein(t, s, tol) <= tol:
            return "near"
    return "wrong"


def partial_ratio(typed: str, spelling: str) -> float:
    """连续得分比 = 序列相似度（考核 / 游戏口径）。

    2026-09-20 换算法（用户定稿）：``difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()``，
    即 2 × 最长匹配块覆盖总长 ÷ 两串长度之和。区分大小写、空格等一切字符原样参与比较，
    不做 normalize；差异只拉低局部（"Hello World" vs "ello World" → 0.952），
    不再像逐位比较那样一个错位导致后面全部算错。
    空串 vs 空串 → 1.0、单边为空 → 0.0（difflib 原生语义）。
    前端 core/judge.ts 有逐位等价的 TS 复刻，改动必须两端同步 + 跑对拍脚本。
    """
    return SequenceMatcher(None, typed or "", spelling or "", autojunk=False).ratio()
