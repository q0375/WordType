"""按词限时（D35）：长词限时更长，短词更短；同一单词在不同题型（模式）下限时不同。

限时档位（宽松/标准/紧凑）2026-09-20 取消、2026-09-21 应用户要求恢复：
- 每题倒计时 = ceil(基准时限 × 档位倍数)，下限 EXAM_MIN_LIMIT_S；
  倍数宽松 1.5 / 标准 1.0 / 紧凑 0.7（``defaults.EXAM_TIME_POLICIES``，写死）。
- **计分权重不受档位影响**：始终用原始时限 ``question_base_limit_s``
  （payload 里的 ``base_limit_s``）——选哪档都按原始时限算分（用户定稿 2026-09-21）。

公式（常量见 domain/defaults.py）：
- 打字类（默写/挖空/听音）：base = EXAM_TYPING_BASE_S + EXAM_TYPING_PER_CHAR_S × 字符数
- 选择类（四选一，点击即答）：固定 EXAM_CHOICE_BASE_S
- 单词若设了 time_limit_s，则以该值替换打字题 base；选择题不受影响

⚠ 游戏下坠时限用的是**另一套独立公式**（ceil(词长/3/难度系数)），见 domain/game_score.py；
词库「时限秒」不参与游戏下坠。

配套文档：docs/口径总纲.md
"""

from .defaults import (
    EXAM_CHOICE_BASE_S,
    EXAM_MIN_LIMIT_S,
    EXAM_TIME_POLICIES,
    EXAM_TIME_POLICY_DEFAULT,
    EXAM_TYPING_BASE_S,
    EXAM_TYPING_PER_CHAR_S,
)

__all__ = ["question_base_limit_s", "question_limit_s", "time_policy_factor"]


def question_base_limit_s(qtype: str, spelling: str, override_base_s: int | None = None) -> float:
    """基准时限（秒，float）：**原始时限**，计分权重的唯一来源。

    override_base_s：管理员给该词设置的打字题基础秒数（替换 5+0.75×词长 的 base）；
    选择题不受 override 影响。
    """
    if qtype == "choice":
        return float(EXAM_CHOICE_BASE_S)
    if override_base_s:
        return max(float(EXAM_MIN_LIMIT_S), float(override_base_s))
    return EXAM_TYPING_BASE_S + EXAM_TYPING_PER_CHAR_S * max(len(spelling), 1)


def time_policy_factor(policy: str | None) -> float:
    """限时档位倍数：宽松 1.5 / 标准 1.0 / 紧凑 0.7；未知值回落标准档。"""
    return EXAM_TIME_POLICIES.get(policy or EXAM_TIME_POLICY_DEFAULT, EXAM_TIME_POLICIES[EXAM_TIME_POLICY_DEFAULT])


def question_limit_s(
    qtype: str,
    spelling: str,
    override_base_s: int | None = None,
    policy: str = EXAM_TIME_POLICY_DEFAULT,
) -> int:
    """单题实际倒计时秒数 = ceil(基准时限 × 档位倍数)，最少 EXAM_MIN_LIMIT_S 秒。

    档位仅影响倒计时；计分权重仍是原始时限 ``question_base_limit_s``。
    """
    raw = question_base_limit_s(qtype, spelling, override_base_s) * time_policy_factor(policy)
    return max(EXAM_MIN_LIMIT_S, int(raw + 0.999))
