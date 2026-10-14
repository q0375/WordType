"""口径常量唯一真相源（single source of truth）。

收录「时限 / 计分 / 宽松度 / 难度」四类口径的默认值与基值。
调整口径时**只改本文件**，其余模块一律从此处导入，禁止就地写字面量；
前端对应 ``frontend/src/core/gameRules.ts``（游戏）与 ``core/judge.ts``（判定），
跨语言无法共享代码，靠对拍脚本保证同源。

- 配套文档：``docs/口径总纲.md``（公式、生效范围、覆盖优先级、改动检查清单）
- 配套对拍：``.workbuddy/tools/judge_sync/``（判定）、``.workbuddy/tools/game_sync/``（游戏时限与计分）
- 改口径必跑：``.workbuddy/tools/game_sync/verify_limit_difficulty.py`` 与 ``verify_game_rules.py``，再重建前端 dist。

2026-09-19 / 2026-09-20 / 2026-09-21 口径变更（用户定稿）：
- 考核与游戏的**得分比为连续值**：序列相似度（2026-09-20 起 difflib SequenceMatcher，区分大小写），
  写死在 ``judge.partial_ratio``，不再有 correct/near/wrong 三档得分比配置（已删除）。
- 考核「每词限时」档位（宽松/标准/紧凑）：2026-09-20 取消，2026-09-21 应用户要求**恢复**——
  倍数固定 1.5 / 1.0 / 0.7（写死本文件，不再做管理端可配），**仅作用于每题倒计时**；
  计分权重始终用原始时限 ``base_limit_s``，档位不影响卷面分。
- 游戏时限公式改为 ``ceil(词长 / 3 / 难度系数)``，难度系数 easy 0.8 / normal 1.0 / hard 1.25；
  单词得分 = ``ceil(得分比 × 时限 × Combo 倍率)``（去掉旧的每秒基准分）。

注意：考核打字题与游戏下坠的基值仍各自独立（语义不同、可分别调整）。
考核另有选择题固定限时与「词库时限秒」覆盖机制，游戏没有。
"""

# ==================== 判定 / 宽松度 ====================

# 学习/练习/复习：宽松模式下的固定容差（个字符）。
# 考核与游戏不做「近似」判定，得分比完全由 judge.partial_ratio 决定。
DEFAULT_JUDGE_TOLERANCE = 1

# ==================== 考核：时限 ====================

# 每词限时档位（2026-09-21 恢复）：宽松 / 标准 / 紧凑，倍数写死（历史值，原 exam_time_factors）。
# 仅作用于每题倒计时 limit_s = ceil(基准时限 × 倍数)；计分权重始终用原始时限 base_limit_s，
# 档位不影响卷面分（用户定稿 2026-09-21：无论选哪档，得分按原始时限算）。
EXAM_TIME_POLICIES = {"relaxed": 1.5, "standard": 1.0, "tight": 0.7}
EXAM_TIME_POLICY_DEFAULT = "standard"

# 打字题基值（即「原始时限」）：base + per_char × 字符数
# （单词若设了 time_limit_s，则以该值替换 base）
EXAM_TYPING_BASE_S = 5.0
EXAM_TYPING_PER_CHAR_S = 0.75

# 选择题固定基值（即原始时限），不随词长
EXAM_CHOICE_BASE_S = 10.0

# 单题限时下限（秒），作用于每题最终倒计时
EXAM_MIN_LIMIT_S = 3

# ==================== 游戏：下坠时限 ====================

# 时限 = ceil(字母数 / GAME_FALL_LEN_DIVISOR / 难度系数)，下限 GAME_MIN_FALL_S
# （公式本身算出的值可能小于 2 秒，短词打不完，故保留保底）
GAME_FALL_LEN_DIVISOR = 3.0
GAME_MIN_FALL_S = 2.0
# 词库「时限秒」不参与游戏下坠（仅考核打字题使用）

# 难度系数（写死在代码，管理端不可配；越大时限越短、落得越快）
# 简单 0.8 → 时限 ×1.25；普通 1.0；困难 1.25 → 时限 ×0.8
GAME_DIFF_FACTOR = {"easy": 0.8, "normal": 1.0, "hard": 1.25}

# ==================== 游戏：计分 ====================

# 单词得分 = ceil(得分比 × 落地时限 × Combo 倍率)；得分比见 judge.partial_ratio
# Combo 阶梯（连击数下限 → 倍率），按降序匹配
GAME_COMBO_STEPS = ((20, 3.0), (15, 2.5), (10, 2.0), (5, 1.5))
GAME_COMBO_BASE_MULT = 1.0

# 血条（2026-09-21 起服务端也参与重算）：上限 500 点、掉血 = round((1-得分比) × 100)
# （四舍五入半进位，与前端 gameRules.damageOf 的 Math.round 同口径）
GAME_HP_MAX = 500
GAME_HP_DAMAGE_UNIT = 100

# 限时模式（3 分钟）到时结算：剩余生命 1:1 作为奖励计入总得分。
# 服务端按词日志重算剩余生命（HP_MAX − Σ 掉血），仅 mode=timed 且有效时长 ≥ 本值才给奖励
# （中途退出 / 未到时不给；血尽时剩余为 0 自然无奖励）。
GAME_TIMED_LIMIT_MS = 180_000
