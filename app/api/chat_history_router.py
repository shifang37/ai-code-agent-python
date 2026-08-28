"""对话历史路由 —— 对齐 Java 版 ChatHistoryController。"""

import math
from datetime import datetime

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, DbSession, LoginUser
from app.api.schemas import ChatHistoryQueryRequest, ChatHistoryVO
from app.core.response import BusinessException, ErrorCode, ok
from app.services import app_service, chat_history_service, user_service

router = APIRouter(prefix="/chatHistory", tags=["chatHistory"])


@router.get("/app/{appId}")
async def list_app_chat_history(
    appId: int,
    db: DbSession,
    user: LoginUser,
    pageSize: int = Query(default=10),
    lastCreateTime: datetime | None = Query(default=None),
):
    app = await app_service.get_by_id(db, appId)
    if app is None:
        raise BusinessException(ErrorCode.NOT_FOUND_ERROR, "应用不存在")
    # 仅本人或管理员可查看对话历史
    if app.user_id != user.id and not user_service.is_admin(user):
        raise BusinessException(ErrorCode.NO_AUTH_ERROR, "无权限查看该应用的对话历史")

    records, total = await chat_history_service.list_app_chat_history(db, appId, pageSize, lastCreateTime)
    return ok(
        {
            "records": [ChatHistoryVO.model_validate(r).model_dump(mode="json") for r in records],
            "pageNumber": 1,
            "pageSize": pageSize,
            "totalRow": total,
            "totalPage": math.ceil(total / pageSize) if pageSize else 0,
        }
    )


@router.post("/admin/list/page/vo")
async def list_all_chat_history_by_page_for_admin(body: ChatHistoryQueryRequest, db: DbSession, _: AdminUser):
    records, total = await chat_history_service.page_all(db, body)
    return ok(
        {
            "records": [ChatHistoryVO.model_validate(r).model_dump(mode="json") for r in records],
            "pageNumber": body.pageNum,
            "pageSize": body.pageSize,
            "totalRow": total,
            "totalPage": math.ceil(total / body.pageSize) if body.pageSize else 0,
        }
    )
