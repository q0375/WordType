/**
 * 游戏口径唯一真相源（前端）。
 *
 * 与后端 ``backend/app/domain/defaults.py`` 及 ``app/domain/game_score.py`` 严格同公式；
 * 跨语言无法共享代码，靠 ``.workbuddy/tools/game_sync/`` 下对拍脚本保证同源。
 * 调整口径必须两端同步 + 重建 dist。配套文档：docs/口径总纲.md
 *
 * 2026-09-19 定稿：时限 = ceil(字母数 / 3 / 难度系数)；得分 = ceil(得分比 × 时限 × Combo)；
 * 得分比 = 输入正确的字符数 ÷ 全部字符数（见 core/judge.partialRatio，大小写敏感）；
 * 不再有得分比配置、不再有全局加速。
 * 2026-09-20 改：下坠时长与计分时限解耦——局面可能多词同屏，按「时限」直落太快，
 * 下坠（出现 → 消失）= 时限 × 下坠倍数（管理员全局可配，默认 2）；计分权重仍是时限本身，后端无感。
 * 2026-09-20 改：漏词（触红线）除扣血外同步扣分 fallPenalty（前后端同式，交卷复核一致）。
 */

/** 血条上限与扣血基数：掉血 = (1 - 得分比) × HP_DAMAGE_UNIT，仅前端实现 */
export const HP_MAX = 500;
export const HP_DAMAGE_UNIT = 100;

/** 下坠时限 = ceil(字母数 / FALL_LEN_DIVISOR / 难度系数)，下限 MIN_FALL_S（短词保底） */
export const FALL_LEN_DIVISOR = 3;
export const MIN_FALL_S = 2;

/** 难度系数（写死在代码，管理端不可配；越大时限越短、落得越快） */
export const FALL_DIFF_FACTOR: Record<string, number> = { easy: 0.8, normal: 1.0, hard: 1.25 };

/** Combo 阶梯（连击数下限 → 倍率），按降序匹配 */
export const COMBO_STEPS: ReadonlyArray<readonly [number, number]> = [[20, 3], [15, 2.5], [10, 2], [5, 1.5]];
export const COMBO_BASE_MULT = 1;

/**
 * 难度节奏参数：同屏上限 maxOnScreen、生成间隔 spawnMs。
 * 下落速度不在这里——速度 = 下落距离 ÷ 下坠时长（由 fallDurationSeconds 决定），且不随时间加速。
 */
export const DIFF = {
  easy: { maxOnScreen: 2, spawnMs: 3400 },
  normal: { maxOnScreen: 3, spawnMs: 2400 },
  hard: { maxOnScreen: 4, spawnMs: 1600 },
};

/** 连击数 → 倍率 */
export function comboMultiplier(combo: number): number {
  for (const [threshold, mult] of COMBO_STEPS) if (combo >= threshold) return mult;
  return COMBO_BASE_MULT;
}

/** 该题时限（计分权重）：ceil(字母数 / 3 / 难度系数)，下限 2 秒；词库「时限秒」不参与游戏下坠 */
export function fallSeconds(spelling: string, difficulty: string): number {
  const factor = FALL_DIFF_FACTOR[difficulty] ?? 1;
  return Math.max(MIN_FALL_S, Math.ceil(spelling.length / FALL_LEN_DIVISOR / factor));
}

/** 下坠倍数默认值：出现 → 消失的总秒数 = 时限 × mult（多词同屏放缓节奏；计分时限不受影响）。
 *  实际倍数是管理员全局配置（system_setting.game_fall_duration_mult，随 /settings 下发），此常量仅作缺省兜底。 */
export const FALL_DURATION_MULT = 2;

/** 下坠总秒数（视觉节奏，仅前端动画用；计分权重仍用 fallSeconds）。
 *  mult 缺省/非法（≤0 或非有限数）时回落默认值。 */
export function fallDurationSeconds(spelling: string, difficulty: string, mult?: number | null): number {
  const m = mult && Number.isFinite(mult) && mult > 0 ? mult : FALL_DURATION_MULT;
  return fallSeconds(spelling, difficulty) * m;
}

/** 掉血量：满分不掉血，全错扣满 HP_DAMAGE_UNIT */
export function damageOf(ratio: number): number {
  return Math.max(0, Math.round((1 - ratio) * HP_DAMAGE_UNIT));
}

/** 限时模式总时长（3 分钟）。到时结算：剩余生命 1:1 作为奖励计入总得分——
 *  服务端按词日志重算剩余生命（HP_MAX − Σ damageOf），仅 timed 且有效时长 ≥ 本值才给
 *  （中途退出/未到时不给，血尽剩余 0 自然无奖励）。2026-09-21 用户定稿。 */
export const TIMED_MODE_MS = 180_000;

/** 单词得分 = ceil(得分比 × 时限 × Combo 倍率)（与后端 game_score.word_score 同口径） */
export function wordScore(ratio: number, limitS: number, comboMult = 1): number {
  return Math.ceil(ratio * limitS * comboMult);
}

/** 漏词扣分 = ceil((1 − 得分比) × 时限)（2026-09-20 新增，与后端 game_score.fall_penalty 同口径）。
 *  词触红线除扣血外同步扣分：没碰的词扣 ceil(时限) 分（2~5 分，≈一个完美词的得分）；
 *  已输入部分分照常保留（净变化 = wordScore(ratio) − fallPenalty(ratio)）。 */
export function fallPenalty(ratio: number, limitS: number): number {
  return Math.ceil((1 - ratio) * limitS);
}
