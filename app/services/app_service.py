"""应用服务 —— 移植 service/impl/AppServiceImpl。"""

import logging
import random
import shutil
import string
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.builder import build_project
from app.api.schemas import AppQueryRequest, AppVO
from app.core.paths import (
    CODE_DEPLOY_HOST,
    CODE_DEPLOY_ROOT_DIR,
    CODE_OUTPUT_ROOT_DIR,
    GOOD_APP_PRIORITY,
    CodeGenType,
)
from app.core.response import BusinessException, ErrorCode
from app.db.models import App, User
from app.llm.services import route_code_gen_type
from app.services.user_service import to_user_vo
from app.utils.snowflake import next_id

logger = logging.getLogger(__name__)

DEPLOY_KEY_LENGTH = 6


async def create_app(db: AsyncSession, init_prompt: str, login_user: User) -> int:
    """建应用时就用 LLM 定下生成模式，后续对话不再重新路由。"""
    if not init_prompt or not init_prompt.strip():
        raise BusinessException(ErrorCode.PARAMS_ERROR, "初始化 prompt 不能为空")

    gen_type = await route_code_gen_type(init_prompt)
    app = App(
        id=next_id(),
        app_name=init_prompt[:12],  # 应用名暂取 prompt 前 12 字
        init_prompt=init_prompt,
        code_gen_type=gen_type.value,
        user_id=login_user.id,
        priority=0,
        is_delete=0,
    )
    db.add(app)
    await db.commit()
    logger.info("应用创建成功，ID: %s, 类型: %s", app.id, gen_type.value)
    return app.id


async def get_by_id(db: AsyncSession, app_id: int) -> App | None:
    return await db.scalar(select(App).where(App.id == app_id, App.is_delete == 0))


async def get_app_vo(db: AsyncSession, app: App) -> AppVO:
    vo = AppVO(
        id=app.id,
        appName=app.app_name,
        cover=app.cover,
        initPrompt=app.init_prompt,
        codeGenType=app.code_gen_type,
        deployKey=app.deploy_key,
        deployedTime=app.deployed_time,
        priority=app.priority,
        userId=app.user_id,
        createTime=app.create_time,
        updateTime=app.update_time,
    )
    user = await db.scalar(select(User).where(User.id == app.user_id))
    vo.user = to_user_vo(user)
    return vo


async def get_app_vo_list(db: AsyncSession, apps: list[App]) -> list[AppVO]:
    """批量关联用户，一次查询解决 N+1。"""
    if not apps:
        return []
    user_ids = {a.user_id for a in apps}
    users = (await db.scalars(select(User).where(User.id.in_(user_ids)))).all()
    user_map = {u.id: to_user_vo(u) for u in users}
    result = []
    for app in apps:
        vo = AppVO(
            id=app.id,
            appName=app.app_name,
            cover=app.cover,
            initPrompt=app.init_prompt,
            codeGenType=app.code_gen_type,
            deployKey=app.deploy_key,
            deployedTime=app.deployed_time,
            priority=app.priority,
            userId=app.user_id,
            createTime=app.create_time,
            updateTime=app.update_time,
            user=user_map.get(app.user_id),
        )
        result.append(vo)
    return result


async def page_apps(db: AsyncSession, query: AppQueryRequest) -> tuple[list[App], int]:
    stmt = select(App).where(App.is_delete == 0)
    if query.id is not None:
        stmt = stmt.where(App.id == query.id)
    if query.appName:
        stmt = stmt.where(App.app_name.like(f"%{query.appName}%"))
    if query.initPrompt:
        stmt = stmt.where(App.init_prompt.like(f"%{query.initPrompt}%"))
    if query.codeGenType:
        stmt = stmt.where(App.code_gen_type == query.codeGenType)
    if query.deployKey:
        stmt = stmt.where(App.deploy_key == query.deployKey)
    if query.priority is not None:
        stmt = stmt.where(App.priority == query.priority)
    if query.userId is not None:
        stmt = stmt.where(App.user_id == query.userId)

    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    # 精选列表按优先级降序，其余按创建时间倒序
    order_col = App.priority.desc() if query.priority == GOOD_APP_PRIORITY else App.create_time.desc()
    stmt = stmt.order_by(order_col).offset((query.pageNum - 1) * query.pageSize).limit(query.pageSize)
    return list((await db.scalars(stmt)).all()), total


async def update_app(db: AsyncSession, app: App, **fields) -> bool:
    for column, value in fields.items():
        if value is not None:
            setattr(app, column, value)
    app.edit_time = datetime.now()
    await db.commit()
    return True


async def remove_by_id(db: AsyncSession, app_id: int) -> bool:
    app = await get_by_id(db, app_id)
    if app is None:
        return False
    app.is_delete = 1
    await db.commit()
    return True


def source_dir_of(app: App) -> Path:
    return CODE_OUTPUT_ROOT_DIR / f"{app.code_gen_type}_{app.id}"


async def deploy_app(db: AsyncSession, app: App) -> str:
    """把生成产物复制到部署目录，返回访问 URL。

    Vue 工程必须先构建，部署的是 dist 而非源码。
    """
    source_dir = source_dir_of(app)
    if not source_dir.is_dir():
        raise BusinessException(ErrorCode.SYSTEM_ERROR, "应用代码不存在，请先生成代码")

    if CodeGenType.from_value(app.code_gen_type) is CodeGenType.VUE_PROJECT:
        if not await build_project(source_dir):
            raise BusinessException(ErrorCode.SYSTEM_ERROR, "Vue 项目构建失败，请检查代码和依赖")
        dist_dir = source_dir / "dist"
        if not dist_dir.exists():
            raise BusinessException(ErrorCode.SYSTEM_ERROR, "Vue 项目构建完成但未生成 dist 目录")
        source_dir = dist_dir
        logger.info("Vue 项目构建成功，将部署 dist 目录: %s", dist_dir)

    deploy_key = app.deploy_key or "".join(
        random.choices(string.ascii_letters + string.digits, k=DEPLOY_KEY_LENGTH)
    )
    deploy_dir = CODE_DEPLOY_ROOT_DIR / deploy_key
    try:
        deploy_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_dir, deploy_dir, dirs_exist_ok=True)
    except OSError as e:
        raise BusinessException(ErrorCode.SYSTEM_ERROR, f"部署失败：{e}") from e

    app.deploy_key = deploy_key
    app.deployed_time = datetime.now()
    await db.commit()
    return f"{CODE_DEPLOY_HOST}/{deploy_key}/"
