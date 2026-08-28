"""应用路由 —— 对齐 Java 版 AppController，含核心的 SSE 代码生成流。"""

import io
import json
import logging
import math
import zipfile

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.agent import codegen
from app.api.deps import AdminUser, DbSession, LoginUser, RateLimitedUser
from app.api.schemas import (
    AppAddRequest,
    AppAdminUpdateRequest,
    AppDeployRequest,
    AppQueryRequest,
    AppUpdateRequest,
    DeleteRequest,
)
from app.core.paths import GOOD_APP_PRIORITY, CodeGenType
from app.core.response import BusinessException, ErrorCode, ok
from app.db.models import MESSAGE_TYPE_AI, MESSAGE_TYPE_USER
from app.db.session import SessionLocal
from app.services import app_service, chat_history_service, user_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/app", tags=["app"])

MAX_PAGE_SIZE = 20


@router.post("/add")
async def add_app(body: AppAddRequest, db: DbSession, user: LoginUser):
    return ok(await app_service.create_app(db, body.initPrompt, user))


@router.post("/update")
async def update_app(body: AppUpdateRequest, db: DbSession, user: LoginUser):
    app = await app_service.get_by_id(db, body.id)
    if app is None:
        raise BusinessException(ErrorCode.NOT_FOUND_ERROR)
    if app.user_id != user.id:
        raise BusinessException(ErrorCode.NO_AUTH_ERROR)
    return ok(await app_service.update_app(db, app, app_name=body.appName))


@router.post("/delete")
async def delete_app(body: DeleteRequest, db: DbSession, user: LoginUser):
    if body.id <= 0:
        raise BusinessException(ErrorCode.PARAMS_ERROR)
    app = await app_service.get_by_id(db, body.id)
    if app is None:
        raise BusinessException(ErrorCode.NOT_FOUND_ERROR)
    # 仅本人或管理员可删除
    if app.user_id != user.id and not user_service.is_admin(user):
        raise BusinessException(ErrorCode.NO_AUTH_ERROR)
    await chat_history_service.delete_by_app_id(db, app.id)
    return ok(await app_service.remove_by_id(db, body.id))


@router.get("/get/vo")
async def get_app_vo_by_id(id: int, db: DbSession):
    if id <= 0:
        raise BusinessException(ErrorCode.PARAMS_ERROR)
    app = await app_service.get_by_id(db, id)
    if app is None:
        raise BusinessException(ErrorCode.NOT_FOUND_ERROR)
    vo = await app_service.get_app_vo(db, app)
    return ok(vo.model_dump(mode="json"))


async def _page_response(db, query: AppQueryRequest):
    records, total = await app_service.page_apps(db, query)
    vos = await app_service.get_app_vo_list(db, records)
    return ok(
        {
            "records": [vo.model_dump(mode="json") for vo in vos],
            "pageNumber": query.pageNum,
            "pageSize": query.pageSize,
            "totalRow": total,
            "totalPage": math.ceil(total / query.pageSize) if query.pageSize else 0,
        }
    )


@router.post("/my/list/page/vo")
async def list_my_app_vo_by_page(body: AppQueryRequest, db: DbSession, user: LoginUser):
    if body.pageSize > MAX_PAGE_SIZE:
        raise BusinessException(ErrorCode.PARAMS_ERROR, f"每页最多查询 {MAX_PAGE_SIZE} 个应用")
    body.userId = user.id
    return await _page_response(db, body)


@router.post("/good/list/page/vo")
async def list_good_app_vo_by_page(body: AppQueryRequest, db: DbSession):
    if body.pageSize > MAX_PAGE_SIZE:
        raise BusinessException(ErrorCode.PARAMS_ERROR, f"每页最多查询 {MAX_PAGE_SIZE} 个应用")
    body.priority = GOOD_APP_PRIORITY
    return await _page_response(db, body)


@router.post("/admin/delete")
async def delete_app_by_admin(body: DeleteRequest, db: DbSession, _: AdminUser):
    if body.id <= 0:
        raise BusinessException(ErrorCode.PARAMS_ERROR)
    app = await app_service.get_by_id(db, body.id)
    if app is None:
        raise BusinessException(ErrorCode.NOT_FOUND_ERROR)
    return ok(await app_service.remove_by_id(db, body.id))


@router.post("/admin/update")
async def update_app_by_admin(body: AppAdminUpdateRequest, db: DbSession, _: AdminUser):
    app = await app_service.get_by_id(db, body.id)
    if app is None:
        raise BusinessException(ErrorCode.NOT_FOUND_ERROR)
    return ok(
        await app_service.update_app(
            db, app, app_name=body.appName, cover=body.cover, priority=body.priority
        )
    )


