"""幂等中心（D9/D19/口径24）：头幂等 + 单题 request_id，24h 去重，重放 200 + X-Idempotent-Replay。"""

import json
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import IdempotencyRecord

WINDOW_HOURS = 24


async def find_replay(db: AsyncSession, key: str, endpoint: str, user_id: int, request=None) -> dict | None:
    """命中返回首次响应快照（dict），否则 None；命中时标记 request.state 供中间件加响应头。"""
    if not key:
        return None
    row = (
        await db.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.key == key,
                IdempotencyRecord.endpoint == endpoint,
                IdempotencyRecord.user_id == user_id,
                IdempotencyRecord.created_at >= datetime.now() - timedelta(hours=WINDOW_HOURS),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if request is not None:
        request.state.idempotent_replay = True
    return json.loads(row.response_snapshot)


async def save_snapshot(db: AsyncSession, key: str, endpoint: str, user_id: int, status_code: int, payload: dict) -> None:
    if not key:
        return
    db.add(
        IdempotencyRecord(
            key=key,
            endpoint=endpoint,
            user_id=user_id,
            status_code=status_code,
            response_snapshot=json.dumps(payload, ensure_ascii=False),
            created_at=datetime.now(),
        )
    )
