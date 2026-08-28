"""依赖项：登录态、权限校验、限流。

会话方案：Java 版用 Spring Session + Redis + Cookie。这里用签名 Cookie 存 userId
（`itsdangerous`），服务端不存会话——省掉一个 Redis 依赖，语义等价。
Cookie 属性与 Java 版对齐：httponly / samesite=lax / 30 天。
这两条属性不是可选项：httponly 挡住 JS 读 Cookie；samesite=lax 保证沙箱预览页
（opaque origin）发出的跨站请求不带会话，两者共同构成预览隔离的一半。
"""

import logging
import time
from typing import Annotated

from fastapi import Cookie, Depends, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.paths import USER_LOGIN_STATE
from app.core.response import BusinessException, ErrorCode
from app.db.models import User
from app.db.session import get_db
from app.services import user_service

logger = logging.getLogger(__name__)

SESSION_COOKIE = "SESSION"
_serializer = URLSafeTimedSerializer(settings.session_secret, salt=USER_LOGIN_STATE)


def set_login_cookie(response: Response, user_id: int) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        _serializer.dumps(str(user_id)),
        max_age=settings.session_max_age,
        httponly=True,
        samesite="lax",
        path="/",
    )


def clear_login_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_login_user(
    db: DbSession,
    session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> User:
    """取当前登录用户，未登录抛 40100（前端据此跳登录页）。"""
    if not session:
        raise BusinessException(ErrorCode.NOT_LOGIN_ERROR)
    try:
        user_id = int(_serializer.loads(session, max_age=settings.session_max_age))
    except (BadSignature, ValueError):
        raise BusinessException(ErrorCode.NOT_LOGIN_ERROR) from None

    user = await user_service.get_by_id(db, user_id)
    if user is None:
        raise BusinessException(ErrorCode.NOT_LOGIN_ERROR)
    return user


LoginUser = Annotated[User, Depends(get_login_user)]


async def get_admin_user(user: LoginUser) -> User:
    if not user_service.is_admin(user):
        raise BusinessException(ErrorCode.NO_AUTH_ERROR)
    return user


AdminUser = Annotated[User, Depends(get_admin_user)]


# ---------------------------------------------------------------- 限流

# 与 Java 版 @RateLimit(USER, 5 次 / 60 秒) 对齐
CHAT_RATE_LIMIT = 5
CHAT_RATE_WINDOW = 60


class _MemoryRateLimiter:
    """Redis 不可用时的降级实现：单进程内存滑动窗口。

    多副本部署下它只能限住单个副本，属于「聊胜于无」；生产环境应确保 Redis 可用。
    """

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str, limit: int, window: int) -> bool:
        now = time.time()
        hits = [t for t in self._hits.get(key, []) if now - t < window]
        if len(hits) >= limit:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True


_memory_limiter = _MemoryRateLimiter()
_redis_client = None


def set_redis_client(client) -> None:
    global _redis_client
    _redis_client = client


async def check_rate_limit(key: str, limit: int = CHAT_RATE_LIMIT, window: int = CHAT_RATE_WINDOW) -> None:
    """滑动窗口限流。Redis 故障时降级到内存实现，不因限流器挂掉就拒绝服务。"""
    if _redis_client is not None:
        try:
            redis_key = f"rate_limit:{key}"
            count = await _redis_client.incr(redis_key)
            if count == 1:
                await _redis_client.expire(redis_key, window)
            if count > limit:
                raise BusinessException(ErrorCode.TOO_MANY_REQUEST)
            return
        except BusinessException:
            raise
        except Exception:
            logger.warning("Redis 限流不可用，降级到内存限流", exc_info=True)

    if not _memory_limiter.allow(key, limit, window):
        raise BusinessException(ErrorCode.TOO_MANY_REQUEST)


async def rate_limit_chat(request: Request, user: LoginUser) -> User:
    """代码生成接口的限流依赖项，按用户维度计数。"""
    await check_rate_limit(f"chat:{user.id}")
    return user


RateLimitedUser = Annotated[User, Depends(rate_limit_chat)]
