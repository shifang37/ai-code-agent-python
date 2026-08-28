"""用户服务 —— 移植 service/impl/UserServiceImpl。

密码哈希沿用 Java 版的 `md5(SALT + password)`，SALT 固定为 "yupi"。
这套方案本身很弱（MD5 + 固定盐），但要和既有库里的密文兼容就只能保持一致；
换算法需要一次全量密码重置，属于独立的迁移任务。
"""

import hashlib

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import LoginUserVO, UserQueryRequest, UserVO
from app.core.paths import ADMIN_ROLE, DEFAULT_ROLE
from app.core.response import BusinessException, ErrorCode
from app.db.models import User
from app.utils.snowflake import next_id

SALT = "yupi"


def encrypt_password(raw: str) -> str:
    return hashlib.md5((SALT + raw).encode()).hexdigest()


def to_user_vo(user: User | None) -> UserVO | None:
    return UserVO.model_validate(user) if user else None


def to_login_user_vo(user: User | None) -> LoginUserVO | None:
    return LoginUserVO.model_validate(user) if user else None


async def register(db: AsyncSession, account: str, password: str, check_password: str) -> int:
    if not account or not password or not check_password:
        raise BusinessException(ErrorCode.PARAMS_ERROR, "参数为空")
    if len(account) < 4:
        raise BusinessException(ErrorCode.PARAMS_ERROR, "用户账号过短")
    if len(password) < 8 or len(check_password) < 8:
        raise BusinessException(ErrorCode.PARAMS_ERROR, "用户密码过短")
    if password != check_password:
        raise BusinessException(ErrorCode.PARAMS_ERROR, "两次输入的密码不一致")

    existing = await db.scalar(select(User).where(User.user_account == account, User.is_delete == 0))
    if existing:
        raise BusinessException(ErrorCode.PARAMS_ERROR, "账号重复")

    user = User(
        id=next_id(),
        user_account=account,
        user_password=encrypt_password(password),
        user_name="无名",
        user_role=DEFAULT_ROLE,
        is_delete=0,
    )
    db.add(user)
    await db.commit()
    return user.id


async def login(db: AsyncSession, account: str, password: str) -> User:
    if not account or not password:
        raise BusinessException(ErrorCode.PARAMS_ERROR, "参数为空")
    if len(account) < 4 or len(password) < 8:
        raise BusinessException(ErrorCode.PARAMS_ERROR, "账号或密码错误")

    user = await db.scalar(
        select(User).where(
            User.user_account == account,
            User.user_password == encrypt_password(password),
            User.is_delete == 0,
        )
    )
    if user is None:
        raise BusinessException(ErrorCode.PARAMS_ERROR, "用户不存在或密码错误")
    return user


async def get_by_id(db: AsyncSession, user_id: int) -> User | None:
    return await db.scalar(select(User).where(User.id == user_id, User.is_delete == 0))


async def add_user(db: AsyncSession, data) -> int:
    """管理员新增用户，默认密码 12345678（与 Java 版一致）。"""
    user = User(
        id=next_id(),
        user_account=data.userAccount,
        user_password=encrypt_password("12345678"),
        user_name=data.userName,
        user_avatar=data.userAvatar,
        user_profile=data.userProfile,
        user_role=data.userRole or DEFAULT_ROLE,
        is_delete=0,
    )
    db.add(user)
    await db.commit()
    return user.id


async def update_user(db: AsyncSession, data) -> bool:
    user = await get_by_id(db, data.id)
    if user is None:
        raise BusinessException(ErrorCode.NOT_FOUND_ERROR)
    for field, column in (
        ("userName", "user_name"),
        ("userAvatar", "user_avatar"),
        ("userProfile", "user_profile"),
        ("userRole", "user_role"),
    ):
        value = getattr(data, field)
        if value is not None:
            setattr(user, column, value)
    await db.commit()
    return True


async def remove_by_id(db: AsyncSession, user_id: int) -> bool:
    """逻辑删除，与 Java 版 @Column(isLogicDelete=true) 行为一致。"""
    user = await get_by_id(db, user_id)
    if user is None:
        return False
    user.is_delete = 1
    await db.commit()
    return True


async def page_users(db: AsyncSession, query: UserQueryRequest) -> tuple[list[User], int]:
    stmt = select(User).where(User.is_delete == 0)
    if query.id is not None:
        stmt = stmt.where(User.id == query.id)
    if query.userAccount:
        stmt = stmt.where(User.user_account.like(f"%{query.userAccount}%"))
    if query.userName:
        stmt = stmt.where(User.user_name.like(f"%{query.userName}%"))
    if query.userProfile:
        stmt = stmt.where(User.user_profile.like(f"%{query.userProfile}%"))
    if query.userRole:
        stmt = stmt.where(User.user_role == query.userRole)

    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = stmt.order_by(User.create_time.desc()).offset((query.pageNum - 1) * query.pageSize).limit(query.pageSize)
    records = list((await db.scalars(stmt)).all())
    return records, total


def is_admin(user: User) -> bool:
    return user.user_role == ADMIN_ROLE
