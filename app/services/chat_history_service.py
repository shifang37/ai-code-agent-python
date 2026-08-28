"""对话历史服务 —— 移植 service/impl/ChatHistoryServiceImpl。

与 Java 版的关键差异：这张表**不再兼任 Agent 记忆源**。
Java 版要靠 `loadChatHistoryToMemory` 把最近 20 条倒灌回 langchain4j 的滑动窗口
（还得小心翼翼地跳过最新一条再 reverse）。Python 版由 LangGraph checkpointer
按 `thread_id = appId` 持有对话状态，这张表只负责给前端做分页展示。
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ChatHistoryQueryRequest
from app.core.response import BusinessException, ErrorCode
from app.db.models import ChatHistory
from app.utils.snowflake import next_id


async def add_chat_message(db: AsyncSession, app_id: int, message: str, message_type: str, user_id: int) -> None:
    if not message or not message.strip():
        return
    db.add(
        ChatHistory(
            id=next_id(),
            message=message,
            message_type=message_type,
            app_id=app_id,
            user_id=user_id,
            is_delete=0,
        )
    )
    await db.commit()


async def list_app_chat_history(
    db: AsyncSession,
    app_id: int,
    page_size: int,
    last_create_time: datetime | None,
) -> tuple[list[ChatHistory], int]:
    """游标分页：以 createTime 为游标向更早的方向翻页。"""
    if app_id <= 0:
        raise BusinessException(ErrorCode.PARAMS_ERROR, "应用 ID 不能为空")
    if page_size <= 0 or page_size > 50:
        raise BusinessException(ErrorCode.PARAMS_ERROR, "每页数量必须在 1-50 之间")

    stmt = select(ChatHistory).where(ChatHistory.app_id == app_id, ChatHistory.is_delete == 0)
    if last_create_time is not None:
        stmt = stmt.where(ChatHistory.create_time < last_create_time)

    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = stmt.order_by(ChatHistory.create_time.desc()).limit(page_size)
    return list((await db.scalars(stmt)).all()), total


async def delete_by_app_id(db: AsyncSession, app_id: int) -> None:
    """应用删除时一并清掉它的对话历史（逻辑删除）。"""
    rows = (await db.scalars(select(ChatHistory).where(ChatHistory.app_id == app_id, ChatHistory.is_delete == 0))).all()
    for row in rows:
        row.is_delete = 1
    await db.commit()


async def page_all(db: AsyncSession, query: ChatHistoryQueryRequest) -> tuple[list[ChatHistory], int]:
    stmt = select(ChatHistory).where(ChatHistory.is_delete == 0)
    if query.id is not None:
        stmt = stmt.where(ChatHistory.id == query.id)
    if query.appId is not None:
        stmt = stmt.where(ChatHistory.app_id == query.appId)
    if query.userId is not None:
        stmt = stmt.where(ChatHistory.user_id == query.userId)
    if query.messageType:
        stmt = stmt.where(ChatHistory.message_type == query.messageType)
    if query.message:
        stmt = stmt.where(ChatHistory.message.like(f"%{query.message}%"))
    if query.lastCreateTime is not None:
        stmt = stmt.where(ChatHistory.create_time < query.lastCreateTime)

    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = stmt.order_by(ChatHistory.create_time.desc())
    stmt = stmt.offset((query.pageNum - 1) * query.pageSize).limit(query.pageSize)
    return list((await db.scalars(stmt)).all()), total
