"""游戏计分（得分比 × 下坠时限 × Combo）。

- 时限（秒）：``ceil(字母数 / GAME_FALL_LEN_DIVISOR / 难度系数)``，下限 GAME_MIN_FALL_S
  （难度系数 简单 0.8 / 普通 1.0 / 困难 1.25 —— 越大时限越短、落得越快）。
  与页面 fallSeconds 严格同公式，见 frontend/src/core/gameRules.ts。
  词库导入的「时限秒」不参与游戏下坠与计分。
- 得分比：**连续值** `judge.partial_ratio`（序列相似度 difflib，2026-09-20 换算法，大小写敏感）；
  输入完成即按此销毁结算，不再有 correct/near/wrong 三档、不再读取任何得分比配置。
- 单词得分 = ceil(得分比 × 时限 × Combo 倍率)；落地/放弃不计 Combo（倍率 GAME_COMBO_BASE_MULT）。
- Combo 阶梯：5→1.5 / 10→2.0 / 15→2.5 / 20→3.0。
- 漏词扣分（2026-09-20 新增）：触线落地另扣 ceil((1-得分比) × 时限)（fall_penalty，前端同式）；
  中途放弃不扣分。
- 血条：上限 500 点、掉血 =(1-得分比)×100（round 半进位），常量在 defaults.py（GAME_HP_*）。
  掉血扣减仍在前端实时表现；**服务端在结算时按词日志同步重算剩余生命**（hp_damage / timed_hp_bonus），
  用于限时模式的生命奖励：到时（有效时长 ≥ 3 分钟）结算时剩余生命 1:1 计入总得分（2026-09-21 新增）。

常量统一取自 domain/defaults.py（口径唯一真相源）。
改口径必跑 .workbuddy/tools/game_sync/ 下对拍脚本，并重建前端 dist。
配套文档：docs/口径总纲.md
"""

import math

from .defaults import (
    GAME_COMBO_BASE_MULT,
    GAME_COMBO_STEPS,
    GAME_DIFF_FACTOR,
    GAME_FALL_LEN_DIVISOR,
    GAME_HP_DAMAGE_UNIT,
    GAME_HP_MAX,
    GAME_MIN_FALL_S,
    GAME_TIMED_LIMIT_MS,
)
from .judge import partial_ratio

__all__ = ["combo_multiplier", "fall_seconds", "partial_ratio", "word_score", "fall_penalty",
           "hp_damage", "timed_hp_bonus", "recompute_score", "GAME_DIFF_FACTOR"]


def combo_multiplier(combo: int) -> float:
    """连击数 → 倍率（阶梯见 GAME_COMBO_STEPS，按降序匹配）。"""
    for threshold, mult in GAME_COMBO_STEPS:
        if combo >= threshold:
            return mult
    return GAME_COMBO_BASE_MULT


def fall_seconds(spelling: str, difficulty: str = "normal") -> float:
    """该词从出生到红线的落地秒数（=该题时限）：ceil(字母数 / 3 / 难度系数)，下限 2 秒。"""
    factor = GAME_DIFF_FACTOR.get(difficulty, 1.0)
    n = max(len(spelling or ""), 1)
    return float(max(GAME_MIN_FALL_S, math.ceil(n / GAME_FALL_LEN_DIVISOR / factor)))


def word_score(ratio: float, limit_s: float, combo_mult: float = 1.0) -> int:
    # 上取整：与前端 Math.ceil 同口径，避免服务端复核出现 ±1 偏差
    return int(math.ceil(float(ratio) * float(limit_s) * float(combo_mult)))


def fall_penalty(ratio: float, limit_s: float) -> int:
    """漏词扣分 = ceil((1 − 得分比) × 时限)（2026-09-20 新增，与前端 gameRules.fallPenalty 同口径）。

    词触红线除扣血（纯前端）外同步扣分：没碰的词扣 ceil(时限) 分（2~5 分）；
    已输入部分分照常保留（净变化 = word_score(ratio) − fall_penalty(ratio)）。
    """
    return int(math.ceil((1.0 - float(ratio)) * float(limit_s)))


def hp_damage(ratio: float) -> int:
    """单事件掉血 = round((1 − 得分比) × 100)，下限 0（2026-09-21 起服务端同步重算血量）。

    与前端 gameRules.damageOf 同口径：Math.round 为「半进位」，
    Python 的 round() 是银行家舍入（0.5 取偶），必须用 floor(x+0.5) 复刻，避免 ±1 漂移。
    """
    return max(0, int(math.floor((1.0 - float(ratio)) * GAME_HP_DAMAGE_UNIT + 0.5)))


def timed_hp_bonus(word_results: list[dict], mode: str, duration_ms: int) -> int:
    """限时模式到时结算的生命奖励（2026-09-21 新增，用户定稿）。

    剩余生命 1:1 计入总得分：HP_MAX − Σ 掉血（销毁/落地/放弃三类事件同口径掉血，
    满分销毁不掉血）。仅 mode=timed 且有效时长 ≥ GAME_TIMED_LIMIT_MS（3 分钟）才给：
    中途退出未到时不给；血尽时剩余为 0 自然无奖励。
    服务端从词日志完整重算，不信任客户端上报的血量。
    """
    if mode != "timed" or int(duration_ms) < GAME_TIMED_LIMIT_MS:
        return 0
    used = sum(hp_damage(w.get("ratio", 0.0)) for w in word_results)
    return max(0, GAME_HP_MAX - used)


def recompute_score(word_results: list[dict]) -> int:
    """服务端复核：word_results 按结算顺序 [{destroyed, landed, ratio, limit_s}]。

    - 销毁计入 Combo 阶梯；
    - 触线落地（landed=True）：Combo 清零、1.0 倍率计部分分，**另按缺口扣分** fall_penalty；
    - 中途放弃（landed=False）：Combo 清零、1.0 倍率计部分分，不扣分（仅前端扣血）。
    """
    score = 0
    combo = 0
    for w in word_results:
        if w.get("destroyed"):
            combo += 1
            mult = combo_multiplier(combo)
            score += word_score(w.get("ratio", 0.0), w.get("limit_s", 0.0), mult)
        else:
            combo = 0
            ratio = w.get("ratio", 0.0)
            limit_s = w.get("limit_s", 0.0)
            score += word_score(ratio, limit_s, GAME_COMBO_BASE_MULT)
            if w.get("landed"):
                score -= fall_penalty(ratio, limit_s)
    return score
