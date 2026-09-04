"""统一异常 → {code, message, detail}，语义化状态码（接口规范 §1.2/§8）。"""

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

STATUS_BY_CODE: dict[str, int] = {
    "VALIDATION_ERROR": 422,
    "USER_ID_FORBIDDEN": 400,
    "AUTH_REQUIRED": 401,
    "AUTH_EXPIRED": 401,
    "AUTH_PWD_CHANGED": 401,
    "AUTH_DEACTIVATED": 401,
    "AUTH_INVALID": 401,
    "ADMIN_REQUIRED": 403,
    "FORBIDDEN": 403,
    "USERNAME_TAKEN": 409,
    "INVITE_CODE_INVALID": 422,
    "NOT_FOUND": 404,
    "WORD_DUPLICATE": 409,
    "FILE_TOO_LARGE": 413,
    "FILE_TYPE_FORBIDDEN": 415,
    "ROW_LIMIT_EXCEEDED": 422,
    "PREVIEW_EXPIRED": 410,
    "QUOTA_EXCEEDED": 409,
    "QUEUE_ITEM_NOT_PENDING": 409,
    "EXAM_ALREADY_ACTIVE": 409,
    "EXAM_ALREADY_SUBMITTED": 409,
    "SESSION_EXPIRED": 410,
    "SESSION_ALREADY_SETTLED": 409,
    "CHEAT_DETECTED": 422,
    "EMPTY_WORD_POOL": 422,
    "ADVICE_REFRESH_LIMIT": 429,
    "RATE_LIMITED": 429,
    "DB_BUSY": 503,
    "INTERNAL_ERROR": 500,
}


class AppError(Exception):
    def __init__(self, code: str, message: str, detail: list[dict[str, Any]] | None = None):
        self.code = code
        self.message = message
        self.detail = detail or []
        self.status_code = STATUS_BY_CODE.get(code, 500)
        super().__init__(message)


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "detail": exc.detail},
    )


async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"code": "INTERNAL_ERROR", "message": "服务器内部错误", "detail": []},
    )
