"""应用工厂 + lifespan + 中间件链（RequestID → 幂等/user_id 检查 → 异常归一 → X-New-Token 回传）。"""

import json
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .api.v1.router import api_router
from .core.config import get_settings
from .core.errors import AppError, app_error_handler, unhandled_error_handler
from .core.logging import setup_logging
from .db.engine import get_sessionmaker

logger = logging.getLogger("wordtype")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    from .db.init_db import init_db

    await init_db()
    scheduler = None
    try:
        from .tasks.scheduler import start_scheduler

        scheduler = start_scheduler()
    except Exception as e:  # noqa: BLE001
        logger.warning("scheduler disabled: %s", e)
    logger.info("%s v%s started", get_settings().app_name, get_settings().version)
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    app = FastAPI(title=get_settings().app_name, version=get_settings().version, lifespan=lifespan)
    s = get_settings()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-New-Token", "X-Idempotent-Replay", "X-Request-ID"],
    )

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    @app.middleware("http")
    async def middleware_chain(request: Request, call_next):
        # X-Request-ID 全链路追踪
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        # user_id 红线：body/query 携带 user_id → 400
        if request.method in ("POST", "PUT", "PATCH") and "application/json" in (request.headers.get("content-type") or ""):
            body = await request.body()
            if body:
                try:
                    data = json.loads(body)
                    if isinstance(data, dict) and "user_id" in data:
                        return JSONResponse(status_code=400, content={"code": "USER_ID_FORBIDDEN", "message": "请求体不允许携带 user_id", "detail": []})
                except json.JSONDecodeError:
                    pass
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if getattr(request.state, "idempotent_replay", False):
            response.headers["X-Idempotent-Replay"] = "true"
        # 滑动续期：依赖注入设置的 X-New-Token
        if hasattr(request.state, "new_token"):
            response.headers["X-New-Token"] = request.state.new_token
        return response

    @app.get("/api/health")
    async def health():
        db_ok = False
        try:
            async with get_sessionmaker()() as session:
                await session.execute(text("SELECT 1"))
            db_ok = True
        except Exception:  # noqa: BLE001
            pass
        return {
            "status": "ok" if db_ok else "degraded",
            "db": "ok" if db_ok else "error",
            "scheduler": "alive",
            "version": s.version,
        }

    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
