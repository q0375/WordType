"""系统级配置（system_setting 表）读写助手（仅管理员可写）。

活跃配置项：
- game_fall_duration_mult：游戏下坠倍数（出现 → 消失 = 时限 × N），默认 2，
  管理员经 PUT /admin/game-config/fall-mult 修改，随 /settings 下发给全端。

已固化不可配（历史）：
- 考核「每词限时倍数」随限时档位（宽松/标准/紧凑）一并取消——每词倒计时统一按基准时限；
- 得分比与分级容差更早取消（2026-09-19，改为连续字符正确率）。
全部口径固化在 domain/defaults.py（唯一真相源）。

配套文档：docs/口径总纲.md
"""

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.engine import now_str
from ..models import SystemSetting

# 历史遗留键（已废弃，保留常量仅供清理脚本引用）
KEY_TIME_FACTORS_LEGACY = "exam_time_factors"
KEY_SCORE_RATIOS_LEGACY = "exam_score_ratios"
KEY_TOLERANCE_RULES_LEGACY = "exam_tolerance_rules"

# 游戏下坠倍数（2026-09-20 起可配）
KEY_GAME_FALL_MULT = "game_fall_duration_mult"
GAME_FALL_MULT_DEFAULT = 2.0
GAME_FALL_MULT_MIN = 1.0
GAME_FALL_MULT_MAX = 10.0


async def get_game_fall_mult(db: AsyncSession) -> float:
    """读取下坠倍数；无记录或值非法时回落默认 2。"""
    v = await _get_json(db, KEY_GAME_FALL_MULT)
    try:
        m = float(v.get("mult")) if isinstance(v, dict) else None
    except (TypeError, ValueError):
        m = None
    if m is None or not (GAME_FALL_MULT_MIN <= m <= GAME_FALL_MULT_MAX):
        return GAME_FALL_MULT_DEFAULT
    return m


async def set_game_fall_mult(db: AsyncSession, mult: float) -> None:
    await _set_json(db, KEY_GAME_FALL_MULT, {"mult": float(mult)})


async def _get_json(db: AsyncSession, key: str) -> dict | None:
    row = (await db.execute(select(SystemSetting).where(SystemSetting.key == key))).scalar_one_or_none()
    if row is None:
        return None
    try:
        v = json.loads(row.value_json)
    except Exception:
        return None
    return v if isinstance(v, dict) else None


async def _set_json(db: AsyncSession, key: str, value: dict) -> None:
    payload = json.dumps(value, ensure_ascii=False)
    row = (await db.execute(select(SystemSetting).where(SystemSetting.key == key))).scalar_one_or_none()
    if row is None:
        db.add(SystemSetting(key=key, value_json=payload, updated_at=now_str()))
    else:
        row.value_json = payload
        row.updated_at = now_str()
    await db.flush()