@router.post("/admin/list/page/vo")
async def list_app_vo_by_page_by_admin(body: AppQueryRequest, db: DbSession, _: AdminUser):
    return await _page_response(db, body)


@router.get("/admin/get/vo")
async def get_app_vo_by_id_by_admin(id: int, db: DbSession, _: AdminUser):
    app = await app_service.get_by_id(db, id)
    if app is None:
        raise BusinessException(ErrorCode.NOT_FOUND_ERROR)
    vo = await app_service.get_app_vo(db, app)
    return ok(vo.model_dump(mode="json"))


# ---------------------------------------------------------------- SSE 代码生成


@router.get("/chat/gen/code")
async def chat_to_gen_code(appId: int, message: str, db: DbSession, user: RateLimitedUser):
    """流式生成代码。

    事件格式与 Java 版严格一致，前端 ChatPage.vue 手写的 fetch+ReadableStream
    解析逻辑无需改动：每块 `data: {"d":"<chunk>"}`，结束发 `event: done`。
    """
    if appId <= 0:
        raise BusinessException(ErrorCode.PARAMS_ERROR, "应用 ID 不能为空")
    if not message or not message.strip():
        raise BusinessException(ErrorCode.PARAMS_ERROR, "用户消息不能为空")

    app = await app_service.get_by_id(db, appId)
    if app is None:
        raise BusinessException(ErrorCode.NOT_FOUND_ERROR, "应用不存在")
    if app.user_id != user.id:
        raise BusinessException(ErrorCode.NO_AUTH_ERROR, "无权限访问该应用")

    gen_type = CodeGenType.from_value(app.code_gen_type)
    if gen_type is None:
        raise BusinessException(ErrorCode.SYSTEM_ERROR, "不支持的代码生成类型")

    await chat_history_service.add_chat_message(db, appId, message, MESSAGE_TYPE_USER, user.id)

    user_id = user.id

    async def event_stream():
        # 流式响应期间请求作用域的会话已不可用，这里单独开一个用于落库
        collected: list[str] = []
        try:
            async for chunk in codegen.generate_and_save_code_stream(message, gen_type, appId):
                collected.append(chunk)
                yield f"data: {json.dumps({'d': chunk}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception("代码生成失败 appId=%s", appId)
            failure = "\n\n生成失败：" + str(e)
            yield f"data: {json.dumps({'d': failure}, ensure_ascii=False)}\n\n"
        finally:
            # 无论正常结束还是中断，都把已产出的内容落进对话历史
            if collected:
                async with SessionLocal() as session:
                    await chat_history_service.add_chat_message(
                        session, appId, "".join(collected), MESSAGE_TYPE_AI, user_id
                    )
            yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------- 部署 / 下载


@router.post("/deploy")
async def deploy_app(body: AppDeployRequest, db: DbSession, user: LoginUser):
    if body.appId <= 0:
        raise BusinessException(ErrorCode.PARAMS_ERROR, "应用 ID 不能为空")
    app = await app_service.get_by_id(db, body.appId)
    if app is None:
        raise BusinessException(ErrorCode.NOT_FOUND_ERROR, "应用不存在")
    if app.user_id != user.id:
        raise BusinessException(ErrorCode.NO_AUTH_ERROR, "无权限部署该应用")
    return ok(await app_service.deploy_app(db, app))


@router.get("/download/{appId}")
async def download_app_code(appId: int, db: DbSession, user: LoginUser):
    """打包生成目录为 zip 下载。node_modules / dist 体积大且可重建，不打进包。"""
    app = await app_service.get_by_id(db, appId)
    if app is None:
        raise BusinessException(ErrorCode.NOT_FOUND_ERROR, "应用不存在")
    if app.user_id != user.id and not user_service.is_admin(user):
        raise BusinessException(ErrorCode.NO_AUTH_ERROR, "无权限下载该应用")

    source_dir = app_service.source_dir_of(app)
    if not source_dir.is_dir():
        raise BusinessException(ErrorCode.NOT_FOUND_ERROR, "应用代码不存在")

    skip_dirs = {"node_modules", "dist", ".git"}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in source_dir.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(source_dir)
            if skip_dirs & set(rel.parts):
                continue
            zf.write(path, rel.as_posix())
    buffer.seek(0)

    filename = f"{app.code_gen_type}_{app.id}.zip"
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
