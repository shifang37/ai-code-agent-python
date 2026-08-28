"""FastAPI 应用入口。

对齐 Java 版的对外形态：端口 8123，全部接口挂在 `/api` 前缀下，
响应统一为 `{code, data, message}` 信封 —— 现有 Vue 前端零改动即可切过来。
"""

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    app_router,
    chat_history_router,
    deps,
    static_router,
    user_router,
    workflow_router,
)
from app.core.config import settings
from app.core.paths import ensure_dirs
from app.core.response import ok, register_exception_handlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_dirs()
    redis_client = None
    try:
        import redis.asyncio as aioredis

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        await redis_client.ping()
        deps.set_redis_client(redis_client)
        logger.info("Redis 连接就绪：%s:%s", settings.redis_host, settings.redis_port)
    except Exception as e:
        # Redis 只服务于限流，挂了就降级到内存限流，不该拖垮整个服务启动
        logger.warning("Redis 不可用，限流降级为内存实现：%s", e)
        redis_client = None

    yield

    if redis_client is not None:
        await redis_client.aclose()


app = FastAPI(
    title="AI 代码生成 Agent",
    description="Python / LangChain / LangGraph 实现",
    version="0.1.0",
    lifespan=lifespan,
)

# 前端开发服务器跨域访问；allow_credentials 为 True 时不能用通配源
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

health_router = APIRouter(prefix="/health", tags=["health"])


@health_router.get("/")
async def health():
    return ok("ok")


api = APIRouter(prefix="/api")
api.include_router(health_router)
api.include_router(user_router.router)
api.include_router(app_router.router)
api.include_router(chat_history_router.router)
api.include_router(workflow_router.router)
api.include_router(static_router.router)
app.include_router(api)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.server_port, reload=True)
