"""SM-2 + 熟练度增量（DBD §4.2）+ sync_lifecycle（§3.3）+ 错题本联动。纯函数，无 IO。"""

from datetime import date, timedelta


def plus_days(d: str | date, n: int) -> str:
    if isinstance(d, str):
        d = date.fromisoformat(d)
    return (d + timedelta(days=n)).isoformat()


def apply_study_set(rating: str, today: str) -> dict:
    """D8 自评设值 / D17 默写三态映射（学习模式专用，绝对值设值）。"""
    mapping = {
        "know": {"proficiency": 60, "interval_days": 3},
        "vague": {"proficiency": 30, "interval_days": 1},
        "unknown": {"proficiency": 0, "interval_days": 1},
    }
    # D17 默写：correct→60/+3d, near→30/+1d, wrong→0/+1d
    m = mapping.get(rating)
    if m is None:  # 传入判定三态
        m = {"correct": mapping["know"], "near": mapping["vague"], "wrong": mapping["unknown"]}[rating]
    return {
        "proficiency": m["proficiency"],
        "interval_days": m["interval_days"],
        "next_review_at": plus_days(today, m["interval_days"]),
        "streak_correct": 0,
    }


def apply_review_sm2(stat: dict, result: str, today: str) -> dict:
    """复习路径全量 SM-2（§4.2 表）。stat 为当前行快照 dict。"""
    iv = stat.get("interval_days") or 1
    ease = stat.get("ease_factor") or 2.5
    p = stat.get("proficiency", 0)
    streak = stat.get("streak_correct", 0)
    out: dict = {}
    if result == "correct":
        new_iv = 3 if iv <= 1 else round(iv * ease)
        out = {
            "interval_days": new_iv,
            "ease_factor": min(3.0, ease + 0.05),
            "proficiency": min(100, p + 10 * min(streak + 1, 3)),
            "streak_correct": streak + 1,
            "next_review_at": plus_days(today, new_iv),
        }
    elif result == "near":
        new_iv = max(1, int(iv / 2))
        out = {
            "interval_days": new_iv,
            "ease_factor": max(1.3, ease - 0.1),
            "proficiency": max(0, p - 12),
            "streak_correct": streak,
            "next_review_at": plus_days(today, new_iv),
        }
    else:  # wrong
        out = {
            "interval_days": 1,
            "ease_factor": max(1.3, ease - 0.2),
            "proficiency": max(0, p - 25),
            "streak_correct": 0,
            "next_review_at": plus_days(today, 1),
        }
    return out


def apply_increment(stat: dict, result: str) -> dict:
    """练习/考核/游戏路径：仅 p 与计数增量（N4：不推进 SM-2 间隔）。"""
    p = stat.get("proficiency", 0)
    streak = stat.get("streak_correct", 0)
    out: dict = {}
    if result == "correct":
        out = {
            "proficiency": min(100, p + 10 * min(streak + 1, 3)),
            "streak_correct": streak + 1,
            "correct_count": stat.get("correct_count", 0) + 1,
        }
    elif result == "near":
        out = {
            "proficiency": max(0, p - 12),
            "streak_correct": streak,
            "near_miss_count": stat.get("near_miss_count", 0) + 1,
        }
    else:
        out = {
            "proficiency": max(0, p - 25),
            "streak_correct": 0,
            "wrong_count": stat.get("wrong_count", 0) + 1,
        }
    return out


def sync_lifecycle(result: str, p: int, next_review_at: str | None, interval_days: int | None, today: str) -> dict:
    """任何模式写 stat 后必须调用（DBD §3.3）。"""
    if p >= 80 and next_review_at is not None:
        return {"next_review_at": None}  # ① 掌握退出
    if result == "wrong" and next_review_at is None and interval_days is not None:
        return {"next_review_at": plus_days(today, 1), "interval_days": 1}  # ② D18 回退
    return {}  # ③ 其余不动


def wrongbook_on_result(result: str) -> str:
    """返回错题本动作：add（入本/归零）、conquer（连对+1）、none（近似不动，N1）。"""
    if result == "wrong":
        return "add"
    if result == "correct":
        return "conquer"
    return "none"
