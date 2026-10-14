"""考核计分：卷面分 = Σ(得分比 × 原始时限) / Σ(原始时限) × 100。

得分比 = **连续值**（序列相似度 difflib，2026-09-20 换算法，大小写敏感），由
``judge.partial_ratio`` 逐题计算——2026-09-19 起不再有「正确 / 近似 / 错误」
三档得分比配置（已随「考核宽松判定」一并取消）。

权重用**原始时限**（不含档位倍数）：无论每词限时选宽松 / 标准 / 紧凑，
卷面分不变——档位只影响每题倒计时。
按限时加权：长词、慢题在本卷中权重更高，学生把时间花在难题上不吃亏。

配套文档：docs/口径总纲.md
"""


def score_exam(
    per_question_ratio: list[float],
    limit_weights: list[float] | None = None,
) -> tuple[float, float]:
    """加权卷面分。

    per_question_ratio：每题得分比（0~1）
    limit_weights：每题原始时限秒数（base_limit_s）；缺失或全为 0 时退化为等权（算术平均）

    返回 (score, weighted_rate)：score 为百分制（保留 1 位小数），weighted_rate 为 0~1 的加权得分比。
    """
    n = len(per_question_ratio)
    if n == 0:
        return 0.0, 0.0
    if limit_weights and len(limit_weights) == n:
        total_w = sum(float(w) for w in limit_weights)
    else:
        total_w = 0.0
    if total_w <= 0:
        rate = sum(per_question_ratio) / n
    else:
        rate = sum(float(r) * float(w) for r, w in zip(per_question_ratio, limit_weights)) / total_w
    return round(rate * 100, 1), rate
