"""判定引擎（与前端 core/judge 同规则）：normalize → 严格完全匹配 / 宽松 Levenshtein≤1 判近似。"""


def normalize(s: str) -> str:
    return (s or "").strip().lower()


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


def judge(typed: str, spelling: str, loose: bool = True) -> str:
    """返回 correct | near | wrong。"""
    t, s = normalize(typed), normalize(spelling)
    if t == s:
        return "correct"
    if loose and t and levenshtein(t, s, 1) <= 1:
        return "near"
    return "wrong"
