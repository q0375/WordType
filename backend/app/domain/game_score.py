"""游戏计分复核（口径19）：base = 10 + 词长×2 + 高度加成（服务端不可得，按 0 计入复核值，
与客户端总分偏差 >5% 时以服务端值为准）。Combo 阶梯：5→1.5 / 10→2.0 / 15→2.5 / 20→3.0。"""


def combo_multiplier(combo: int) -> float:
    if combo >= 20:
        return 3.0
    if combo >= 15:
        return 2.5
    if combo >= 10:
        return 2.0
    if combo >= 5:
        return 1.5
    return 1.0


def recompute_score(word_results: list[dict]) -> int:
    """word_results: 顺序正确的 [{destroyed: bool, word_len: int}]，按销毁顺序累计。"""
    score = 0
    combo = 0
    for w in word_results:
        if w.get("destroyed"):
            combo += 1
            score += round((10 + w["word_len"] * 2) * combo_multiplier(combo))
        else:
            combo = 0
    return score
