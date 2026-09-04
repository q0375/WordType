"""Repository 基类：隔离下沉 —— 强制 user_id 过滤，业务代码禁止裸查。"""

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import AppError


class UserRepository:
    """所有按用户隔离的数据访问必须经由本基类（显式携带 user_id）。"""

    def __init__(self, db: AsyncSession, user_id: int):
        self.db = db
        self.user_id = user_id

    def scoped(self, stmt: Select, model_user_col) -> Select:
        return stmt.where(model_user_col == self.user_id)

    async def scalar_or_404(self, stmt: Select, what: str):
        row = (await self.db.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise AppError("NOT_FOUND", f"{what}不存在")
        return row


async def paginate(db: AsyncSession, stmt: Select, page: int, page_size: int) -> tuple[list, int]:
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return list(rows), int(total)
