"""AI 规则建议引擎（口径20）：固定优先级取前 3 —— 错误率>60% → 高危明日到期 → 连续7天未学 → 指法专项。"""


def build_items(
    high_error_words: list[dict],      # [{word_id, spelling, wrong_count, total_count}]
    danger_due_tomorrow: int,          # 高危且 next_review_at=明日 数量
    inactive_days: int | None,         # 连续未学天数；0=近 7 天活跃；None=无活动记录
    weak_bigrams: list[str],           # letter_stat bigram Top（慢/错）
    new_user: bool = False,            # 零学习历史新用户：给新手引导而非流失召回
) -> list[dict]:
    items: list[dict] = []
    if new_user:
        items.append({
            "type": "onboarding",
            "priority": 0,
            "message": "欢迎！三步开始：① 去词库挑一本公共词库（或导入自己的） ② 学第一组单词 ③ 回到这里看进度。",
            "action": {"type": "books", "params": {}},
        })
        return items[:3]
    if high_error_words:
        ids = [w["word_id"] for w in high_error_words[:20]]
        items.append({
            "type": "high_error",
            "priority": 1,
            "message": f"错误率超过 60% 的词共 {len(high_error_words)} 个，建议立即专项练习巩固。",
            "action": {"type": "practice", "params": {"word_ids": ids}},
        })
    if danger_due_tomorrow > 0:
        items.append({
            "type": "danger_due",
            "priority": 2,
            "message": f"有 {danger_due_tomorrow} 个高危遗忘词即将到期，现在复习效果最佳。",
            "action": {"type": "review", "params": {}},
        })
    if inactive_days is not None and inactive_days >= 7:
        items.append({
            "type": "inactive",
            "priority": 3,
            "message": "连续 7 天未学习，来一份今日 10 分钟轻量计划找回节奏。",
            "action": {"type": "study_plan", "params": {"minutes": 10}},
        })
    if weak_bigrams:
        top = "、".join(weak_bigrams[:5])
        items.append({
            "type": "typing_weak",
            "priority": 4,
            "message": f"指法专项：字母组合 {top} 最慢/最易错，值得针对性训练。",
            "action": {"type": "typing_drill", "params": {"letters": weak_bigrams[:5]}},
        })
    if not items:
        items.append({
            "type": "keep_going",
            "priority": 9,
            "message": "今天已经开动了！完成今日新词额度并清掉到期复习，就是漂亮的一天。",
            "action": {"type": "study_plan", "params": {"minutes": 10}},
        })
    return items[:3]
