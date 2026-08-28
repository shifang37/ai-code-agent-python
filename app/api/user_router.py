"""用户路由 —— 对齐 Java 版 UserController。"""

import math

from fastapi import APIRouter, Response

from app.api.deps import (
    AdminUser,
    DbSession,
    LoginUser,
    clear_login_cookie,
    set_login_cookie,
)
from app.api.schemas import (
    DeleteRequest,
    UserAddRequest,
    UserLoginRequest,
    UserQueryRequest,
    UserRegisterRequest,
    UserUpdateRequest,
)
from app.core.response import BusinessException, ErrorCode, ok
from app.services import user_service

router = APIRouter(prefix="/user", tags=["user"])


@router.post("/register")
async def user_register(body: UserRegisterRequest, db: DbSession):
    user_id = await user_service.register(db, body.userAccount, body.userPassword, body.checkPassword)
    return ok(user_id)


@router.post("/login")
async def user_login(body: UserLoginRequest, db: DbSession, response: Response):
    user = await user_service.login(db, body.userAccount, body.userPassword)
    set_login_cookie(response, user.id)
    return ok(user_service.to_login_user_vo(user).model_dump(mode="json"))


@router.get("/get/login")
async def get_login_user(user: LoginUser):
    return ok(user_service.to_login_user_vo(user).model_dump(mode="json"))


@router.post("/logout")
async def user_logout(response: Response, user: LoginUser):
    clear_login_cookie(response)
    return ok(True)


@router.post("/add")
async def add_user(body: UserAddRequest, db: DbSession, _: AdminUser):
    return ok(await user_service.add_user(db, body))


@router.get("/get")
async def get_user_by_id(id: int, db: DbSession, _: AdminUser):
    user = await user_service.get_by_id(db, id)
    if user is None:
        raise BusinessException(ErrorCode.NOT_FOUND_ERROR)
    return ok(user_service.to_login_user_vo(user).model_dump(mode="json"))


@router.get("/get/vo")
async def get_user_vo_by_id(id: int, db: DbSession):
    user = await user_service.get_by_id(db, id)
    if user is None:
        raise BusinessException(ErrorCode.NOT_FOUND_ERROR)
    return ok(user_service.to_user_vo(user).model_dump(mode="json"))


@router.post("/delete")
async def delete_user(body: DeleteRequest, db: DbSession, _: AdminUser):
    if body.id <= 0:
        raise BusinessException(ErrorCode.PARAMS_ERROR)
    return ok(await user_service.remove_by_id(db, body.id))


@router.post("/update")
async def update_user(body: UserUpdateRequest, db: DbSession, _: AdminUser):
    return ok(await user_service.update_user(db, body))


@router.post("/list/page/vo")
async def list_user_vo_by_page(body: UserQueryRequest, db: DbSession, _: AdminUser):
    records, total = await user_service.page_users(db, body)
    return ok(
        {
            "records": [user_service.to_user_vo(u).model_dump(mode="json") for u in records],
            "pageNumber": body.pageNum,
            "pageSize": body.pageSize,
            "totalRow": total,
            "totalPage": math.ceil(total / body.pageSize) if body.pageSize else 0,
        }
    )
