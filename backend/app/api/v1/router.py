"""v1 路由装配。"""

from fastapi import APIRouter

from .endpoints import admin, auth, books, challenge, learning, misc

api_router = APIRouter()
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(books.router, tags=["books"])
api_router.include_router(learning.router, tags=["learning"])
api_router.include_router(challenge.router, tags=["exam-game"])
api_router.include_router(misc.router, tags=["misc"])
api_router.include_router(admin.router, tags=["admin"])
