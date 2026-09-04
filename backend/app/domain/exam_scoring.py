"""考核计分 D20：每题 1 分，宽松模式近似 0.5 分，score = round(得分/题量×100)。"""


def score_exam(per_question_raw: list[float], loose: bool) -> tuple[int, float]:
    """per_question_raw: 每题 1/0.5/0（宽松下 near=0.5）。返回 (score, correct_rate)。"""
    n = len(per_question_raw) or 1
    total = sum(per_question_raw)
    return round(total / n * 100), total / n


def question_raw(result: str, loose: bool) -> float:
    if result == "correct":
        return 1.0
    if result == "near" and loose:
        return 0.5
    return 0.0
